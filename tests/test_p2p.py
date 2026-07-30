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
    """Блок доходит до соседа.

    Ожидание обязательно: узел рассылает только АНОНС (хеш), а тело сосед
    забирает сам — распространение асинхронное, как в Bitcoin.
    """
    a, b = two_nodes
    a.mine(generate_wallet().address)
    assert _wait_until(lambda: b.node.height == a.node.height)
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
    assert _wait_until(lambda: b.node.height == a.node.height)
    tx = a.node.create_transaction(miner, bob.address, 5, fee=0.1)
    assert a.submit_transaction(tx)
    # Транзакция, как и блок, распространяется АНОНСОМ: тело сосед забирает сам,
    # поэтому submit_transaction возвращает управление раньше доставки.
    assert _wait_until(
        lambda: any(t.txid == tx.txid for t in b.node.mempool.transactions))


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


# --- Анонс блока (inv) вместо рассылки тела ---------------------------------
# Раньше полный блок улетал каждому пиру, даже тому, у кого он уже есть:
# трафик = размер блока × число соседей.
def test_mining_announces_only_the_hash():
    """В рассылке уходит анонс, а не тело блока."""
    node = _lone_node()
    sent = []
    node._gossip = lambda message, **kw: sent.append(message)
    node.node.mine_pending(generate_wallet().address)
    node._announce_block(node.node.blockchain.last_block.hash, background=False)
    assert sent and sent[-1]["type"] == "inv"
    assert "block" not in sent[-1]                 # тела в анонсе нет
    assert sent[-1]["hash"] == node.node.blockchain.last_block.hash


def test_get_block_returns_the_body_by_hash():
    source = _chain_of(3, batch=10)
    try:
        wanted = source.node.blockchain.last_block.hash
        resp = source.send(source.host, source.port,
                           {"type": "get_block", "hash": wanted})
        assert resp["block"]["hash"] == wanted
        missing = source.send(source.host, source.port,
                              {"type": "get_block", "hash": "ff" * 64})
        assert missing["block"] is None
    finally:
        source.stop()


def test_known_block_is_not_refetched():
    """Анонс уже известного блока не тянет тело заново."""
    node = _lone_node()
    node.node.mine_pending(generate_wallet().address)
    known = node.node.blockchain.last_block.hash
    with node._seen_lock:
        node.seen_blocks.add(known)
    resp = json.loads(node._dispatch(node._json(
        {"type": "inv", "kind": "block", "hash": known,
         "from": [node.host, node.port]}), "127.0.0.1").decode())
    assert resp["wanted"] is False


def test_inv_without_a_source_is_ignored():
    """Без адреса, у кого спрашивать, анонс бесполезен."""
    node = _lone_node()
    resp = json.loads(node._dispatch(node._json(
        {"type": "inv", "kind": "block", "hash": "ab" * 64}), None).decode())
    assert resp["wanted"] is False


def test_concurrent_fetches_are_bounded():
    """Число одновременных докачек тел ограничено, а хеши не дублируются."""
    from b_hydra.p2p import MAX_INFLIGHT_FETCHES
    node = _lone_node()
    reserved = [node._start_fetch("%064x" % i) for i in range(MAX_INFLIGHT_FETCHES)]
    assert all(reserved)
    assert node._start_fetch("ff" * 64) is False       # слоты кончились
    node._fetch_slots.release()
    assert node._start_fetch("%064x" % 0) is False     # такой хеш уже качаем


def test_block_propagates_through_announcement(two_nodes):
    """Сквозная проверка: сосед сам забирает тело по анонсу."""
    a, b = two_nodes
    block = a.mine(generate_wallet().address)
    assert _wait_until(lambda: b.node.height == a.node.height)
    assert b.node.blockchain.last_block.hash == block.hash
    assert b.node.is_valid()


# --- Блок со СВЯЗАННЫМИ транзакциями ----------------------------------------
def test_block_with_chained_transactions_is_accepted_by_others():
    """Мемпул разрешает тратить неподтверждённую сдачу — блок с такой цепочкой
    обязан приниматься и другими узлами.

    Раньше _validate_block_transactions искала входы только в подтверждённом
    наборе UTXO, поэтому вторая транзакция цепочки не находила выход первой.
    Такой блок принимал только сам майнер, а сеть его отвергала — то есть узел
    не мог распространить собственный штатный блок.
    """
    source = BHydraNode(difficulty=1)
    alice, bob = generate_wallet(), generate_wallet()
    source.mine_pending(alice.address)
    for _ in range(6):                       # цепочка: каждая тратит сдачу
        source.add_transaction(
            source.create_transaction(alice, bob.address, 0.01, fee=0.001))
    assert len(source.mempool) == 6
    source.mine_pending(alice.address)

    other = BHydraNode(difficulty=1)
    for index in (1, 2):
        assert other.receive_block(source.blockchain.chain[index].to_dict())
    assert other.get_balance(bob.address) == source.get_balance(bob.address)
    assert other.is_valid()


def test_transaction_cannot_spend_a_later_output_in_the_same_block():
    """Внутри блока порядок обязан быть топологическим.

    Иначе можно было бы сослаться на выход транзакции, которая идёт ПОЗЖЕ, —
    а значит и на ещё не существующие деньги.
    """
    source = BHydraNode(difficulty=1)
    alice, bob = generate_wallet(), generate_wallet()
    source.mine_pending(alice.address)
    first = source.create_transaction(alice, bob.address, 1, fee=0.001)
    source.add_transaction(first)
    second = source.create_transaction(alice, bob.address, 0.5, fee=0.001)
    source.add_transaction(second)
    block = source.mine_pending(alice.address)

    # Переставляем связанные транзакции местами — блок должен стать негодным.
    swapped = list(block.data)
    swapped[1], swapped[2] = swapped[2], swapped[1]
    block.data = swapped
    other = BHydraNode(difficulty=1)
    other.receive_block(source.blockchain.chain[1].to_dict())
    assert other._validate_block_transactions(
        block, block.index, other.utxo_set(),
        pq_used=other.pq_used_indices(include_mempool=False)) is False


# --- Пачка блоков ограничена не только числом, но и РАЗМЕРОМ ----------------
# Блок вмещает до 5000 транзакций (~3,8 МБ), поэтому 500 таких блоков — около
# 1,9 ГБ. Сообщение больше MAX_MESSAGE_SIZE получатель просто отбрасывает:
# пачка не пролезает, докачка возвращает пустоту и синхронизация встаёт —
# ровно тот потолок длины, ради снятия которого пачки и вводились.
from b_hydra.p2p import MAX_BATCH_BYTES
from b_hydra.tcp import MAX_MESSAGE_SIZE as _MESSAGE_LIMIT


def _fat_chain(blocks=5, per_block=12):
    """Цепочка из блоков с транзакциями — чтобы тела были ощутимыми."""
    node = P2PNode("127.0.0.1", _free_port(), BHydraNode(difficulty=1))
    alice, bob = generate_wallet(), generate_wallet()
    node.node.mine_pending(alice.address)
    for _ in range(blocks):
        for _ in range(per_block):
            try:
                node.node.add_transaction(node.node.create_transaction(
                    alice, bob.address, 0.001, fee=0.0001))
            except Exception:
                break
        node.node.mine_pending(alice.address)
    node.start()
    time.sleep(0.2)
    return node


def test_batch_budget_is_derived_from_the_message_limit():
    """Бюджет пачки считается ОТ лимита сообщения и заведомо меньше его.

    Два независимых числа рано или поздно разъедутся, и пачка снова
    перестанет пролезать.
    """
    assert MAX_BATCH_BYTES < _MESSAGE_LIMIT
    assert P2PNode("127.0.0.1", _free_port(),
                   BHydraNode(difficulty=1)).max_batch_bytes == MAX_BATCH_BYTES


def test_batch_is_cut_by_size_not_only_by_count():
    """При тесном бюджете пачка укорачивается, хотя лимит по числу не достигнут."""
    source = _fat_chain()
    try:
        one_block = len(json.dumps(source.node.blockchain.chain[1].to_dict()))
        source.max_batch_bytes = one_block * 2      # заведомо меньше всей цепочки
        resp = source.send(source.host, source.port,
                           {"type": "get_blocks", "from": 0, "count": 500})
        assert 0 < len(resp["blocks"]) < source.node.height
        assert len(json.dumps(resp["blocks"])) <= source.max_batch_bytes + one_block
    finally:
        source.stop()


def test_a_single_oversized_block_is_still_served():
    """Один блок отдаём всегда, даже если он больше бюджета.

    Иначе цепочку с крупным блоком нельзя было бы догнать вовсе — докачка
    возвращала бы пустую пачку и вставала.
    """
    source = _fat_chain()
    try:
        source.max_batch_bytes = 1                  # бюджет меньше любого блока
        resp = source.send(source.host, source.port,
                           {"type": "get_blocks", "from": 1, "count": 500})
        assert len(resp["blocks"]) == 1
        assert resp["blocks"][0]["index"] == 1
    finally:
        source.stop()


def test_sync_completes_when_batches_must_be_split():
    """Сквозная проверка: узел догоняет цепочку, даже если в пачку влезает
    всего один блок."""
    source = _fat_chain()
    fresh = P2PNode("127.0.0.1", _free_port(), BHydraNode(difficulty=1))
    fresh.start()
    time.sleep(0.2)
    try:
        source.max_batch_bytes = len(
            json.dumps(source.node.blockchain.chain[1].to_dict()))
        fresh.add_peer(source.host, source.port)
        assert fresh.sync() is True
        assert fresh.node.height == source.node.height
        assert fresh.node.is_valid()
    finally:
        fresh.stop()
        source.stop()


# --- Постоянные соединения ---------------------------------------------------
# Раньше на КАЖДОЕ сообщение открывался новый TCP-сокет: рукопожатие и полный
# круг задержки до первого байта данных. Синхронизация делает к одному соседу
# десятки запросов подряд (get_height + поиск развилки + пачки блоков), и все
# они платили эту цену заново.
from b_hydra.p2p import (INBOUND_TIMEOUT, MAX_POOLED_CONNECTIONS,
                         MAX_POOLED_PER_PEER, POOL_IDLE_TIMEOUT)


def _count_connections(node):
    """Считает принятые соединения, не меняя обработку сообщений."""
    counter = {"n": 0}
    original = node._handle_conn

    def counted(conn, host=None):
        counter["n"] += 1
        return original(conn, host)

    node._handle_conn = counted
    return counter


def _pooled(node, host, port) -> int:
    with node._pool_lock:
        return len(node._pool.get((host, port), []))


def test_repeated_requests_reuse_one_connection():
    """Десять запросов к пиру идут по ОДНОМУ соединению."""
    server = _lone_node()
    accepted = _count_connections(server)
    server.start()
    time.sleep(0.2)
    client = _lone_node()
    try:
        for _ in range(10):
            assert client.send(server.host, server.port,
                               {"type": "ping"})["type"] == "pong"
        assert accepted["n"] == 1
        assert _pooled(client, server.host, server.port) == 1
    finally:
        client.stop()
        server.stop()


def test_sync_uses_a_single_connection():
    """Синхронизация с поиском развилки и пачками — одно соединение.

    Это главный выигрыш: раньше здесь было по сокету на каждый запрос.
    """
    source = _chain_of(20, batch=5)
    accepted = _count_connections(source)
    fresh = P2PNode("127.0.0.1", _free_port(), BHydraNode(difficulty=1))
    fresh.max_blocks_per_message = 5
    try:
        fresh.add_peer(source.host, source.port)
        assert fresh.sync() is True
        assert fresh.node.height == source.node.height
        assert accepted["n"] == 1
    finally:
        fresh.stop()
        source.stop()


def test_pool_idle_timeout_is_derived_from_the_server_timeout():
    """Срок жизни в пуле считается ОТ таймаута сервера, а не отдельным числом.

    Держать сокет в пуле дольше, чем сосед готов молчать, бессмысленно: он его
    уже закрыл, и каждое переиспользование стоило бы лишней попытки. Два
    независимых числа неизбежно разъехались бы.
    """
    assert POOL_IDLE_TIMEOUT < INBOUND_TIMEOUT


def test_stale_pooled_connection_is_not_reused():
    """Залежавшийся сокет закрывается, а не переиспользуется."""
    server = _lone_node()
    server.start()
    time.sleep(0.2)
    client = _lone_node()
    try:
        client.send(server.host, server.port, {"type": "ping"})
        key = (server.host, server.port)
        with client._pool_lock:                 # состарим запись искусственно
            sock, session, _ts = client._pool[key][0]
            client._pool[key][0] = (sock, session,
                                    time.time() - POOL_IDLE_TIMEOUT - 1)
        assert client._checkout(*key) is None   # просрочен — не отдаётся
        assert _pooled(client, *key) == 0
    finally:
        client.stop()
        server.stop()


def test_peer_that_closed_an_idle_connection_is_retried():
    """Сосед закрыл соединение по своему таймауту — запрос всё равно проходит.

    Без повтора штатное закрытие idle-сокета выглядело бы как «пир недоступен»,
    и узел терял бы связь с честными соседями на ровном месте.
    """
    server = _lone_node()
    server.inbound_timeout = 0.3            # закрывает молчащие соединения быстро
    server.start()
    time.sleep(0.2)
    client = _lone_node()
    try:
        assert client.send(server.host, server.port,
                           {"type": "ping"})["type"] == "pong"
        time.sleep(0.6)                     # сервер уже закрыл соединение
        assert _pooled(client, server.host, server.port) == 1   # а мы ещё держим
        assert client.send(server.host, server.port,
                           {"type": "ping"})["type"] == "pong"
    finally:
        client.stop()
        server.stop()


def test_parallel_requests_do_not_share_a_socket():
    """32 потока к одному пиру: ответы не перепутаны.

    Соединение выдаётся из пула ИСКЛЮЧИТЕЛЬНО одному вызывающему — два потока
    в одном сокете смешали бы запросы и ответы.
    """
    server = _chain_of(3, batch=10)
    client = _lone_node()
    results, errors = [], []

    def ask(kind):
        try:
            if kind:
                results.append(client.send(server.host, server.port,
                                           {"type": "ping"})["type"] == "pong")
            else:
                resp = client.send(server.host, server.port,
                                   {"type": "get_height"})
                results.append(resp["height"] == server.node.height)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=ask, args=(i % 2 == 0,)) for i in range(32)]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        assert errors == []
        assert results == [True] * 32
    finally:
        client.stop()
        server.stop()


def test_pool_is_capped_per_peer():
    """На одного пира держим не больше MAX_POOLED_PER_PEER соединений."""
    node = _lone_node()
    socks = [socket.socket() for _ in range(MAX_POOLED_PER_PEER + 3)]
    try:
        for sock in socks:
            node._checkin("10.0.0.1", 7000, sock)
        assert _pooled(node, "10.0.0.1", 7000) == MAX_POOLED_PER_PEER
    finally:
        for sock in socks:
            sock.close()


def test_pool_is_capped_globally():
    """Общий потолок пула не даёт израсходовать файловые дескрипторы."""
    node = _lone_node()
    socks = [socket.socket() for _ in range(MAX_POOLED_CONNECTIONS + 5)]
    try:
        for i, sock in enumerate(socks):
            node._checkin("10.0.%d.1" % i, 7000, sock)   # по одному на пира
        assert node._pooled == MAX_POOLED_CONNECTIONS
    finally:
        for sock in socks:
            sock.close()


def test_stop_closes_live_connections():
    """После stop() узел не отвечает даже по УЖЕ открытому соединению.

    Закрытия слушающего сокета мало: принятые соединения живут отдельно, и
    «остановленный» узел продолжал бы обслуживать по ним запросы.
    """
    server = _lone_node()
    server.start()
    time.sleep(0.2)
    client = _lone_node()
    try:
        assert client.send(server.host, server.port,
                           {"type": "ping"})["type"] == "pong"
        assert _pooled(client, server.host, server.port) == 1
        server.stop()
        time.sleep(0.3)
        with pytest.raises(OSError):
            client.send(server.host, server.port, {"type": "ping"})
    finally:
        client.stop()
        server.stop()


def test_ban_closes_pooled_connections():
    """Бан рвёт постоянные соединения: разговор по открытому сокету — не бан."""
    node = _lone_node()
    sock = socket.socket()
    try:
        node._checkin(EXTERNAL, 7001, sock)
        assert _pooled(node, EXTERNAL, 7001) == 1
        node.ban_peer(EXTERNAL)
        assert _pooled(node, EXTERNAL, 7001) == 0
    finally:
        sock.close()


def test_pool_is_closed_on_stop():
    """stop() не оставляет открытых сокетов в пуле."""
    server = _lone_node()
    server.start()
    time.sleep(0.2)
    client = _lone_node()
    try:
        client.send(server.host, server.port, {"type": "ping"})
        assert client._pooled == 1
        client.stop()
        assert client._pooled == 0
    finally:
        server.stop()


# --- Анонс транзакции (inv) вместо рассылки тела -----------------------------
# Транзакция рассылалась ЦЕЛИКОМ каждому соседу, и в связной сети один и тот же
# байт приходил столько раз, сколько у узла соседей: каждый пересылал тело
# дальше, а получатель отбрасывал его по дедупу.
from b_hydra.p2p import MAX_INFLIGHT_TX_FETCHES


def _funded_node():
    """Узел с намайненной наградой — есть что тратить."""
    node = _lone_node()
    miner = generate_wallet()
    node.node.mine_pending(miner.address)
    return node, miner


def test_transaction_is_announced_by_txid_not_body():
    node, miner = _funded_node()
    sent = []
    node._gossip = lambda message, **kw: sent.append(message)
    tx = node.node.create_transaction(miner, generate_wallet().address, 5, fee=0.1)
    assert node.submit_transaction(tx)
    assert sent and sent[-1]["type"] == "inv"
    assert sent[-1]["kind"] == "tx"
    assert sent[-1]["hash"] == tx.txid
    assert "transaction" not in sent[-1]           # тела в анонсе нет


def test_get_tx_returns_the_body_by_txid():
    node, miner = _funded_node()
    node.start()
    time.sleep(0.2)
    try:
        tx = node.node.create_transaction(miner, generate_wallet().address, 5)
        assert node.node.add_transaction(tx)
        resp = node.send(node.host, node.port,
                         {"type": "get_tx", "txid": tx.txid})
        assert resp["transaction"]["vout"] == [o.to_dict() for o in tx.vout]
        missing = node.send(node.host, node.port,
                            {"type": "get_tx", "txid": "ff" * 64})
        assert missing["transaction"] is None
    finally:
        node.stop()


def test_known_transaction_is_not_refetched():
    """Анонс уже виденной транзакции не тянет тело заново."""
    node, miner = _funded_node()
    tx = node.node.create_transaction(miner, generate_wallet().address, 5)
    with node._seen_lock:
        node.seen_tx.add(tx.txid)
    resp = json.loads(node._dispatch(node._json(
        {"type": "inv", "kind": "tx", "hash": tx.txid,
         "from": [node.host, node.port]}), "127.0.0.1").decode())
    assert resp["wanted"] is False


def test_peer_must_send_the_announced_transaction():
    """Под видом анонсированного txid нельзя прислать другую транзакцию.

    txid считается ОТ СОДЕРЖИМОГО, поэтому сверяется пересчитанный, а не поле
    в присланном JSON.
    """
    node, miner = _funded_node()
    tx = node.node.create_transaction(miner, generate_wallet().address, 5)
    node.send = lambda host, port, message: {"transaction": tx.to_dict()}
    assert node._fetch_tx(("127.0.0.1", 7000), "ab" * 64) is False
    assert node.node.mempool.get(tx.txid) is None


def test_transaction_fetches_are_bounded_separately_from_blocks():
    """У транзакций свой потолок докачек — поток анонсов не вытесняет блоки."""
    node = _lone_node()
    reserved = [node._start_tx_fetch("%064x" % i)
                for i in range(MAX_INFLIGHT_TX_FETCHES)]
    assert all(reserved)
    assert node._start_tx_fetch("ff" * 64) is False     # слоты транзакций кончились
    assert node._start_fetch("ff" * 64) is True         # а блочные свободны
    node._tx_fetch_slots.release()
    assert node._start_tx_fetch("%064x" % 0) is False   # такой txid уже качаем


def test_transaction_propagates_through_announcement(two_nodes):
    """Сквозная проверка: сосед сам забирает тело транзакции по анонсу."""
    a, b = two_nodes
    miner = generate_wallet()
    a.mine(miner.address)
    assert _wait_until(lambda: b.node.height == a.node.height)
    tx = a.node.create_transaction(miner, generate_wallet().address, 5, fee=0.1)
    assert a.submit_transaction(tx)
    assert _wait_until(lambda: b.node.mempool.get(tx.txid) is not None)
    assert b.node.mempool.get(tx.txid).txid == tx.txid


def test_mempool_finds_a_transaction_by_txid():
    """Поиск по txid — O(1) по индексу, иначе get_tx перебирал бы 50 000 штук."""
    node, miner = _funded_node()
    tx = node.node.create_transaction(miner, generate_wallet().address, 5)
    assert node.node.mempool.get(tx.txid) is None
    assert node.node.add_transaction(tx)
    assert node.node.mempool.get(tx.txid) is tx
    node.node.mempool.transactions = []          # прямое присваивание чистит индекс
    assert node.node.mempool.get(tx.txid) is None


def test_one_host_cannot_occupy_every_inbound_slot():
    """Доля одного хоста во входящих соединениях ограничена.

    Пока соединение жило одно сообщение, слот освобождался сам. Постоянное
    держится, пока пир его не отпустит, — поэтому один сосед иначе занял бы всю
    таблицу слотов и не пускал никого больше, просто изредка пингуя.
    """
    from b_hydra.p2p import MAX_INBOUND_PER_HOST
    node = _lone_node()
    for i in range(MAX_INBOUND_PER_HOST):
        assert node._claim_host_slot(EXTERNAL) is True, i
    assert node._claim_host_slot(EXTERNAL) is False      # его доля исчерпана
    assert node._claim_host_slot("198.51.100.4") is True  # другому хосту — можно
    assert node.inbound_connections(EXTERNAL) == MAX_INBOUND_PER_HOST
    node._release_host_slot(EXTERNAL)
    assert node._claim_host_slot(EXTERNAL) is True        # слот освободился


def test_loopback_is_not_limited_per_host():
    """На 127.0.0.1 живут все локальные узлы — общий лимит запретил бы им
    разговаривать друг с другом (та же причина, что и у ban_loopback)."""
    from b_hydra.p2p import MAX_INBOUND_PER_HOST
    node = _lone_node()
    for _ in range(MAX_INBOUND_PER_HOST * 3):
        assert node._claim_host_slot("127.0.0.1") is True


# --- Шифрование канала между узлами ------------------------------------------
# Трафик ходил открытым текстом: кто видел канал, видел адреса, транзакции и
# соседей. Замер на прослушке настоящего соединения — в открытом канале
# находились и адрес майнера, и txid, и тип сообщения.
from b_hydra import secure
from b_hydra.wallet import Wallet


def _capture_wire(monkeypatch):
    """Перехватывает ВСЁ, что уходит в сокеты: и кадры, и рукопожатие.

    Оба модуля берут send_message себе в пространство имён, поэтому подменять
    надо у каждого — иначе часть трафика мимо перехвата, и «не нашли открытый
    текст» ничего не доказывает.
    """
    import b_hydra.p2p as p2p_module
    captured = bytearray()

    for module in (p2p_module, secure):
        original = module.send_message

        def tap(sock, data, _original=original):
            captured.extend(data if isinstance(data, bytes) else data.encode())
            return _original(sock, data)

        monkeypatch.setattr(module, "send_message", tap)
    return captured


def test_traffic_between_nodes_is_encrypted(monkeypatch):
    """В канале не должно быть ни адресов, ни типов сообщений открытым текстом."""
    server = _lone_node()
    server.start()
    time.sleep(0.2)
    client = _lone_node()
    captured = _capture_wire(monkeypatch)
    try:
        miner = generate_wallet()
        server.node.mine_pending(miner.address)
        assert client.send(server.host, server.port,
                           {"type": "get_height"})["height"] == 2
        assert b"get_height" not in captured          # даже тип сообщения скрыт
        assert miner.address.encode() not in captured
        assert b'{"type"' not in captured
        assert secure.MAGIC in captured               # рукопожатие видно — оно и должно
    finally:
        client.stop()
        server.stop()


def test_plaintext_traffic_really_leaks_without_encryption(monkeypatch):
    """Контроль: с encrypt=False то же самое видно в канале открытым текстом.

    Без этой пары тест выше ничего бы не доказывал — он мог бы «проходить»
    просто потому, что перехват не работает.
    """
    server = _lone_node()
    server.start()
    time.sleep(0.2)
    client = _lone_node(encrypt=False)
    captured = _capture_wire(monkeypatch)
    try:
        assert client.send(server.host, server.port, {"type": "get_height"})
        assert b"get_height" in captured
    finally:
        client.stop()
        server.stop()


def test_plaintext_peer_is_still_served_by_default():
    """Сервер терпит открытого клиента: узел, намеренно запущенный без
    шифрования, не должен оказаться отрезан от сети."""
    server = _lone_node()
    server.start()
    time.sleep(0.2)
    client = _lone_node(encrypt=False)
    try:
        assert client.send(server.host, server.port,
                           {"type": "ping"})["type"] == "pong"
    finally:
        client.stop()
        server.stop()


def test_require_encryption_refuses_plaintext():
    """С require_encryption открытого клиента не обслуживают вовсе."""
    server = _lone_node(require_encryption=True)
    server.start()
    time.sleep(0.2)
    plain = _lone_node(encrypt=False)
    encrypted = _lone_node()
    try:
        assert plain.send(server.host, server.port, {"type": "ping"}) == {}
        assert encrypted.send(server.host, server.port,
                              {"type": "ping"})["type"] == "pong"
    finally:
        plain.stop()
        encrypted.stop()
        server.stop()


def test_handshake_failure_does_not_fall_back_to_plaintext():
    """Понижения версии нет: сорванное рукопожатие — отказ, а не открытый канал.

    Молчаливый откат — ровно то, чего добивается активный атакующий: испортил
    рукопожатие и читает дальше.
    """
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    listener.listen(4)

    def refuse():
        while True:
            try:
                conn, _ = listener.accept()
            except OSError:
                return
            with conn:
                _recv(conn)
                _send(conn, b"not a handshake at all")

    threading.Thread(target=refuse, daemon=True).start()
    node = _lone_node()
    try:
        with pytest.raises(OSError):          # HandshakeError — это OSError
            node.send("127.0.0.1", port, {"type": "ping"})
    finally:
        node.stop()
        listener.close()


def test_peer_key_is_pinned_on_first_contact():
    server = _lone_node()
    server.start()
    time.sleep(0.2)
    client = _lone_node()
    try:
        assert client.pinned_key(server.host, server.port) is None
        client.send(server.host, server.port, {"type": "ping"})
        assert client.pinned_key(server.host, server.port) == server.node_key
    finally:
        client.stop()
        server.stop()


def test_impersonated_peer_is_refused_after_pinning():
    """Другой узел на том же адресе — соединения не будет.

    Это и есть смысл закрепления: со второго контакта подмена узла видна.
    """
    port = _free_port()
    first = P2PNode("127.0.0.1", port, BHydraNode(difficulty=1))
    first.start()
    time.sleep(0.2)
    client = _lone_node()
    try:
        client.send("127.0.0.1", port, {"type": "ping"})
        pinned = client.pinned_key("127.0.0.1", port)
        first.stop()
        time.sleep(0.3)
        # На том же адресе поднимается ДРУГОЙ узел (свой ключ).
        impostor = P2PNode("127.0.0.1", port, BHydraNode(difficulty=1))
        impostor.start()
        time.sleep(0.2)
        try:
            assert impostor.node_key != pinned
            with pytest.raises(OSError):
                client.send("127.0.0.1", port, {"type": "ping"})
        finally:
            impostor.stop()
    finally:
        client.stop()
        first.stop()


def test_node_identity_survives_restart(tmp_path):
    """Ключ узла обязан переживать перезапуск.

    Новый ключ после каждого старта выглядел бы для соседей ровно как подмена
    узла — все закрепления ломались бы на ровном месте.
    """
    path = str(tmp_path / "identity.json")
    first = _lone_node(identity_file=path)
    again = _lone_node(identity_file=path)
    assert first.node_key == again.node_key
    assert Wallet.from_private_hex(
        json.loads(open(path, encoding="utf-8").read())["private_key"]
    ).public_key_hex == first.node_key


def test_corrupt_identity_file_is_replaced(tmp_path):
    path = str(tmp_path / "identity.json")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("не json вовсе")
    node = _lone_node(identity_file=path)
    assert len(node.node_key) == 130            # 65 байт несжатой точки в hex


def test_pins_are_saved_and_loaded(tmp_path):
    """Закрепления сохраняются вместе с адресами соседей.

    Иначе после перезапуска каждое соединение снова было бы «первым контактом»,
    и подмена узла опять проходила бы незамеченной.
    """
    path = str(tmp_path / "peers.json")
    node = _lone_node(peers_file=path)
    node.add_peer("10.0.0.7", 5001)
    node.pin_peer("10.0.0.7", 5001, "04" + "ab" * 64)
    assert node.save_peers() is True
    fresh = _lone_node(peers_file=path)
    assert fresh.load_peers() == 1
    assert fresh.pinned_key("10.0.0.7", 5001) == "04" + "ab" * 64


def test_pin_is_not_overwritten_by_a_later_key():
    """Первый ключ и есть доверенный — перезапись сделала бы закрепление
    бессмысленным."""
    node = _lone_node()
    node.pin_peer("10.0.0.8", 5002, "04" + "11" * 64)
    node.pin_peer("10.0.0.8", 5002, "04" + "22" * 64)
    assert node.pinned_key("10.0.0.8", 5002) == "04" + "11" * 64


def test_garbage_pins_in_the_file_are_ignored(tmp_path):
    path = str(tmp_path / "peers.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"peers": [["10.0.0.9", 5003]],
                   "pins": {"без порта": "0411", "10.0.0.9:5003": "04ff"}}, handle)
    node = _lone_node(peers_file=path)
    assert node.load_peers() == 1
    assert node.pinned_key("10.0.0.9", 5003) == "04ff"


def test_encrypted_sync_and_gossip_work_end_to_end(two_nodes):
    """Сквозная проверка: по шифрованному каналу работают и блоки, и sync."""
    a, b = two_nodes
    assert a.pinned_key(b.host, b.port) is None
    a.mine(generate_wallet().address)
    assert _wait_until(lambda: b.node.height == a.node.height)
    # оба узла закрепили ключи друг друга, значит канал был шифрованным
    assert a.pinned_key(b.host, b.port) == b.node_key
    for _ in range(2):
        a.node.mine_pending(generate_wallet().address)
    assert b.sync() is True
    assert b.node.height == a.node.height
    assert b.node.is_valid()


def test_encrypted_session_is_reused_from_the_pool():
    """Рукопожатие стоит дорого (ECDH на чистом Python) — оно должно быть ОДНО
    на соединение, а не на сообщение. Это и делает шифрование посильным."""
    server = _lone_node()
    accepted = _count_connections(server)
    server.start()
    time.sleep(0.2)
    client = _lone_node()
    try:
        for _ in range(8):
            assert client.send(server.host, server.port,
                               {"type": "ping"})["type"] == "pong"
        assert accepted["n"] == 1
    finally:
        client.stop()
        server.stop()
