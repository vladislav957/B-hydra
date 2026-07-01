"""Тесты ретаргетинга сложности по времени (как в Bitcoin).

Цель пересчитывается на границе окна: блоки шли быстрее цели → труднее
(target меньше), медленнее → проще (target больше), но не легче генезиса и
не более чем в MAX_ADJUST_FACTOR раз за пересчёт.
"""

from b_hydra.blockchain import (
    Blockchain, Block, TARGET_BLOCK_TIME, MAX_ADJUST_FACTOR,
)
from b_hydra.node import BHydraNode
from b_hydra.wallet import generate_wallet

INTERVAL = 4
EXPECTED_WINDOW = int(INTERVAL * TARGET_BLOCK_TIME)   # ожидаемое время окна, сек
# Медленное окно (≥ ожидания) держит цель на базовой (генезис) — удобная отправная
# точка: дальше управляем только вторым окном.
SLOW = EXPECTED_WINDOW


def _build(gaps, difficulty=1, interval=INTERVAL):
    """Собирает цепочку, добавляя блоки с заданными паузами времени (сек).

    `gaps[i]` — пауза перед (i+1)-м блоком. Каждый блок получает цель из
    expected_target, поэтому ретаргетинг применяется автоматически.
    """
    bc = Blockchain(difficulty=difficulty, retarget_interval=interval)
    t = 0.0
    for gap in gaps:
        t += gap
        height = len(bc.chain)
        block = Block(height, bc.last_block.hash, [], timestamp=t,
                      target=bc.expected_target(height))
        block.mine_block()
        bc.chain.append(block)
    return bc


def test_target_constant_inside_window():
    """Внутри окна цель не меняется — все блоки на базовой (генезис) цели."""
    bc = _build([SLOW, SLOW])
    base = bc.genesis_target
    assert all(b.target == base for b in bc.chain)
    assert bc.is_chain_valid()


def test_first_window_slow_stays_at_base():
    """Медленное первое окно оставляет цель на базе (легче генезиса не бывает)."""
    bc = _build([SLOW] * INTERVAL)
    assert bc.expected_target(INTERVAL) == bc.genesis_target


def test_fast_window_increases_difficulty():
    """Быстрое окно → пересчёт делает блок труднее (target ↓), но в пределах ×4."""
    fast = EXPECTED_WINDOW // 8                        # окно сильно быстрее цели
    bc = _build([SLOW] * INTERVAL + [fast, fast, fast, 1])
    prev = bc.chain[2 * INTERVAL - 1].target
    retargeted = bc.expected_target(2 * INTERVAL)
    assert retargeted < bc.genesis_target              # труднее базы
    assert retargeted >= prev // MAX_ADJUST_FACTOR      # но не более чем в 4 раза


def test_adjustment_is_clamped_to_factor():
    """Мгновенное окно даёт ровно максимально допустимый скачок (÷4)."""
    bc = _build([SLOW] * INTERVAL + [0, 0, 0, 1])
    prev = bc.chain[2 * INTERVAL - 1].target           # = генезис (окно 1 медленное)
    lo = EXPECTED_WINDOW // MAX_ADJUST_FACTOR
    expected = prev * lo // EXPECTED_WINDOW             # та же целочисленная формула
    assert bc.expected_target(2 * INTERVAL) == expected
    assert expected >= prev // MAX_ADJUST_FACTOR


def test_target_never_easier_than_genesis():
    """Очень медленные блоки не делают цель легче базовой сети."""
    huge = EXPECTED_WINDOW * 100
    bc = _build([huge] * (2 * INTERVAL))
    assert bc.expected_target(2 * INTERVAL) <= bc.genesis_target


def test_retarget_is_deterministic():
    gaps = [10, 20, 5, 30, 15, 7, 3, 40]
    a = _build(gaps)
    b = _build(gaps)
    assert [x.target for x in a.chain] == [x.target for x in b.chain]


def test_real_mined_chain_is_valid():
    """Цепочка, добытая узлом с коротким окном, проходит полную проверку."""
    node = BHydraNode(difficulty=2)
    node.blockchain.retarget_interval = INTERVAL
    for _ in range(10):
        node.mine_pending(generate_wallet().address)
    assert node.is_valid()
