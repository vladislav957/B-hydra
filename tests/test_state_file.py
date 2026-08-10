"""Файл состояния узла: атомарная запись и восстановление после обрыва.

⚠️ Это тесты на настоящую поломку у пользователя. Клиент падал при КАЖДОМ
запуске с `JSONDecodeError: Expecting ',' delimiter: line 3992 column 153
(char 185473)` — окно не открывалось вовсе, а починить это из интерфейса было
нечем.

Причина: `save()` открывал целевой файл на "w", то есть обрезал его сразу, и
только потом писал. Любой обрыв посреди записи — снятие процесса, закрытая
крышка, пропажа питания — оставлял наполовину записанный JSON. Дальше `load()`
падал без обработки, и приложение больше не запускалось никогда.

Здесь проверяется обе половины починки: запись, которую нельзя застать
наполовину, и чтение, переживающее уже испорченный файл.
"""

import json
import os

import pytest

from b_hydra.node import BHydraNode
from b_hydra.wallet import generate_wallet



@pytest.fixture(scope="module")
def filled_node():
    """Узел с несколькими блоками и переводами — не пустой генезис."""
    node = BHydraNode(difficulty=2)
    miner, other = generate_wallet(), generate_wallet()
    for _ in range(5):
        node.mine_pending(miner.address)
        tx = node.create_transaction(miner, other.address, 0.5, fee=0.01)
        if tx:
            node.add_transaction(tx)
    return node


# --- Запись --------------------------------------------------------------------
def test_save_and_load_roundtrip(tmp_path, filled_node):
    path = str(tmp_path / "chain.json")
    filled_node.save(path)
    restored = BHydraNode.load(path)
    assert len(restored.blockchain.chain) == len(filled_node.blockchain.chain)
    assert restored.blockchain.chain[-1].hash == filled_node.blockchain.chain[-1].hash
    assert restored.blockchain.is_chain_valid()


def test_save_leaves_no_temporary_file(tmp_path, filled_node):
    """После записи рядом не должно остаться `.tmp` — иначе он копится."""
    path = str(tmp_path / "chain.json")
    filled_node.save(path)
    assert os.path.exists(path)
    assert not os.path.exists(path + ".tmp")
    assert sorted(os.listdir(tmp_path)) == ["chain.json"]


def test_interrupted_save_keeps_the_previous_file(tmp_path, filled_node,
                                                  monkeypatch):
    """Обрыв ПОСРЕДИ записи не должен портить уже лежащий файл.

    Это главный тест здесь: ровно так пользователь и остался без кошелька.
    Прежний код обрезал целевой файл первым же действием, поэтому после
    падения на диске лежал огрызок.
    """
    path = str(tmp_path / "chain.json")
    filled_node.save(path)                      # первая, удачная запись
    before = open(path, encoding="utf-8").read()

    def explode(*args, **kwargs):
        raise KeyboardInterrupt("питание пропало посреди записи")

    monkeypatch.setattr(json, "dump", explode)
    with pytest.raises(KeyboardInterrupt):
        filled_node.save(path)

    after = open(path, encoding="utf-8").read()
    assert after == before, "старый файл состояния пострадал от обрыва"
    assert json.loads(after)                     # и он по-прежнему читается


def test_save_overwrites_atomically(tmp_path, filled_node):
    """Повторная запись поверх существующего файла проходит и даёт валидный JSON."""
    path = str(tmp_path / "chain.json")
    filled_node.save(path)
    filled_node.save(path)
    assert BHydraNode.load(path).blockchain.is_chain_valid()


# --- Восстановление ------------------------------------------------------------
@pytest.mark.parametrize("fraction", [0.30, 0.55, 0.80, 0.95, 0.999])
def test_recover_salvages_the_intact_prefix(tmp_path, filled_node, fraction):
    """Оборванный файл читается до места разрыва, а не выбрасывается целиком.

    Владелец теряет последние блоки, а не весь кошелёк. Обрыв проверяется в
    разных местах: у границы блока и посреди него.
    """
    whole = str(tmp_path / "whole.json")
    filled_node.save(whole)
    text = open(whole, encoding="utf-8").read()

    broken = str(tmp_path / f"broken-{int(fraction * 1000)}.json")
    with open(broken, "w", encoding="utf-8") as fh:
        fh.write(text[:int(len(text) * fraction)])

    with pytest.raises(ValueError):              # обычная загрузка обязана упасть
        BHydraNode.load(broken)

    node = BHydraNode.recover(broken)
    assert node.blockchain.is_chain_valid(), "спасённая цепочка обязана быть валидной"
    assert 1 <= len(node.blockchain.chain) <= len(filled_node.blockchain.chain)
    # Спасённые блоки совпадают с исходными побитово — это не «похожая» цепочка.
    for saved, original in zip(node.blockchain.chain, filled_node.blockchain.chain):
        assert saved.hash == original.hash


def test_recovered_chain_grows_with_how_much_survived(tmp_path, filled_node):
    """Чем больше уцелело файла, тем больше блоков спасается."""
    whole = str(tmp_path / "whole.json")
    filled_node.save(whole)
    text = open(whole, encoding="utf-8").read()

    heights = []
    for fraction in (0.4, 0.7, 0.95):
        broken = str(tmp_path / f"b{int(fraction * 100)}.json")
        with open(broken, "w", encoding="utf-8") as fh:
            fh.write(text[:int(len(text) * fraction)])
        heights.append(len(BHydraNode.recover(broken).blockchain.chain))
    assert heights == sorted(heights), heights


def test_recover_refuses_a_file_without_blocks(tmp_path):
    """Спасать нечего — честная ошибка, а не пустая цепочка."""
    path = str(tmp_path / "junk.json")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write('{"difficulty": 4, "chai')
    with pytest.raises(ValueError):
        BHydraNode.recover(path)


def test_recover_refuses_complete_garbage(tmp_path):
    path = str(tmp_path / "junk.json")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("это вообще не JSON")
    with pytest.raises(ValueError):
        BHydraNode.recover(path)


def test_recover_keeps_the_difficulty(tmp_path, filled_node):
    """Сложность берётся из уцелевшего заголовка, а не подставляется наугад."""
    whole = str(tmp_path / "whole.json")
    filled_node.save(whole)
    text = open(whole, encoding="utf-8").read()
    broken = str(tmp_path / "broken.json")
    with open(broken, "w", encoding="utf-8") as fh:
        fh.write(text[:int(len(text) * 0.6)])
    assert BHydraNode.recover(broken).blockchain.difficulty == \
        filled_node.blockchain.difficulty


def test_recover_works_on_an_intact_file(tmp_path, filled_node):
    """На целом файле восстановление обязано дать ровно ту же цепочку."""
    path = str(tmp_path / "chain.json")
    filled_node.save(path)
    assert len(BHydraNode.recover(path).blockchain.chain) == \
        len(filled_node.blockchain.chain)


def test_a_truncated_block_is_not_accepted(tmp_path, filled_node):
    """Разрыв ПОСРЕДИ блока не должен давать полублок в цепочке.

    `raw_decode` возвращает только целые объекты, а хвост дополнительно
    отрезается до прохождения `is_chain_valid()` — блок с недочитанными
    транзакциями попасть в цепочку не может.
    """
    whole = str(tmp_path / "whole.json")
    filled_node.save(whole)
    text = open(whole, encoding="utf-8").read()
    for cut in range(int(len(text) * 0.5), int(len(text) * 0.9), 137):
        broken = str(tmp_path / "b.json")
        with open(broken, "w", encoding="utf-8") as fh:
            fh.write(text[:cut])
        try:
            node = BHydraNode.recover(broken)
        except ValueError:
            continue
        assert node.blockchain.is_chain_valid(), f"обрыв на {cut}"


# --- Запуск клиента ------------------------------------------------------------
def test_startup_survives_a_corrupt_state_file(tmp_path, filled_node):
    """Битый файл не должен мешать запуску.

    Раньше `BHydraNode.load` звался НАПРЯМУЮ из клиента, REST-сервера и CLI, и
    все трое падали ДО открытия окна — насовсем.
    """
    whole = str(tmp_path / "whole.json")
    filled_node.save(whole)
    text = open(whole, encoding="utf-8").read()

    state = tmp_path / "bhydra_chain.json"
    state.write_text(text[: int(len(text) * 0.7)], encoding="utf-8")

    node, notice = BHydraNode.open_state(str(state))
    assert node.blockchain.is_chain_valid()
    assert notice and "восстанов" in notice.lower()
    # Битый файл отложен, а не удалён: решать его судьбу вправе владелец.
    assert any(name.startswith("bhydra_chain.json.corrupt-")
               for name in os.listdir(tmp_path)), os.listdir(tmp_path)
    # И на месте снова лежит целый файл — следующий запуск пройдёт молча.
    assert BHydraNode.load(str(state)).blockchain.is_chain_valid()


def test_startup_falls_back_when_nothing_can_be_saved(tmp_path):
    """Если спасать нечего — пустая цепочка и внятное объяснение, но не падение."""
    state = tmp_path / "bhydra_chain.json"
    state.write_text("{вообще не json", encoding="utf-8")

    node, notice = BHydraNode.open_state(str(state))
    assert len(node.blockchain.chain) == 1        # только генезис
    assert notice and "повреждён" in notice.lower()


def test_startup_without_a_state_file_is_silent(tmp_path):
    """Первый запуск — никаких предупреждений."""
    node, notice = BHydraNode.open_state(str(tmp_path / "bhydra_chain.json"))
    assert notice is None
    assert len(node.blockchain.chain) == 1
