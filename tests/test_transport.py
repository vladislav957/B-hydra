"""Транспорт: сеть B-hydra поверх чего угодно, а не только TCP/IP.

Протокол выше сокета про транспорт ничего не знает: `tcp.py` пользуется
`sendall`/`recv`, `secure.py` — ими же плюс таймаутами. Значит, чтобы пустить
сеть по Bluetooth (RFCOMM — такой же байтовый поток), переписывать протокол не
нужно, достаточно подменить объект транспорта.

Проверяется это не рассуждением, а делом: те же тесты гоняются на
`PairTransport`, где нет ни IP, ни портов ОС, ни маршрутизации — только пары
`socket.socketpair()` внутри процесса. Если поверх него работают рукопожатие,
шифрование, gossip, майнинг и синхронизация — абстракция настоящая.

⚠️ Bluetooth здесь НЕ проверяется и проверен быть не может: в контейнере нет
адаптера (`AF_BLUETOOTH` даёт «Address family not supported»). Проверяется
ровно то, что радиоканал можно будет подставить, ничего не ломая.
"""

import socket
import threading
import time

import pytest

from b_hydra.node import BHydraNode
from b_hydra.p2p import P2PNode
from b_hydra.transport import PairTransport, TCPTransport, Transport
from b_hydra.wallet import generate_wallet


def _wait_until(check, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if check():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def pair_net():
    """Два узла в сети БЕЗ IP: общий PairTransport, адреса выдуманные."""
    wire = PairTransport()
    a = P2PNode("узел-А", 1, node=BHydraNode(difficulty=1), transport=wire)
    b = P2PNode("узел-Б", 2, node=BHydraNode(difficulty=1), transport=wire)
    a.start()
    b.start()
    assert _wait_until(lambda: a._running and b._running)
    try:
        yield a, b
    finally:
        a.stop()
        b.stop()


# --- Интерфейс ----------------------------------------------------------------
def test_default_transport_is_tcp():
    """Умолчание не изменилось: кто ничего не просил, получает TCP/IP."""
    node = P2PNode("127.0.0.1", 5000, node=BHydraNode(difficulty=1))
    assert isinstance(node.transport, TCPTransport)
    assert node.transport.name == "tcp"


def test_base_transport_refuses_to_pretend():
    """Заготовка не должна молча «работать» — иначе узел поднимется и будет
    тихо ничего не принимать."""
    base = Transport()
    for call in (lambda: base.listen(1),
                 lambda: base.accept(None),
                 lambda: base.connect("h", 1, 1)):
        with pytest.raises(NotImplementedError):
            call()


def test_pair_transport_has_no_ip_at_all():
    """Проверяем, что это действительно не сеть: сокеты — AF_UNIX."""
    wire = PairTransport()
    server = wire.listen(7)
    try:
        client = wire.connect("кто-нибудь", 7, timeout=2)
        conn, host = wire.accept(server)
        assert client.family == socket.AF_UNIX
        assert conn.family == socket.AF_UNIX
        assert host == "кто-нибудь"        # «хост» здесь просто метка
        client.sendall("привет".encode("utf-8"))
        assert conn.recv(16) == "привет".encode("utf-8")
        client.close()
        conn.close()
    finally:
        server.close()


def test_pair_transport_refuses_a_busy_port():
    wire = PairTransport()
    server = wire.listen(9)
    try:
        with pytest.raises(OSError):
            wire.listen(9)
    finally:
        server.close()


def test_pair_transport_refuses_when_nobody_listens():
    with pytest.raises(OSError):
        PairTransport().connect("никого", 42, timeout=1)


def test_closing_the_listener_wakes_accept():
    """Иначе `stop()` оставлял бы поток приёма висеть навсегда."""
    wire = PairTransport()
    server = wire.listen(11)
    failed = []

    def wait():
        try:
            wire.accept(server)
        except OSError:
            failed.append(True)

    thread = threading.Thread(target=wait, daemon=True)
    thread.start()
    time.sleep(0.05)
    server.close()
    thread.join(timeout=3)
    assert failed == [True]
    assert not thread.is_alive()


# --- Настоящая сеть без IP ----------------------------------------------------
def test_nodes_meet_over_a_non_ip_transport(pair_net):
    """Знакомство и обмен пирами работают поверх socketpair."""
    a, b = pair_net
    assert b.connect("узел-А", 1) is True
    assert ("узел-А", 1) in b.peer_list()
    assert _wait_until(lambda: ("узел-Б", 2) in a.peer_list())


def test_channel_is_still_encrypted_without_ip(pair_net):
    """Шифрование — свойство протокола, а не TCP.

    Если бы рукопожатие цеплялось за сокет TCP, на другом транспорте оно
    молча отвалилось бы в открытый текст — то самое понижение, которого в
    проекте быть не должно.
    """
    a, b = pair_net
    assert a.encrypt and b.encrypt
    assert b.connect("узел-А", 1) is True
    # Ключ узла закреплён (TOFU) — значит, рукопожатие реально состоялось.
    assert b.pinned_key("узел-А", 1)


def test_blocks_travel_over_a_non_ip_transport(pair_net):
    """Майнинг, анонс и докачка блока — весь путь целиком."""
    a, b = pair_net
    assert b.connect("узел-А", 1) is True
    before = b.node.height
    a.mine(generate_wallet().address)
    assert _wait_until(lambda: b.node.height > before)
    assert b.node.blockchain.last_block.hash == a.node.blockchain.last_block.hash


def test_transactions_travel_over_a_non_ip_transport(pair_net):
    a, b = pair_net
    assert b.connect("узел-А", 1) is True
    sender = generate_wallet()
    a.mine(sender.address)
    assert _wait_until(lambda: b.node.height == a.node.height)

    tx = a.node.create_transaction(sender, generate_wallet().address, 1, 0.1)
    assert a.submit_transaction(tx) is True
    assert _wait_until(lambda: b.node.mempool.get(tx.txid) is not None)


def test_sync_catches_up_over_a_non_ip_transport(pair_net):
    """Догон отставшего узла: get_hashes/get_blocks поверх того же потока."""
    a, b = pair_net
    miner = generate_wallet().address
    for _ in range(4):
        a.node.mine_pending(miner)          # намеренно молча, без анонсов
    assert b.node.height < a.node.height

    b.connect("узел-А", 1)                  # connect сам зовёт sync()
    assert _wait_until(lambda: b.node.height == a.node.height)
    assert b.node.blockchain.last_block.hash == a.node.blockchain.last_block.hash


def test_discovery_is_refused_where_it_makes_no_sense(pair_net):
    """У Bluetooth и «точки-точки» широковещания нет.

    Поднимать потоки маяков там, где кричать некуда, — значит делать вид, что
    авто-поиск работает. Честный отказ лучше: у такого транспорта для поиска
    соседей свои средства.
    """
    a, _b = pair_net
    assert a.transport.supports_discovery is False
    assert a.start_discovery() is False
    assert a._discovery_running is False


def test_discovery_still_works_on_tcp():
    """А на TCP авто-поиск обязан остаться прежним."""
    node = P2PNode("127.0.0.1", 0, node=BHydraNode(difficulty=1))
    assert node.transport.supports_discovery is True
    node._running = True
    try:
        assert node.start_discovery() is True
        assert node._discovery_running is True
    finally:
        node.stop_discovery()
        node._running = False
