"""Тесты P2P-синхронизации: общий генезис, рассылка блоков, sync, консенсус."""

import socket
import threading
import time

import pytest

from b_hydra.node import BHydraNode
from b_hydra.p2p import P2PNode
from b_hydra.wallet import generate_wallet


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def two_nodes():
    pa, pb = _free_port(), _free_port()
    a = P2PNode("127.0.0.1", pa, BHydraNode(difficulty=2))
    b = P2PNode("127.0.0.1", pb, BHydraNode(difficulty=2))
    a.start()
    b.start()
    time.sleep(0.2)
    a.add_peer("127.0.0.1", pb)
    b.add_peer("127.0.0.1", pa)
    yield a, b
    a.stop()
    b.stop()


def test_nodes_share_genesis(two_nodes):
    a, b = two_nodes
    assert a.node.blockchain.chain[0].hash == b.node.blockchain.chain[0].hash


def test_block_is_broadcast(two_nodes):
    a, b = two_nodes
    a.mine(generate_wallet().address)
    assert b.node.height == a.node.height
    assert b.node.blockchain.last_block.hash == a.node.blockchain.last_block.hash


def test_sync_catches_up_a_lagging_node(two_nodes):
    a, b = two_nodes
    miner = generate_wallet()
    for _ in range(3):                 # майним напрямую, без рассылки
        a.node.mine_pending(miner.address)
    assert b.node.height < a.node.height
    b.sync()
    assert b.node.height == a.node.height
    assert b.node.is_valid()


def test_transaction_propagates(two_nodes):
    a, b = two_nodes
    miner, bob = generate_wallet(), generate_wallet()
    a.mine(miner.address)              # рассылается → B тоже знает этот UTXO
    tx = a.node.create_transaction(miner, bob.address, 5, fee=0.1)
    assert a.submit_transaction(tx)
    assert any(t.txid == tx.txid for t in b.node.mempool.transactions)


def test_longest_valid_chain_wins(two_nodes):
    a, b = two_nodes
    a.node.mine_pending(generate_wallet().address)        # A: высота 2
    for _ in range(3):
        b.node.mine_pending(generate_wallet().address)    # B: высота 4
    a.sync()
    assert a.node.height == b.node.height == 4
    assert a.node.blockchain.last_block.hash == b.node.blockchain.last_block.hash


def test_shorter_chain_is_not_adopted(two_nodes):
    a, b = two_nodes
    for _ in range(3):
        a.node.mine_pending(generate_wallet().address)    # A: высота 4
    b.node.mine_pending(generate_wallet().address)        # B: высота 2
    a.sync()                                              # B короче — не принимаем
    assert a.node.height == 4


def _wait_until(cond, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return cond()


@pytest.fixture
def line_nodes():
    """Линейная топология A — B — C (A и C НЕ соседи)."""
    pa, pb, pc = _free_port(), _free_port(), _free_port()
    a = P2PNode("127.0.0.1", pa, BHydraNode(difficulty=1))
    b = P2PNode("127.0.0.1", pb, BHydraNode(difficulty=1))
    c = P2PNode("127.0.0.1", pc, BHydraNode(difficulty=1))
    for n in (a, b, c):
        n.start()
    time.sleep(0.2)
    a.add_peer("127.0.0.1", pb)
    b.add_peer("127.0.0.1", pa)
    b.add_peer("127.0.0.1", pc)
    c.add_peer("127.0.0.1", pb)
    yield a, b, c
    for n in (a, b, c):
        n.stop()


def test_block_propagates_multi_hop(line_nodes):
    a, b, c = line_nodes
    a.mine(generate_wallet().address)   # A не сосед C — блок дойдёт через B
    assert _wait_until(lambda: c.node.height == a.node.height)
    assert c.node.blockchain.last_block.hash == a.node.blockchain.last_block.hash


def test_transaction_propagates_multi_hop(line_nodes):
    a, b, c = line_nodes
    miner = generate_wallet()
    a.mine(miner.address)                                # фондируем + распространяем
    assert _wait_until(lambda: c.node.height == a.node.height)
    tx = a.node.create_transaction(miner, generate_wallet().address, 5)
    a.submit_transaction(tx)
    assert _wait_until(
        lambda: any(t.txid == tx.txid for t in c.node.mempool.transactions))


# --- Ограничение роста seen_tx / seen_blocks (анти-петля без утечки) --------
from b_hydra.p2p import _BoundedSet, SEEN_LIMIT


def test_bounded_set_evicts_oldest_keeps_recent():
    """При переполнении вытесняется самый старый, свежие остаются."""
    s = _BoundedSet(max_size=3)
    for item in ("a", "b", "c"):
        s.add(item)
    assert len(s) == 3 and "a" in s
    s.add("d")                       # переполнение → выталкивается "a"
    assert len(s) == 3
    assert "a" not in s              # старый вытеснен
    assert "b" in s and "c" in s and "d" in s   # свежие на месте


def test_bounded_set_dedup_no_growth_on_repeat():
    """Повтор уже виденного не растит множество и не двигает порядок."""
    s = _BoundedSet(max_size=3)
    s.add("a"); s.add("b"); s.add("c")
    s.add("a")                       # уже видели — размер не меняется
    assert len(s) == 3
    s.add("d")                       # "a" по-прежнему самый старый → его и вытолкнет
    assert "a" not in s and "b" in s


def test_node_uses_bounded_seen_sets():
    """У узла seen_* ограничены по размеру и дедуп работает."""
    node = P2PNode("127.0.0.1", _free_port(), BHydraNode(difficulty=2),
                   seen_limit=5)
    for i in range(20):
        node.seen_tx.add(f"tx{i}")
    assert len(node.seen_tx) == 5            # рост ограничен
    assert "tx19" in node.seen_tx            # свежие сохранены
    assert "tx0" not in node.seen_tx         # старые вытеснены
    # дедуп: недавно виденное по-прежнему распознаётся
    node.seen_tx.add("tx19")
    assert "tx19" in node.seen_tx and len(node.seen_tx) == 5


def test_default_seen_limit_is_bounded():
    node = P2PNode("127.0.0.1", _free_port(), BHydraNode(difficulty=2))
    for i in range(SEEN_LIMIT + 100):
        node.seen_blocks.add(f"h{i}")
    assert len(node.seen_blocks) == SEEN_LIMIT


# --- Устойчивость сетевого слоя к недружелюбному пиру -----------------------
# Адреса пиров ничего не стоят и никак не проверяются, поэтому раздуть таблицу
# и заставить узел ходить по несуществующим адресам — самая дешёвая атака на
# сетевой слой. Тесты ниже закрепляют пределы, которые её обезвреживают.
from b_hydra.p2p import MAX_PEERS_PER_MESSAGE


def _lone_node(**kwargs):
    return P2PNode("127.0.0.1", _free_port(), BHydraNode(difficulty=1), **kwargs)


def test_peer_table_is_capped():
    """Таблица пиров не растёт бесконечно."""
    node = _lone_node(max_peers=16)
    for i in range(200):
        node.add_peer("10.0.%d.%d" % (i // 250, i % 250), 6000 + i)
    assert len(node.peers) == 16


def test_add_peer_reports_only_genuinely_new():
    node = _lone_node()
    assert node.add_peer("10.0.0.1", 6001) is True
    assert node.add_peer("10.0.0.1", 6001) is False      # уже знаем
    assert node.add_peer(node.host, node.port) is False  # это мы сами


def test_one_response_cannot_flood_peer_table():
    """Один ответ пира даёт не больше MAX_PEERS_PER_MESSAGE адресов."""
    node = _lone_node()
    fake = [["10.9.%d.%d" % (i // 250, i % 250), 7000 + i] for i in range(500)]
    accepted = node._accept_peers({"peers": fake})
    assert len(accepted) == MAX_PEERS_PER_MESSAGE
    assert len(node.peers) == MAX_PEERS_PER_MESSAGE


def test_node_does_not_amplify_peer_list():
    """Узел отдаёт ограниченный список — порча не расползается по сети."""
    node = _lone_node()
    for i in range(MAX_PEERS_PER_MESSAGE * 4):
        node.add_peer("10.8.%d.%d" % (i // 250, i % 250), 7000 + i)
    assert len(node._peers_payload()["peers"]) == MAX_PEERS_PER_MESSAGE


def test_accept_peers_ignores_garbage():
    """Мусор в списке пиров не роняет разбор и не попадает в таблицу."""
    node = _lone_node()
    junk = {"peers": [None, 42, ["хост"], ["хост", "порт"], ["1.2.3.4", 5]]}
    assert node._accept_peers(junk) == [("1.2.3.4", 5)]


def test_silent_connection_does_not_hold_thread_forever():
    """Slowloris: пир открыл соединение и замолчал — поток освобождается сам."""
    node = _lone_node()
    node.inbound_timeout = 0.3
    node.start()
    time.sleep(0.2)
    base = threading.active_count()
    socks = []
    for _ in range(5):
        sock = socket.socket()
        sock.connect((node.host, node.port))
        sock.sendall(b"\x00\x00")          # половина заголовка длины, дальше тишина
        socks.append(sock)
    try:
        assert _wait_until(lambda: threading.active_count() <= base, timeout=5)
        assert node.send(node.host, node.port, {"type": "ping"})["type"] == "pong"
    finally:
        for sock in socks:
            sock.close()
        node.stop()


def test_concurrent_peer_updates_do_not_break_iteration():
    """Обход таблицы во время добавления пиров не падает (гонка на set)."""
    node = _lone_node(max_peers=100_000)
    errors = []
    stop = threading.Event()

    def writer():
        i = 0
        while not stop.is_set():
            node.add_peer("10.1.%d.%d" % (i // 250 % 250, i % 250), 7000 + i % 900)
            i += 1

    def reader():
        while not stop.is_set():
            try:
                node.peer_list()
                node._peers_payload()
            except Exception as exc:       # RuntimeError: set changed size…
                errors.append(exc)

    threads = ([threading.Thread(target=writer, daemon=True) for _ in range(2)]
               + [threading.Thread(target=reader, daemon=True) for _ in range(3)])
    for thread in threads:
        thread.start()
    time.sleep(1.0)
    stop.set()
    for thread in threads:
        thread.join(timeout=2)
    assert errors == []


def test_gossip_does_not_serialize_on_unreachable_peers():
    """Недостижимые пиры обходятся параллельно, а не по очереди.

    Последовательный обход был бы ≈8 × peer_timeout; параллельный укладывается
    примерно в один таймаут.
    """
    node = _lone_node()
    node.peer_timeout = 0.5
    for i in range(8):
        node.add_peer("10.255.255.%d" % (i + 1), 9300 + i)   # «чёрные дыры»
    started = time.time()
    node._gossip({"type": "ping"})
    assert time.time() - started < 8 * node.peer_timeout / 2


# --- Выбор источника синхронизации: по работе, а не по высоте ---------------
def test_get_height_reports_chain_work(two_nodes):
    a, b = two_nodes
    b.node.mine_pending(generate_wallet().address)
    resp = a.send(b.host, b.port, {"type": "get_height"})
    assert resp["height"] == b.node.height
    assert resp["work"] == b.node.blockchain.total_work


def _sync_with_canned_replies(node, replies):
    """Подменяет опрос пиров готовыми ответами; возвращает выбранного пира."""
    chosen = []
    node._fanout = lambda peers, action: [(p, replies[p]) for p in peers]
    node._sync_from = lambda peer: chosen.append(peer) or True
    node.sync()
    return chosen


def test_sync_prefers_heaviest_chain_not_tallest():
    """Короткая, но более трудная цепочка выигрывает у длинной и «дешёвой»."""
    node = _lone_node()
    tall, heavy = ("10.0.0.1", 7001), ("10.0.0.2", 7002)
    node.add_peer(*tall)
    node.add_peer(*heavy)
    base = node.node.blockchain.total_work
    chosen = _sync_with_canned_replies(node, {
        tall: {"height": 100, "work": base + 1},        # выше, но легче
        heavy: {"height": 10, "work": base + 500},      # ниже, но тяжелее
    })
    assert chosen == [heavy]


def test_sync_ignores_peers_no_heavier_than_us():
    node = _lone_node()
    peer = ("10.0.0.3", 7003)
    node.add_peer(*peer)
    base = node.node.blockchain.total_work
    assert _sync_with_canned_replies(
        node, {peer: {"height": 99, "work": base}}) == []


def test_sync_falls_back_to_height_for_legacy_peers():
    """Пир старой версии работу не сообщает — тогда судим по высоте."""
    node = _lone_node()
    peer = ("10.0.0.4", 7004)
    node.add_peer(*peer)
    assert _sync_with_canned_replies(
        node, {peer: {"height": node.node.height + 5}}) == [peer]
