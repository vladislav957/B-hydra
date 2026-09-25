"""Генезис-ЗАГЛУШКА: узел не должен падать на её пустых полях.

⚠️ ЭТО НЕ ВЫДУМАННЫЙ СЛУЧАЙ, А ЖИВАЯ ЦЕПОЧКА. В сети B-hydra генезис — не
настоящая coinbase, а заглушка: у его «транзакции» пустые `txid`, `timestamp`,
`vin.signature`, `vout.amount` и `vout.address`. На цепочке в 1011 блоков
вкладка «Адреса» падала при первом же открытии:

    TypeError: unsupported operand type(s) for +=: 'float' and 'NoneType'

⚠️ ЧИНИТСЯ КОД, А НЕ ДАННЫЕ. Хеш генезиса входит в отпечаток сети
(`p2p.network_id`) и в `previous_hash` первого блока: поправь блок — и цепочка
перестанет быть собой, а узел не сойдётся ни с кем. Поэтому пустые выходы
ОТСЕИВАЮТСЯ при чтении, и это не обход ошибки: из адреса `null` нельзя
потратить, а `null` BHY — не сумма.
"""

import pytest

from b_hydra.node import BHydraNode
from b_hydra.wallet import generate_wallet

#: Ровно та заглушка, что лежит в живой цепочке.
ЗАГЛУШКА = {
    "txid": None,
    "timestamp": None,
    "vin": [{"txid": None, "index": 0, "signature": None, "public_key": None}],
    "vout": [{"amount": None, "address": None}],
}


def цепочка(блоков=4, заглушка=None):
    """Узел с добытыми блоками и генезисом-заглушкой вместо настоящего."""
    node = BHydraNode(difficulty=1)
    майнер = generate_wallet()
    for _ in range(блоков):
        node.mine_pending(майнер.address)
    node.blockchain.chain[0].data = [
        dict(ЗАГЛУШКА) if заглушка is None else заглушка]
    # Кэши собирались до подмены — заставляем пересобрать.
    node._utxo_height = 0
    node._utxo = {}
    return node, майнер


# --- То, что падало ------------------------------------------------------------
def test_address_stats_survives_a_stub_genesis():
    """⚠️ ГЛАВНЫЙ ТЕСТ: именно здесь падала вкладка «Адреса»."""
    node, _ = цепочка()
    статы = node.address_stats()          # раньше: TypeError на float += None
    assert статы


def test_the_emission_is_counted_without_the_genesis():
    """Заглушка НИЧЕГО не создаёт: сумма балансов = только награды за блоки.

    Посчитай мы её выход как монету — эмиссия разошлась бы с экономикой, а
    обозреватель показывал бы деньги, которых нет.
    """
    блоков = 4
    node, _ = цепочка(блоков)
    статы = node.address_stats()
    ожидается = sum(node.blockchain.block_reward(h) for h in range(1, блоков + 1))
    assert sum(s["balance"] for s in статы) == pytest.approx(ожидается)


def test_the_empty_address_never_shows_up_in_the_rich_list():
    """Адреса `null` в списке быть не должно — это не адрес."""
    node, _ = цепочка()
    адреса = [s["address"] for s in node.address_stats()]
    assert None not in адреса
    assert all(a and str(a).startswith("BHY") for a in адреса)


def test_address_history_survives_it_too():
    """⚠️ Найдено обходом ВСЕХ путей узла, а не по отчёту.

    `address_history` падал тем же `int + NoneType`. Через REST это не
    стреляло по случайности — адрес оттуда всегда строка, а `None == "BHY…"`
    ложно. Заглушка с настоящим адресом (ниже) роняла бы и этот путь.
    """
    node, майнер = цепочка()
    assert node.address_history(майнер.address)
    assert node.address_history(None) == []


def test_a_stub_with_a_real_address_but_no_amount_is_also_safe():
    """⚠️ Условие на СУММУ нужно отдельно от условия на адрес.

    Проверь мы только адрес — заглушка с настоящим адресом и пустой суммой
    прошла бы фильтр и уронила подсчёт.
    """
    майнер = generate_wallet()
    node = BHydraNode(difficulty=1)
    for _ in range(2):
        node.mine_pending(майнер.address)
    node.blockchain.chain[0].data = [{
        "txid": None, "timestamp": None,
        "vin": [{"txid": None, "index": 0}],
        "vout": [{"amount": None, "address": майнер.address}]}]
    node._utxo_height = 0
    node._utxo = {}

    assert node.address_stats()
    assert node.address_history(майнер.address)


# --- Остальные пути узла -------------------------------------------------------
@pytest.mark.parametrize("имя", [
    "utxo_set", "total_supply", "is_valid", "mempool_info",
])
def test_the_rest_of_the_node_does_not_choke(имя):
    """Обход остальных путей: ни один не должен спотыкаться о заглушку."""
    node, _ = цепочка()
    цель = {"utxo_set": node.utxo_set,
            "total_supply": lambda: node.blockchain.total_supply,
            "is_valid": node.is_valid,
            "mempool_info": node.mempool_info}[имя]
    цель()


def test_the_empty_output_never_enters_the_utxo_set():
    """⚠️ Пустой выход не должен стать «непотраченной монетой».

    Попади он в набор — его увидели бы баланс, rich list и попытка траты, и
    сеть отвергла бы транзакцию, ссылающуюся на несуществующий выход.
    """
    node, _ = цепочка()
    набор = node.utxo_set()
    assert набор, "набор UTXO пуст — тест бессмыслен"
    for (txid, _index), выход in набор.items():
        assert txid is not None
        assert выход.get("address") is not None
        assert выход.get("amount") is not None


def test_the_index_does_not_answer_for_a_missing_txid():
    """Поиск транзакции по пустому txid — ответ «нет», а не находка."""
    node, _ = цепочка()
    assert node.find_transaction(None) is None
    assert node.merkle_proof(None) is None


# --- Данные остаются нетронутыми ------------------------------------------------
def test_the_genesis_block_itself_is_not_modified():
    """⚠️ САМОЕ ВАЖНОЕ ОГРАНИЧЕНИЕ: правим чтение, а не цепочку.

    Хеш генезиса — отпечаток сети и `previous_hash` первого блока. Почини мы
    «данные», и узел перестал бы сходиться с самим собой, не говоря о сети.
    """
    node, _ = цепочка()
    хеш_до = node.blockchain.chain[0].hash
    данные_до = node.blockchain.chain[0].data

    node.address_stats()
    node.address_history(None)
    node.utxo_set()

    assert node.blockchain.chain[0].hash == хеш_до
    assert node.blockchain.chain[0].data == данные_до
    assert node.blockchain.chain[1].previous_hash == хеш_до
