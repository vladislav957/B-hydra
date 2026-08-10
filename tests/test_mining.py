"""Майнер: скорость перебора и — главное — умение БРОСИТЬ блок.

Прежний цикл нельзя было остановить: `while int(self.hash, 16) > target` без
единого выхода. Пока сосед не нашёл блок, это нормально; а как только нашёл —
узел продолжал молотить на устаревшем родителе, и вся работа гарантированно
уходила в мусор (сеть такой блок отвергнет, да и сам узел тоже).

Плюс два ускорения, которые ничего не меняют в результате: midstate (неизменная
часть заголовка сжимается один раз) и сравнение байтов вместо перевода
128 hex-символов в число на каждой попытке.

⚠️ Главная проверка здесь — НЕ скорость, а то, что хеш остался прежним
БАЙТ-В-БАЙТ. Изменись он — все существующие блоки стали бы невалидными, то
есть «оптимизация» уничтожила бы цепочку.
"""

import time

import pytest

from b_hydra import hashing
from b_hydra.blockchain import (Block, Blockchain, MINING_CHECK_INTERVAL,
                                genesis_target_for)
from b_hydra.node import BHydraNode
from b_hydra.p2p import P2PNode
from b_hydra.wallet import generate_wallet


def _block(**kwargs):
    options = dict(index=3, previous_hash="ab" * 64, data=["tx1", "tx2"],
                   timestamp=1712345678.9, target=genesis_target_for(2))
    options.update(kwargs)
    return Block(**options)


# --- Совместимость: результат обязан остаться прежним -------------------------
@pytest.mark.parametrize("pure", [True, False])
def test_header_format_is_unchanged(pure):
    """Хеш заголовка — тот же, что и до переписывания цикла.

    Формула зафиксирована здесь дословно: если кто-то поменяет порядок полей
    ради «красоты», тест поймает это раньше, чем цепочка станет невалидной.
    """
    previous = hashing.is_pure()
    hashing.use_pure_sha(pure)
    try:
        block = _block()
        expected = hashing.sha512(
            f"{block.index}{block.previous_hash}{block.merkle_root}"
            f"{block.timestamp}{block.target:x}{block.nonce}")
        assert block.calculate_hash() == expected
        assert block.header_prefix() + str(block.nonce) == (
            f"{block.index}{block.previous_hash}{block.merkle_root}"
            f"{block.timestamp}{block.target:x}{block.nonce}")
    finally:
        hashing.use_pure_sha(previous)


@pytest.mark.parametrize("pure", [True, False])
def test_midstate_gives_exactly_the_same_hash(pure):
    """Ускорение обязано быть невидимым: тот же хеш на любом nonce."""
    previous = hashing.is_pure()
    hashing.use_pure_sha(pure)
    try:
        block = _block()
        base = hashing.sha512_hasher(block.header_prefix())
        for nonce in (0, 1, 7, 12345, 2 ** 31, 999999999999):
            block.nonce = nonce
            assert block._nonce_digest(base).hex() == block.calculate_hash()
    finally:
        hashing.use_pure_sha(previous)


def test_byte_comparison_matches_the_numeric_one():
    """Сравнение байтов эквивалентно сравнению чисел.

    Оба порядка «старший байт первый», поэтому лексикографическое сравнение
    64-байтовых строк и есть сравнение 512-битных чисел. Проверяем на живых
    хешах, а не рассуждением.
    """
    block = _block(target=genesis_target_for(1))
    limit = block.target_bytes()
    base = hashing.sha512_hasher(block.header_prefix())
    for nonce in range(2000):
        block.nonce = nonce
        digest = block._nonce_digest(base)
        assert (digest <= limit) == (int(digest.hex(), 16) <= block.target)


def test_target_bytes_survives_a_huge_target():
    """Цель может быть сколь угодно большой — в 64 байта она обязана влезть."""
    assert len(_block(target=(1 << 512) + 99).target_bytes()) == 64
    assert _block(target=(1 << 512) + 99).target_bytes() == b"\xff" * 64


def test_mined_block_really_satisfies_the_target():
    """Найденный блок обязан проходить проверку — тем же способом, что у узла."""
    block = _block(target=genesis_target_for(2))
    block.mine_block()
    assert int(block.hash, 16) <= block.target
    assert block.hash == block.calculate_hash()      # nonce записан правильно


# --- Прерывание ---------------------------------------------------------------
def test_mining_can_be_abandoned():
    """Майнер обязан уметь сдаваться — иначе работа уходит в мусор."""
    block = _block(target=1)                 # недостижимая цель
    stop = {"now": False}

    def should_stop():
        stop["now"] = True                   # первый же опрос — и хватит
        return True

    started = time.monotonic()
    assert block.mine_block(should_stop=should_stop) is None
    assert stop["now"] is True
    assert time.monotonic() - started < 30   # не «до победного»
    assert block.mining_attempts >= MINING_CHECK_INTERVAL


@pytest.mark.parametrize("budget", [1, 7, MINING_CHECK_INTERVAL,
                                    MINING_CHECK_INTERVAL + 3])
def test_attempt_budget_is_exact(budget):
    """Бюджет соблюдается ТОЧНО, а не «примерно, до ближайшей проверки».

    Сначала он проверялся раз в MINING_CHECK_INTERVAL, и max_attempts=1 молча
    превращался в 512 — то есть «верни управление немедленно» было невозможно.
    """
    block = _block(target=1)
    assert block.mine_block(max_attempts=budget) is None
    assert block.mining_attempts == budget


def test_progress_is_reported():
    """Без отчёта майнинг снаружи выглядит как зависший процесс."""
    block = _block(target=1)
    seen = []
    block.mine_block(max_attempts=MINING_CHECK_INTERVAL * 3,
                     on_progress=lambda attempts, rate: seen.append((attempts, rate)))
    assert len(seen) >= 2
    assert all(attempts > 0 and rate > 0 for attempts, rate in seen)
    assert seen[-1][0] > seen[0][0]          # счётчик растёт


def test_hashrate_is_measured():
    block = _block(target=genesis_target_for(1))
    block.mine_block()
    assert block.hashrate() > 0
    assert block.mining_seconds > 0


def test_abandoned_block_is_not_added_to_the_chain():
    """Недомайненный блок — не блок: в цепочке ему места нет.

    ⚠️ Одна попытка при difficulty=1 УГАДЫВАЕТ примерно раз на шестнадцать:
    `genesis_target_for` — это число ведущих нулей hex, значит цель проходит
    каждый шестнадцатый хеш. Прежняя версия проверяла единственный вызов и
    поэтому падала примерно в 6% прогонов — что и случилось на CI, на ровном
    месте. Здесь проверяются ОБЕ ветки, а брошенный блок ловится за несколько
    попыток: вероятность промахнуться мимо него — 16⁻²⁴.
    """
    abandoned = 0
    for _ in range(24):
        chain = Blockchain(difficulty=1)
        height = len(chain.chain)
        block = chain.add_block(data=["x"], max_attempts=1)
        if block is None:
            assert len(chain.chain) == height, "брошенный блок попал в цепочку"
            abandoned += 1
        else:
            # Повезло с первого хеша — тогда блок обязан быть настоящим.
            assert len(chain.chain) == height + 1
            assert chain.is_chain_valid()
    assert abandoned, "24 попытки подряд угадали цель — так не бывает"


# --- Мемпул при брошенном блоке ------------------------------------------------
def _node_with_pending(count=3):
    """Узел с добытым блоком и `count` переводами в мемпуле."""
    node = BHydraNode(difficulty=1)
    sender = generate_wallet()
    node.mine_pending(sender.address)
    for _ in range(count):
        node.add_transaction(
            node.create_transaction(sender, generate_wallet().address, 1, 0.1))
    return node, sender


def test_abandoned_block_returns_transactions_to_the_mempool():
    """Транзакции снимаются с мемпула ДО майнинга — и обязаны вернуться.

    Иначе прерванный майнинг молча съедал бы чужие переводы: из мемпула их
    убрали, а в цепочку не положили.

    ⚠️ Одна попытка при difficulty=1 угадывает цель примерно раз на шестнадцать,
    поэтому ждать отказа от единственного вызова нельзя — тест плавал бы.
    Берём свежий узел, пока блок не окажется брошенным.
    """
    for _ in range(24):
        node, sender = _node_with_pending()
        before = {tx.txid for tx in node.mempool.transactions}
        assert len(before) == 3
        if node.mine_pending(sender.address, max_attempts=1) is None:
            assert {tx.txid for tx in node.mempool.transactions} == before
            return
        # Повезло с первого хеша: блок добыт, переводы законно ушли в него.
        assert not node.mempool.transactions
    raise AssertionError("24 попытки подряд угадали цель — так не бывает")


def test_returning_does_not_swallow_transactions_that_arrived_meanwhile():
    """Пока шёл майнинг, могли прийти новые транзакции — затирать их нельзя."""
    node = BHydraNode(difficulty=1)
    sender = generate_wallet()
    node.mine_pending(sender.address)
    old = node.create_transaction(sender, generate_wallet().address, 1, 0.1)
    node.add_transaction(old)
    fresh = node.create_transaction(sender, generate_wallet().address, 2, 0.1)

    taken = list(node.mempool.transactions)
    node.mempool.transactions = [fresh]      # «пришла новая, пока мы майнили»
    restored = node._return_to_mempool(taken)

    assert restored == 1
    txids = {tx.txid for tx in node.mempool.transactions}
    assert txids == {old.txid, fresh.txid}


def test_returning_does_not_duplicate():
    """Повторный возврат не должен размножать транзакции."""
    node = BHydraNode(difficulty=1)
    sender = generate_wallet()
    node.mine_pending(sender.address)
    tx = node.create_transaction(sender, generate_wallet().address, 1, 0.1)
    node.add_transaction(tx)

    taken = list(node.mempool.transactions)
    assert node._return_to_mempool(taken) == 0        # они и так на месте
    assert len(node.mempool.transactions) == 1


# --- Гонка в настоящей сети ----------------------------------------------------
def test_node_abandons_mining_when_a_neighbour_wins():
    """Сосед прислал блок — наш родитель устарел, добивать бессмысленно.

    Это и есть то, ради чего всё затевалось: раньше узел домалывал заведомо
    мёртвый блок, а потом сам же его отклонял.
    """
    node = P2PNode("127.0.0.1", 5000, node=BHydraNode(difficulty=1))
    miner = generate_wallet().address
    # Цель недостижима — блок не найдётся никогда, значит выход только через
    # should_stop.
    node.node.blockchain.expected_target = lambda height: 1

    def steal_the_block():
        """Изображаем соседа: подкладываем в цепочку чужой блок."""
        time.sleep(0.05)
        chain = node.node.blockchain
        stolen = Block(index=len(chain.chain), previous_hash=chain.last_block.hash,
                       data=["чужой блок"], target=genesis_target_for(1))
        stolen.mine_block()
        chain.chain.append(stolen)

    import threading

    thief = threading.Thread(target=steal_the_block, daemon=True)
    thief.start()
    started = time.monotonic()
    assert node.mine(miner) is None                # блок брошен
    thief.join(timeout=10)
    assert time.monotonic() - started < 60         # а не «до победного»


def test_mining_still_works_without_any_callbacks():
    """Без колбэков поведение прежнее: перебирать до победы."""
    node = BHydraNode(difficulty=1)
    block = node.mine_pending(generate_wallet().address)
    assert block is not None
    assert int(block.hash, 16) <= block.target


# --- Скорость (не строгая, чтобы не падать на медленной машине) ----------------
def test_new_loop_is_faster_than_the_old_one():
    """Ускорение должно быть заметным, иначе переписывать было незачем.

    Порог мягкий (×1,5 вместо измеренных ×3): тест бегает на разном железе и
    рядом с другими тестами, и цель здесь — поймать РЕГРЕССИЮ, а не заверить
    конкретную цифру.
    """
    previous = hashing.is_pure()
    hashing.use_pure_sha(False)
    try:
        block = _block(target=1)
        started = time.monotonic()
        block.mine_block(max_attempts=40000)
        fast = block.mining_attempts / (time.monotonic() - started)

        old = _block(target=1)
        started = time.monotonic()
        count = 0
        while count < 40000:
            old.nonce += 1
            old.hash = old.calculate_hash()
            _ = int(old.hash, 16) > old.target
            count += 1
        slow = count / (time.monotonic() - started)
        assert fast > slow * 1.5, f"было {slow:.0f}/с, стало {fast:.0f}/с"
    finally:
        hashing.use_pure_sha(previous)
