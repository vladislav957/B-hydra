"""Фоновое обслуживание узла: сам ищет соседей и сам догоняет цепочку.

⚠️ Зачем это нужно. Анонсы приносят только то, что произошло ПРИ НАС: сосед
рассылает `inv` в момент, когда добыл блок, и никто не станет пересылать старые
блоки по собственной воле. Узел, который стоял выключенным или терял связь, без
обслуживания так и оставался бы на своей высоте — навсегда.

Раньше цикл жил в `api.py`, поэтому узел, запущенный через `P2P.py`, обслуживал
себя ТОЛЬКО реактивно: поведение зависело от способа запуска. Теперь цикл один
и живёт в самом узле, а точки входа его лишь включают.

⚠️ По умолчанию обслуживание ВЫКЛЮЧЕНО. В тестах десятки узлов, и фоновые
сетевые вызовы у каждого сделали бы всю сюиту плавающей. Включают его точки
входа — и вот это здесь проверяется явно.
"""

import time

import pytest

from b_hydra.node import BHydraNode
from b_hydra.p2p import MAINTENANCE_INTERVAL, P2PNode
from b_hydra.wallet import generate_wallet

PORT = 5900


def _port():
    global PORT
    PORT += 1
    return PORT


def _wait_until(check, timeout=15.0, step=0.05):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if check():
            return True
        time.sleep(step)
    return False


@pytest.fixture
def stopper():
    """Гарантированно гасит поднятые узлы, даже если тест упал."""
    started = []
    yield started.append
    for node in started:
        try:
            node.stop()
        except Exception:
            pass


# --- Включение и выключение ----------------------------------------------------
def test_maintenance_is_off_by_default(stopper):
    """Без явного интервала фонового потока быть не должно.

    Это защита сюиты: 54 узла в тестах, и у каждого свой сетевой цикл сделал бы
    проверки высот и списков пиров плавающими.
    """
    node = P2PNode("127.0.0.1", _port(), BHydraNode(difficulty=1))
    stopper(node)
    node.start()
    assert node.maintenance_interval == 0
    assert node._maintenance_thread is None


def test_maintenance_starts_when_asked(stopper):
    node = P2PNode("127.0.0.1", _port(), BHydraNode(difficulty=1),
                   maintenance_interval=0.05)
    stopper(node)
    node.start()
    assert node._maintenance_thread is not None
    assert node._maintenance_thread.is_alive()


def test_start_maintenance_refuses_without_an_interval(stopper):
    node = P2PNode("127.0.0.1", _port(), BHydraNode(difficulty=1))
    stopper(node)
    assert node.start_maintenance() is False
    assert node.start_maintenance(0) is False
    assert node.start_maintenance(0.05) is True


def test_start_maintenance_is_not_started_twice(stopper):
    """Повторный вызов не должен плодить потоки — иначе их накопится сколько
    угодно, и каждый будет ходить по сети."""
    node = P2PNode("127.0.0.1", _port(), BHydraNode(difficulty=1))
    stopper(node)
    assert node.start_maintenance(0.05) is True
    first = node._maintenance_thread
    assert node.start_maintenance(0.05) is False
    assert node._maintenance_thread is first


def test_stop_wakes_maintenance_immediately(stopper):
    """⚠️ `stop()` обязан будить цикл, а не ждать конца интервала.

    Иначе «остановленный» узел ещё целый интервал ходил бы по сети — при
    боевых 15 секундах это заметно.
    """
    node = P2PNode("127.0.0.1", _port(), BHydraNode(difficulty=1),
                   maintenance_interval=30.0)      # заведомо дольше теста
    node.start()
    thread = node._maintenance_thread
    assert thread.is_alive()
    started = time.monotonic()
    node.stop()
    thread.join(timeout=5.0)
    assert not thread.is_alive(), "цикл не проснулся на stop()"
    assert time.monotonic() - started < 5.0


# --- Что цикл делает -----------------------------------------------------------
def test_a_node_catches_up_without_being_told(stopper):
    """ГЛАВНОЕ: узел догоняет цепочку сам, без единого вызова снаружи.

    Сосед добывает блоки, пока наш узел ещё не знаком с ним по анонсам —
    ровно как если бы наш узел стоял выключенным. Никто ему эти блоки не
    перешлёт: догнать он обязан сам.
    """
    miner = generate_wallet()
    ahead = P2PNode("127.0.0.1", _port(), BHydraNode(difficulty=1))
    stopper(ahead)
    ahead.start()
    for _ in range(3):
        ahead.node.mine_pending(miner.address)
    target = ahead.node.height

    behind = P2PNode("127.0.0.1", _port(), BHydraNode(difficulty=1),
                     maintenance_interval=0.1)
    stopper(behind)
    behind.add_peer("127.0.0.1", ahead.port)   # адрес знает, цепочку — нет
    assert behind.node.height < target
    behind.start()                              # и больше ничего не вызываем

    assert _wait_until(lambda: behind.node.height == target), \
        f"не догнал сам: {behind.node.height} вместо {target}"
    assert behind.node.blockchain.is_chain_valid()


def test_maintenance_survives_a_dead_peer(stopper):
    """Упавший сосед не должен ронять цикл — он обязан жить дальше.

    Сеть отваливается штатно: адрес в таблице есть, а узла там уже нет.
    """
    node = P2PNode("127.0.0.1", _port(), BHydraNode(difficulty=1),
                   maintenance_interval=0.05)
    stopper(node)
    node.add_peer("127.0.0.1", 1)        # там заведомо никого нет
    node.start()
    time.sleep(0.4)                       # несколько оборотов цикла
    assert node._maintenance_thread.is_alive(), "цикл умер на недоступном пире"


def test_a_dead_peer_is_not_dropped_from_the_table(stopper):
    """⚠️ Молчащий сосед из таблицы НЕ выкидывается.

    Перезагрузка соседа на пару минут не должна стоить нам записи о нём:
    `_fanout` просто пропускает недоступных. Убираются только те, кто ответил
    из ЧУЖОЙ сети.
    """
    node = P2PNode("127.0.0.1", _port(), BHydraNode(difficulty=1),
                   maintenance_interval=0.05)
    stopper(node)
    node.add_peer("127.0.0.1", 1)
    node.start()
    time.sleep(0.4)
    assert ("127.0.0.1", 1) in node.peer_list(), "живой адрес выкинут за молчание"


def test_a_neighbour_that_returns_is_caught_up_with(stopper):
    """Сосед пропал и вернулся — узел обязан догнать его без вмешательства."""
    miner = generate_wallet()
    port = _port()
    peer = P2PNode("127.0.0.1", port, BHydraNode(difficulty=1))
    peer.start()
    peer.node.mine_pending(miner.address)
    first = peer.node.height

    node = P2PNode("127.0.0.1", _port(), BHydraNode(difficulty=1),
                   maintenance_interval=0.1)
    stopper(node)
    node.add_peer("127.0.0.1", port)
    node.start()
    assert _wait_until(lambda: node.node.height == first)

    peer.stop()                            # сосед «отвалился»
    time.sleep(0.3)                        # цикл крутится вхолостую
    assert node._maintenance_thread.is_alive()
    assert ("127.0.0.1", port) in node.peer_list()

    # ⚠️ Тот же УЗЕЛ, а не другой на том же адресе: ключ переиспользуется.
    # Подними мы здесь узел со свежим ключом — соединение было бы законно
    # отвергнуто закреплением (TOFU), и это правильно: для нас это выглядит
    # как подмена узла, а не как возвращение соседа.
    revived = P2PNode("127.0.0.1", port, BHydraNode(difficulty=1),
                      identity=peer.identity)
    stopper(revived)
    revived.start()
    for _ in range(3):
        revived.node.mine_pending(miner.address)
    target = revived.node.height

    assert _wait_until(lambda: node.node.height == target), \
        f"не догнал вернувшегося соседа: {node.node.height} вместо {target}"


# --- Точки входа ---------------------------------------------------------------
def test_both_entry_points_use_the_same_interval():
    """⚠️ Цикл один и настройка одна: поведение не зависит от способа запуска.

    Раньше цикл жил в `api.py` с зашитым `interval=15`, а `P2P.py` не имел его
    вовсе. Теперь оба зовут `start_maintenance(MAINTENANCE_INTERVAL)`.
    """
    import inspect

    from b_hydra import api, p2p

    assert MAINTENANCE_INTERVAL > 0
    for module in (api, p2p):
        source = inspect.getsource(module)
        assert "start_maintenance(MAINTENANCE_INTERVAL)" in source, module.__name__
    # И дубля цикла в api.py больше нет.
    assert "def _maintain(interval" not in inspect.getsource(api)


def test_a_stranger_on_a_known_address_is_refused(stopper):
    """⚠️ Вернуться должен ТОТ ЖЕ узел, а не любой на том же адресе.

    Свойство найдено при написании теста выше: узел со свежим ключом на
    знакомом адресе цепочку нам не навязал. Так и должно быть — закрепление
    ключа (TOFU, как в SSH) считает это подменой узла, а не возвращением
    соседа. Иначе достаточно было бы занять освободившийся адрес, чтобы
    подсунуть нам свою цепочку.
    """
    miner = generate_wallet()
    port = _port()
    peer = P2PNode("127.0.0.1", port, BHydraNode(difficulty=1))
    peer.start()
    peer.node.mine_pending(miner.address)

    node = P2PNode("127.0.0.1", _port(), BHydraNode(difficulty=1),
                   maintenance_interval=0.1)
    stopper(node)
    node.add_peer("127.0.0.1", port)
    node.start()
    assert _wait_until(lambda: node.node.height == peer.node.height)
    settled = node.node.height
    peer.stop()

    # Тот же адрес, но ДРУГОЙ узел: своего ключа мы ему не закрепляли.
    stranger = P2PNode("127.0.0.1", port, BHydraNode(difficulty=1))
    stopper(stranger)
    stranger.start()
    for _ in range(4):
        stranger.node.mine_pending(generate_wallet().address)
    assert stranger.node.height > settled

    time.sleep(1.0)                        # несколько оборотов обслуживания
    assert node.node.height == settled, \
        "чужая цепочка принята с закреплённого адреса"
    assert node._maintenance_thread.is_alive()
