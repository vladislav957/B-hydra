"""Комиссии: выбор транзакций майнером и вытеснение из полного мемпула.

Раньше комиссия не влияла НИ НА ЧТО. `mine_pending` брал транзакции в порядке
поступления, а полный мемпул отвечал `return False` всем подряд — то есть
заплатить за срочность было невозможно, а сеть вставала, как только кто-то
набивал пул копеечными транзакциями: перебить ставкой было нечем.

Теперь и то и другое считается по КОМИССИИ ЗА БАЙТ: место в блоке ограничено
байтами, поэтому транзакция с двадцатью входами и щедрой комиссией может быть
невыгоднее пяти маленьких.

⚠️ Самое тонкое здесь — связанные транзакции. В мемпуле лежат цепочки, где
потомок тратит неподтверждённую сдачу предка. Отсортируй их просто по
комиссии — и потомок окажется раньше предка, не пройдёт проверку по набору
UTXO и будет отброшен НАВСЕГДА. То есть «оптимизация» молча съедала бы
переводы. Это проверяется отдельно и подробно.
"""

import pytest

from b_hydra.node import BHydraNode
from b_hydra.transaction import Transaction, TransactionPool, TxInput, TxOutput
from b_hydra.wallet import generate_wallet


@pytest.fixture
def node():
    return BHydraNode(difficulty=1)


def _funded(node, blocks=1):
    """Кошелёк с деньгами. blocks=1 — ровно ОДИН выход, чтобы траты
    выстраивались в цепочку."""
    wallet = generate_wallet()
    for _ in range(blocks):
        node.mine_pending(wallet.address)
    return wallet


# --- Размер и ставка ----------------------------------------------------------
def test_transaction_knows_its_size():
    """Размер — это то, чем оплачивается место в блоке."""
    tx = Transaction(vin=[TxInput("ab" * 64, 0)],
                     vout=[TxOutput(1.0, "BHYxxxx")], timestamp=1.0)
    assert tx.size_bytes() > 0
    bigger = Transaction(vin=[TxInput("ab" * 64, 0)] * 5,
                         vout=[TxOutput(1.0, "BHYxxxx")] * 5, timestamp=1.0)
    assert bigger.size_bytes() > tx.size_bytes()


def test_fee_rate_is_per_byte(node):
    """Ставка считается на байт, а не по комиссии целиком."""
    wallet = _funded(node, blocks=2)
    small = node.create_transaction(wallet, generate_wallet().address, 1, 0.5)
    assert node.add_transaction(small)
    rate = node.mempool.fee_rate(small.txid)
    assert rate == pytest.approx(0.5 / small.size_bytes())


def test_unknown_fee_counts_as_zero():
    """Транзакция, добавленная без комиссии, — самый дешёвый груз.

    Так её и вытеснят первой, а не наоборот: неизвестное не должно случайно
    оказаться приоритетным.
    """
    pool = TransactionPool(max_size=10)
    tx = Transaction(vin=[TxInput("ab" * 64, 0)],
                     vout=[TxOutput(1.0, "BHYxxxx")], timestamp=1.0)
    assert pool.add(tx) is True
    assert pool.fee_rate(tx.txid) == 0.0


# --- Порядок сборки блока ------------------------------------------------------
def test_block_takes_the_richest_first(node):
    """Независимые транзакции идут в блок по убыванию ставки.

    Отправители РАЗНЫЕ намеренно: у одного кошелька траты выстраиваются в
    цепочку, и порядок задавали бы зависимости, а не комиссия.
    """
    senders = [_funded(node, blocks=2) for _ in range(3)]
    fees = (0.001, 5.0, 0.5)
    for wallet, fee in zip(senders, fees):
        assert node.add_transaction(
            node.create_transaction(wallet, generate_wallet().address, 1, fee))

    order = node._by_fee_rate(list(node.mempool.transactions))
    rates = [node.mempool.fee_rate(tx.txid) for tx in order]
    assert rates == sorted(rates, reverse=True)
    # И порядок поступления был ДРУГИМ — значит сортировка действительно нужна.
    arrived = [node.mempool.fee_rate(tx.txid)
               for tx in node.mempool.transactions]
    assert arrived != rates


def test_parent_goes_before_a_richer_child(node):
    """Предок раньше потомка, даже если потомок платит больше.

    Иначе потомок не найдёт своего входа в наборе UTXO, будет признан
    невалидным и ОТБРОШЕН НАВСЕГДА — сортировка по комиссии съела бы перевод.
    """
    wallet = _funded(node, blocks=1)               # ровно один выход
    parent = node.create_transaction(wallet, generate_wallet().address, 1, 0.0001)
    assert node.add_transaction(parent)
    child = node.create_transaction(wallet, generate_wallet().address, 1, 9.0)
    assert node.add_transaction(child)

    # Убеждаемся, что связь действительно есть, а не «повезло».
    assert any(inp.txid == parent.txid for inp in child.vin)
    assert node.mempool.fee_rate(child.txid) > node.mempool.fee_rate(parent.txid)

    order = [tx.txid for tx in node._by_fee_rate(list(node.mempool.transactions))]
    assert order.index(parent.txid) < order.index(child.txid)


def test_dependent_chain_survives_mining(node):
    """Сквозная проверка: обе связанные транзакции попадают в блок."""
    wallet = _funded(node, blocks=1)
    parent = node.create_transaction(wallet, generate_wallet().address, 1, 0.0001)
    node.add_transaction(parent)
    child = node.create_transaction(wallet, generate_wallet().address, 1, 9.0)
    node.add_transaction(child)

    block = node.mine_pending(generate_wallet().address)
    included = [tx["txid"] for tx in block.data[1:]]
    assert parent.txid in included and child.txid in included
    assert included.index(parent.txid) < included.index(child.txid)
    assert node.is_valid()


def test_ordering_keeps_every_transaction(node):
    """Переупорядочивание не теряет и не дублирует ничего."""
    senders = [_funded(node, blocks=2) for _ in range(4)]
    for i, wallet in enumerate(senders):
        node.add_transaction(
            node.create_transaction(wallet, generate_wallet().address, 1, 0.1 * (i + 1)))
    original = {tx.txid for tx in node.mempool.transactions}
    order = node._by_fee_rate(list(node.mempool.transactions))
    assert [tx.txid for tx in order] == list(dict.fromkeys(tx.txid for tx in order))
    assert {tx.txid for tx in order} == original


def test_miner_collects_the_fees(node):
    """Комиссии выбранных транзакций достаются майнеру — иначе платить не за что."""
    wallet = _funded(node, blocks=2)
    node.add_transaction(
        node.create_transaction(wallet, generate_wallet().address, 1, 2.5))
    miner = generate_wallet()
    block = node.mine_pending(miner.address)
    reward = node.blockchain.block_reward(block.index)
    assert node.get_balance(miner.address) == pytest.approx(reward + 2.5)


# --- Вытеснение из полного мемпула ---------------------------------------------
def test_full_mempool_evicts_the_cheapest(node):
    """Полный пул пускает дорогую транзакцию, выкинув самую дешёвую.

    Прежний глухой отказ означал, что сеть встаёт: набей пул копеечными
    транзакциями — и перебить ставкой нельзя вообще ничем.
    """
    node.mempool.max_size = 2
    senders = [_funded(node, blocks=2) for _ in range(3)]
    cheap = [node.create_transaction(w, generate_wallet().address, 1, 0.0001)
             for w in senders[:2]]
    for tx in cheap:
        assert node.add_transaction(tx)
    assert len(node.mempool) == 2

    rich = node.create_transaction(senders[2], generate_wallet().address, 1, 10.0)
    assert node.add_transaction(rich) is True
    assert len(node.mempool) == 2
    assert node.mempool.get(rich.txid) is not None
    # Выкинули именно дешёвую, а не случайную.
    assert sum(1 for tx in cheap if node.mempool.get(tx.txid) is not None) == 1


def test_full_mempool_refuses_something_cheaper(node):
    """Дешевле того, что уже лежит, — отказ.

    Иначе полный пул можно было бы бесконечно перемешивать, ничего не платя.
    """
    node.mempool.max_size = 1
    rich_sender = _funded(node, blocks=2)
    poor_sender = _funded(node, blocks=2)
    rich = node.create_transaction(rich_sender, generate_wallet().address, 1, 5.0)
    assert node.add_transaction(rich)
    poor = node.create_transaction(poor_sender, generate_wallet().address, 1, 0.0001)
    assert node.add_transaction(poor) is False
    assert node.mempool.get(rich.txid) is not None


def test_equal_fee_does_not_displace(node):
    """Равная ставка не вытесняет: без строгого «дороже» пул можно было бы
    крутить по кругу задаром."""
    pool = TransactionPool(max_size=1)
    first = Transaction(vin=[TxInput("ab" * 64, 0)],
                        vout=[TxOutput(1.0, "BHYaaa")], timestamp=1.0)
    second = Transaction(vin=[TxInput("cd" * 64, 0)],
                         vout=[TxOutput(1.0, "BHYbbb")], timestamp=2.0)
    assert pool.add(first, fee=1.0) is True
    assert pool.add(second, fee=1.0) is False
    assert pool.get(first.txid) is not None


def test_removing_forgets_the_rate():
    """Ставка не должна пережить саму транзакцию — иначе пул медленно
    заполнялся бы мусором из чисел."""
    pool = TransactionPool(max_size=5)
    tx = Transaction(vin=[TxInput("ab" * 64, 0)],
                     vout=[TxOutput(1.0, "BHYxxxx")], timestamp=1.0)
    pool.add(tx, fee=1.0)
    assert pool.fee_rate(tx.txid) > 0
    assert pool.remove(tx.txid) is True
    assert pool.fee_rate(tx.txid) == 0.0
    assert pool.remove(tx.txid) is False


def test_replacing_the_list_drops_stale_rates():
    """Прямое присваивание `transactions` тоже чистит ставки."""
    pool = TransactionPool(max_size=5)
    tx = Transaction(vin=[TxInput("ab" * 64, 0)],
                     vout=[TxOutput(1.0, "BHYxxxx")], timestamp=1.0)
    pool.add(tx, fee=1.0)
    pool.transactions = []
    assert pool.fee_rate(tx.txid) == 0.0
    assert pool._rates == {}


def test_cheapest_is_none_on_an_empty_pool():
    assert TransactionPool(max_size=5).cheapest() is None
