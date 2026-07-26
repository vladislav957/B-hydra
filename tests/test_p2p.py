"""Тесты P2P-синхронизации: общий генезис, рассылка блоков, sync, консенсус."""

import json
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
    accepted = node._accept_peers({"peers": fake, **node.network_id()})
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
    junk = {"peers": [None, 42, ["хост"], ["хост", "порт"], ["1.2.3.4", 5]],
            **node.network_id()}
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


# --- Опознание сети: не пускаем соседей из чужой цепочки --------------------
# chain_id общий для сети, но узлы с разной базовой сложностью имеют РАЗНЫЙ
# генезис и несовместимые цепочки. Такой сосед занимал бы место в таблице
# пиров и слал блоки, которые всё равно нечем применить.
def _alien_node():
    """Узел другой сети: другая базовая сложность → другой генезис.

    difficulty=2, а не больше: генезис реально майнится, и на чистом
    Python-SHA каждая лишняя единица сложности стоит секунд.
    """
    return P2PNode("127.0.0.1", _free_port(), BHydraNode(difficulty=2))


def _alien_identity():
    """Отпечаток чужой сети без поднятия узла — там, где узел не нужен."""
    return {"chain_id": "b-hydra-mainnet", "genesis": "ff" * 64}


def test_network_id_covers_chain_and_genesis():
    node = _lone_node()
    ident = node.network_id()
    assert ident["genesis"] == node.node.blockchain.chain[0].hash
    assert ident["chain_id"]


def test_missing_network_fields_count_as_mismatch():
    """Проверку нельзя обойти, просто не прислав поля."""
    node = _lone_node()
    ident = node.network_id()
    assert node.same_network(ident) is True
    assert node.same_network({}) is False
    assert node.same_network({"chain_id": ident["chain_id"]}) is False
    assert node.same_network({"genesis": ident["genesis"]}) is False
    assert node.same_network(None) is False


def test_foreign_genesis_is_not_our_network():
    ours, alien = _lone_node(), _alien_node()
    assert ours.node.blockchain.chain[0].hash != alien.node.blockchain.chain[0].hash
    assert ours.same_network(alien.network_id()) is False
    assert ours.same_network(_alien_identity()) is False


def test_foreign_node_cannot_join_peer_table():
    """Узел чужой сети не попадает в пиры — ни к нам, ни мы к нему."""
    ours, alien = _lone_node(), _alien_node()
    ours.start()
    alien.start()
    time.sleep(0.2)
    try:
        assert alien.connect(ours.host, ours.port) is False
        assert (alien.host, alien.port) not in ours.peers
        assert (ours.host, ours.port) not in alien.peers
        assert ours.connect(alien.host, alien.port) is False
    finally:
        ours.stop()
        alien.stop()


def test_hello_from_foreign_network_is_refused():
    ours, alien = _lone_node(), _alien_node()
    ours.start()
    time.sleep(0.2)
    try:
        resp = alien.send(ours.host, ours.port, alien._hello_message())
        assert resp["type"] == "error"
        assert (alien.host, alien.port) not in ours.peers
    finally:
        ours.stop()


def test_same_network_node_still_connects():
    """Свои соединяются как раньше — проверка не мешает нормальной работе."""
    first, second = _lone_node(), _lone_node()
    first.start()
    second.start()
    time.sleep(0.2)
    try:
        assert second.connect(first.host, first.port) is True
        assert (first.host, first.port) in second.peers
        assert (second.host, second.port) in first.peers
    finally:
        first.stop()
        second.stop()


def test_peer_list_from_foreign_network_is_ignored():
    """Список адресов из чужой сети не берём целиком."""
    node = _lone_node()
    offered = {"peers": [["10.5.0.1", 7100], ["10.5.0.2", 7101]],
               **_alien_identity()}
    assert node._accept_peers(offered) == []
    assert node.peers == set()


# --- Повторная рассылка блока не должна оставлять узел позади ---------------
def _lagging_pair():
    """Майнер с цепочкой и отставший узел, оба в одной сети."""
    miner_node = P2PNode("127.0.0.1", _free_port(), BHydraNode(difficulty=1))
    lagging = P2PNode("127.0.0.1", _free_port(), BHydraNode(difficulty=1))
    miner_node.start()
    lagging.start()
    time.sleep(0.2)
    lagging.sync_retry_interval = 0        # в тесте ждать между попытками незачем
    for _ in range(4):
        miner_node.node.mine_pending(generate_wallet().address)
    return miner_node, lagging


def test_block_from_the_future_is_not_marked_seen():
    """Блок, до которого мы не доросли, — не брак, и «виденным» не считается.

    Иначе повторная рассылка того же блока игнорировалась бы молча, и узел
    оставался бы позади до ближайшего периодического sync.
    """
    miner_node, lagging = _lagging_pair()
    try:
        block = miner_node.node.blockchain.last_block.to_dict()
        assert lagging._block_is_ahead(block)
        # Отправитель недоступен → догнать не выйдет, но и запоминать нечего.
        lagging.send(lagging.host, lagging.port,
                     {"type": "block", "block": block,
                      "from": ["127.0.0.1", _free_port()]})
        assert block["hash"] not in lagging.seen_blocks
    finally:
        miner_node.stop()
        lagging.stop()


def test_repeated_block_retries_sync_after_a_failed_attempt():
    """Вторая доставка того же блока даёт узлу ещё один шанс догнать."""
    miner_node, lagging = _lagging_pair()
    try:
        block = miner_node.node.blockchain.last_block.to_dict()
        # Первая попытка: origin — мёртвый порт, синхронизация не удаётся.
        lagging.send(lagging.host, lagging.port,
                     {"type": "block", "block": block,
                      "from": ["127.0.0.1", _free_port()]})
        assert lagging.node.height < miner_node.node.height

        # Тот же блок, но теперь отправитель жив — узел обязан догнать.
        lagging.send(lagging.host, lagging.port,
                     {"type": "block", "block": block,
                      "from": [miner_node.host, miner_node.port]})
        assert _wait_until(
            lambda: lagging.node.height == miner_node.node.height)
    finally:
        miner_node.stop()
        lagging.stop()


def test_unusable_block_is_remembered_to_avoid_revalidation():
    """Брак на нашей же высоте запоминается — дорогую проверку делаем один раз."""
    node = _lone_node()
    bogus = {"index": node.node.height, "previous_hash": "00" * 64,
             "data": [], "timestamp": 1.0, "nonce": 0,
             "target": "%x" % node.node.blockchain.genesis_target,
             "hash": "ab" * 64, "merkle_root": "cd" * 64}
    assert node._block_is_ahead(bogus) is False
    # Узел не поднят — дёргаем обработчик напрямую.
    node._dispatch(node._json({"type": "block", "block": bogus}))
    assert bogus["hash"] in node.seen_blocks


def test_sync_retry_is_throttled():
    """Повторы блока не дают гонять нас за цепочкой без ограничений."""
    node = _lone_node()
    node.sync_retry_interval = 60
    peer = ("127.0.0.1", _free_port())      # недостижим — важен сам факт попытки
    node._sync_from_throttled(peer)         # первая попытка проходит
    assert node._sync_from_throttled(peer) is False   # вторая — отсечена


# --- Инкрементальная синхронизация ------------------------------------------
# Раньше цепочка отдавалась ЦЕЛИКОМ одним сообщением, а на сообщение стоит
# лимит 32 МБ: у сети был жёсткий потолок длины, после которого новый узел не
# смог бы синхронизироваться вовсе. Плюс догнать один блок стоило скачивания
# и полной перепроверки всей цепочки.
from b_hydra.p2p import MAX_BLOCKS_PER_MESSAGE


def _chain_of(blocks, batch=4):
    """Узел с готовой цепочкой и мелкими пачками — чтобы дробление было видно."""
    node = P2PNode("127.0.0.1", _free_port(), BHydraNode(difficulty=1))
    node.max_blocks_per_message = batch
    miner = generate_wallet()
    for _ in range(blocks):
        node.node.mine_pending(miner.address)
    node.start()
    time.sleep(0.2)
    return node


def _count_traffic(node):
    """Считает сообщения и объём ответов при синхронизации."""
    stats = {"messages": 0, "bytes": 0, "max_blocks_in_one": 0}
    original = node.send

    def counted(host, port, message):
        resp = original(host, port, message)
        stats["messages"] += 1
        stats["bytes"] += len(json.dumps(resp))
        stats["max_blocks_in_one"] = max(
            stats["max_blocks_in_one"], len(resp.get("blocks") or []))
        return resp

    node.send = counted
    return stats


def test_blocks_are_served_in_bounded_batches():
    """Пачка блоков ограничена, даже если попросили всю цепочку разом."""
    source = _chain_of(10, batch=3)
    try:
        resp = source.send(source.host, source.port,
                           {"type": "get_blocks", "from": 0, "count": 10_000})
        assert len(resp["blocks"]) == 3            # потолок пачки соблюдён
        assert resp["height"] == source.node.height
        assert resp["blocks"][0]["index"] == 0
    finally:
        source.stop()


def test_get_blocks_returns_the_requested_slice():
    source = _chain_of(8, batch=4)
    try:
        resp = source.send(source.host, source.port,
                           {"type": "get_blocks", "from": 5, "count": 2})
        assert [b["index"] for b in resp["blocks"]] == [5, 6]
    finally:
        source.stop()


def test_fresh_node_syncs_across_several_batches():
    """Новый узел догоняет цепочку, длиннее одной пачки."""
    source = _chain_of(12, batch=3)
    fresh = P2PNode("127.0.0.1", _free_port(), BHydraNode(difficulty=1))
    fresh.max_blocks_per_message = 3
    fresh.start()
    time.sleep(0.2)
    try:
        fresh.add_peer(source.host, source.port)
        stats = _count_traffic(fresh)
        assert fresh.sync() is True
        assert fresh.node.height == source.node.height
        assert fresh.node.blockchain.last_block.hash == \
            source.node.blockchain.last_block.hash
        assert fresh.node.is_valid()
        # Ни одно сообщение не принесло цепочку целиком — потолка длины больше нет.
        assert stats["max_blocks_in_one"] <= 3
    finally:
        fresh.stop()
        source.stop()


def test_catching_up_one_block_does_not_refetch_the_chain():
    """Догон одного блока стоит одного блока, а не всей цепочки."""
    source = _chain_of(15, batch=50)
    fresh = P2PNode("127.0.0.1", _free_port(), BHydraNode(difficulty=1))
    fresh.start()
    time.sleep(0.2)
    try:
        fresh.add_peer(source.host, source.port)
        fresh.sync()                                   # догнали
        assert fresh.node.height == source.node.height

        source.node.mine_pending(generate_wallet().address)   # +1 блок
        stats = _count_traffic(fresh)
        assert fresh.sync() is True
        assert fresh.node.height == source.node.height
        # Прилетел ровно новый блок, а не пятнадцать предыдущих.
        assert stats["max_blocks_in_one"] == 1
    finally:
        fresh.stop()
        source.stop()


def test_sync_switches_to_a_heavier_fork():
    """Реорг: общий блок ищется, докачивается только чужой хвост."""
    source = _chain_of(10, batch=4)
    ours = P2PNode("127.0.0.1", _free_port(), BHydraNode(difficulty=1))
    ours.start()
    time.sleep(0.2)
    try:
        ours.add_peer(source.host, source.port)
        ours.sync()
        assert ours.node.height == source.node.height

        # Своя короткая ветка поверх общего префикса.
        forked = P2PNode("127.0.0.1", _free_port(), BHydraNode(difficulty=1))
        forked.node.replace_chain(
            [b.to_dict() for b in source.node.blockchain.chain[:6]])
        for _ in range(8):
            forked.node.mine_pending(generate_wallet().address)
        forked.start()
        time.sleep(0.2)
        try:
            ours.peers.clear()
            ours.add_peer(forked.host, forked.port)
            assert ours.sync() is True
            assert ours.node.blockchain.last_block.hash == \
                forked.node.blockchain.last_block.hash
            assert ours.node.is_valid()
        finally:
            forked.stop()
    finally:
        ours.stop()
        source.stop()


def test_common_height_finds_the_fork_point():
    """Двоичный поиск находит последний общий блок."""
    source = _chain_of(10, batch=4)
    ours = P2PNode("127.0.0.1", _free_port(), BHydraNode(difficulty=1))
    try:
        ours.add_peer(source.host, source.port)
        ours.node.replace_chain(
            [b.to_dict() for b in source.node.blockchain.chain])
        # Полное совпадение — общий блок это наша вершина.
        assert ours._common_height((source.host, source.port),
                                   source.node.height) == ours.node.height - 1
    finally:
        source.stop()


def test_sync_stops_at_the_block_limit_for_a_lying_peer():
    """Пир, объявивший абсурдную высоту, не заставит качать бесконечно."""
    source = _chain_of(6, batch=3)
    ours = P2PNode("127.0.0.1", _free_port(), BHydraNode(difficulty=1))
    ours.max_sync_blocks = 2
    try:
        fetched = ours._fetch_blocks((source.host, source.port), 0, 10 ** 9)
        assert len(fetched) <= 2
    finally:
        source.stop()


# --- Узел переживает перезапуск ---------------------------------------------
# Таблица пиров жила только в памяти, а UDP-маяк работает лишь в пределах одной
# локальной сети. Поэтому узел в интернете после рестарта оставался ОДИН и
# требовал ручного --peer.
def test_peers_are_saved_and_loaded(tmp_path):
    path = str(tmp_path / "peers.json")
    node = _lone_node()
    node.peers_file = path
    node.add_peer("10.0.0.1", 7001)
    node.add_peer("10.0.0.2", 7002)
    assert node.save_peers() is True

    restarted = _lone_node()
    restarted.peers_file = path
    assert restarted.load_peers() == 2
    assert ("10.0.0.1", 7001) in restarted.peers


def test_stop_saves_peers(tmp_path):
    """Соседи сохраняются при остановке — перезапуск не начинает с нуля."""
    path = str(tmp_path / "peers.json")
    node = _lone_node()
    node.peers_file = path
    node.add_peer("10.0.0.5", 7005)
    node.start()
    time.sleep(0.2)
    node.stop()
    assert json.loads(open(path, encoding="utf-8").read())["peers"] == [["10.0.0.5", 7005]]


def test_corrupt_peers_file_is_ignored(tmp_path):
    """Испорченный файл не роняет узел — просто начинаем без соседей."""
    path = str(tmp_path / "peers.json")
    open(path, "w", encoding="utf-8").write("{ это не json")
    node = _lone_node()
    node.peers_file = path
    assert node.load_peers() == 0
    assert node.peers == set()


def test_missing_peers_file_is_not_an_error(tmp_path):
    node = _lone_node()
    node.peers_file = str(tmp_path / "нет-такого.json")
    assert node.load_peers() == 0


def test_load_peers_skips_garbage_entries(tmp_path):
    path = str(tmp_path / "peers.json")
    open(path, "w", encoding="utf-8").write(json.dumps(
        {"peers": [None, 42, ["хост"], ["хост", "порт"], ["1.2.3.4", 5]]}))
    node = _lone_node()
    node.peers_file = path
    assert node.load_peers() == 1
    assert node.peers == {("1.2.3.4", 5)}


def test_bootstrap_finds_the_network_from_a_seed():
    """Первый запуск: узел знает только seed и всё равно догоняет сеть."""
    source = _chain_of(6, batch=10)
    fresh = P2PNode("127.0.0.1", _free_port(), BHydraNode(difficulty=1),
                    seeds=[(source.host, source.port)])
    fresh.start()
    time.sleep(0.2)
    try:
        assert fresh.bootstrap() == 1
        assert fresh.node.height == source.node.height
        assert (source.host, source.port) in fresh.peers
    finally:
        fresh.stop()
        source.stop()


def test_bootstrap_works_from_saved_peers_without_any_seed(tmp_path):
    """Перезапуск: seed'ов нет, соседи берутся с диска."""
    path = str(tmp_path / "peers.json")
    source = _chain_of(6, batch=10)
    try:
        json.dump({"peers": [[source.host, source.port]]},
                  open(path, "w", encoding="utf-8"))
        restarted = P2PNode("127.0.0.1", _free_port(), BHydraNode(difficulty=1),
                            peers_file=path)          # seeds не заданы вовсе
        restarted.start()
        time.sleep(0.2)
        try:
            assert restarted.bootstrap() == 1
            assert restarted.node.height == source.node.height
        finally:
            restarted.stop()
    finally:
        source.stop()


def test_bootstrap_drops_a_peer_from_another_network():
    alien = _alien_node()
    alien.start()
    time.sleep(0.2)
    ours = P2PNode("127.0.0.1", _free_port(), BHydraNode(difficulty=1),
                   seeds=[(alien.host, alien.port)])
    try:
        assert ours.bootstrap() == 0
        assert (alien.host, alien.port) not in ours.peers
    finally:
        alien.stop()


# --- Остановка и выбор источника синхронизации ------------------------------
def test_stop_really_stops_the_server():
    """После stop() узел не должен отвечать.

    close() не будит поток, висящий в accept(), поэтому «остановленный» узел
    продолжал обслуживать запросы — и другие узлы считали его живым соседом.
    """
    node = _lone_node()
    node.start()
    time.sleep(0.3)
    probe = _lone_node()
    assert probe.send(node.host, node.port, {"type": "ping"})["type"] == "pong"
    node.stop()
    time.sleep(0.3)
    with pytest.raises(OSError):
        probe.send(node.host, node.port, {"type": "ping"})


def test_sync_tries_the_next_peer_when_the_best_one_fails():
    """Один негодный пир не должен перекрывать синхронизацию со всеми.

    Пир, объявивший огромную работу, всегда выигрывает выбор источника. Пока
    пробовался только лучший кандидат, такой сосед — упавший или намеренно
    лгущий — навсегда блокировал догон честной цепочки.
    """
    honest = _chain_of(6, batch=10)
    liar_port = _free_port()
    server = socket.socket()
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", liar_port))
    server.listen(8)

    def lie():
        from b_hydra.tcp import recv_message, send_message
        while True:
            try:
                conn, _ = server.accept()
            except OSError:
                return
            with conn:
                if recv_message(conn):
                    # Огромная работа, но ни хешей, ни блоков не отдаём.
                    send_message(conn, json.dumps(
                        {"type": "height", "height": 10 ** 6,
                         "work": 10 ** 40}).encode())

    threading.Thread(target=lie, daemon=True).start()

    ours = P2PNode("127.0.0.1", _free_port(), BHydraNode(difficulty=1))
    try:
        ours.add_peer("127.0.0.1", liar_port)
        ours.add_peer(honest.host, honest.port)
        assert ours.sync() is True                 # лжец не должен нас запереть
        assert ours.node.height == honest.node.height
    finally:
        server.close()
        honest.stop()


# --- Репутация пиров ---------------------------------------------------------
# Прежние лимиты ограничивали УЩЕРБ от плохого соседа, но не отсекали источник:
# пир, шлющий мусор, оставался в таблице и продолжал получать gossip.
from b_hydra.p2p import (BAN_SCORE, PENALTY_BAD_MESSAGE, PENALTY_INVALID_BLOCK,
                         PENALTY_FOREIGN_NETWORK, PENALTY_GARBAGE_PEERS)
from b_hydra.tcp import recv_message as _recv, send_message as _send

EXTERNAL = "203.0.113.7"          # адрес из документационного диапазона


def _strict_node(**kwargs):
    """Узел, который банит и локальные адреса — иначе тесты на loopback немы."""
    node = _lone_node(**kwargs)
    node.ban_loopback = True
    return node


def _send_garbage(node):
    with socket.socket() as client:
        client.settimeout(3)
        client.connect((node.host, node.port))
        _send(client, b"\xff\xfe not json \x00")
        return _recv(client)


def test_penalties_accumulate_and_lead_to_a_ban():
    node = _strict_node()
    hits = BAN_SCORE // PENALTY_BAD_MESSAGE
    for _ in range(hits - 1):
        node._penalise(EXTERNAL, PENALTY_BAD_MESSAGE)
    assert node.is_banned(EXTERNAL) is False
    assert node.ban_score(EXTERNAL) == PENALTY_BAD_MESSAGE * (hits - 1)
    node._penalise(EXTERNAL, PENALTY_BAD_MESSAGE)
    assert node.is_banned(EXTERNAL) is True


def test_garbage_messages_get_the_sender_banned():
    """Поток неразбираемых сообщений отсекает отправителя."""
    node = _strict_node()
    node.start()
    time.sleep(0.2)
    try:
        for _ in range(BAN_SCORE // PENALTY_BAD_MESSAGE):
            try:
                _send_garbage(node)
            except OSError:
                pass                     # последнее соединение уже могут закрыть
        assert node.is_banned("127.0.0.1") is True
    finally:
        node.stop()


def test_banned_peer_is_not_served():
    """Забаненному отказывают сразу, не тратя поток на разбор сообщения."""
    node = _strict_node()
    node.start()
    time.sleep(0.2)
    try:
        assert node.send(node.host, node.port, {"type": "ping"})["type"] == "pong"
        node.ban_peer("127.0.0.1")
        # Соединение закрывают сразу: send() возвращает пустой ответ (или
        # получает разрыв) — обслуживания нет ни в том, ни в другом случае.
        try:
            answer = node.send(node.host, node.port, {"type": "ping"})
        except OSError:
            answer = {}
        assert not answer
    finally:
        node.stop()


def test_banned_host_cannot_be_added_back():
    node = _strict_node()
    node.ban_peer(EXTERNAL)
    assert node.add_peer(EXTERNAL, 7001) is False
    assert node.peers == set()


def test_ban_drops_every_port_of_that_host():
    node = _strict_node()
    node.add_peer(EXTERNAL, 7001)
    node.add_peer(EXTERNAL, 7002)
    node.add_peer("198.51.100.9", 7003)          # другой хост — не трогаем
    node.ban_peer(EXTERNAL)
    assert node.peers == {("198.51.100.9", 7003)}


def test_ban_expires():
    node = _strict_node()
    node.ban_peer(EXTERNAL, duration=0.3)
    assert node.is_banned(EXTERNAL) is True
    time.sleep(0.5)
    assert node.is_banned(EXTERNAL) is False


def test_isolated_mistake_is_forgiven():
    """Редкий сбой у честного соседа не должен копиться годами."""
    node = _strict_node()
    node.score_reset_after = 0.2
    node._penalise(EXTERNAL, PENALTY_BAD_MESSAGE)
    assert node.ban_score(EXTERNAL) == PENALTY_BAD_MESSAGE
    time.sleep(0.4)                              # давнее нарушение прощается
    node._penalise(EXTERNAL, PENALTY_BAD_MESSAGE)
    assert node.ban_score(EXTERNAL) == PENALTY_BAD_MESSAGE


def test_loopback_is_not_banned_by_default():
    """Иначе одно битое сообщение положило бы все узлы на машине."""
    node = _lone_node()                          # ban_loopback выключен
    for _ in range(BAN_SCORE * 2 // PENALTY_BAD_MESSAGE):
        node._penalise("127.0.0.1", PENALTY_BAD_MESSAGE)
    assert node.is_banned("127.0.0.1") is False
    for _ in range(BAN_SCORE // PENALTY_BAD_MESSAGE):
        node._penalise(EXTERNAL, PENALTY_BAD_MESSAGE)
    assert node.is_banned(EXTERNAL) is True      # внешний — банится как обычно


def test_foreign_network_hello_is_penalised():
    ours = _strict_node()
    alien = _alien_node()
    ours._dispatch(ours._json({"type": "hello", "host": "1.2.3.4", "port": 5,
                               **alien.network_id()}), EXTERNAL)
    assert ours.ban_score(EXTERNAL) == PENALTY_FOREIGN_NETWORK


def test_garbage_peer_list_is_penalised():
    node = _strict_node()
    node._accept_peers({"peers": [None, 42, ["1.2.3.4", 5]], **node.network_id()},
                       source=EXTERNAL)
    assert node.ban_score(EXTERNAL) == PENALTY_GARBAGE_PEERS


def test_unusable_block_is_penalised():
    node = _strict_node()
    bogus = {"index": node.node.height, "previous_hash": "00" * 64,
             "data": [], "timestamp": 1.0, "nonce": 0,
             "target": "%x" % node.node.blockchain.genesis_target,
             "hash": "ab" * 64, "merkle_root": "cd" * 64}
    node._dispatch(node._json({"type": "block", "block": bogus}), EXTERNAL)
    assert node.ban_score(EXTERNAL) == PENALTY_INVALID_BLOCK


def test_lagging_behind_does_not_punish_an_honest_peer():
    """Блок «из будущего» — не вина соседа, а наше отставание.

    Честный майнер, ушедший вперёд, не должен зарабатывать штрафы только за
    то, что мы за ним не поспеваем.
    """
    source = _chain_of(5, batch=10)
    node = _strict_node()
    try:
        ahead = source.node.blockchain.last_block.to_dict()
        assert node._block_is_ahead(ahead)
        node._dispatch(node._json({"type": "block", "block": ahead}), EXTERNAL)
        assert node.ban_score(EXTERNAL) == 0
        assert node.is_banned(EXTERNAL) is False
    finally:
        source.stop()
