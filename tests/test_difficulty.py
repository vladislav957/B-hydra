"""Тесты пропорциональной сложности: чем больше майнеров, тем труднее блок."""

from b_hydra.blockchain import Blockchain, _HASH_SPACE
from b_hydra.node import BHydraNode
from b_hydra.wallet import generate_wallet


def test_work_is_proportional_to_miner_count():
    """Требуемая работа растёт линейно с числом разных майнеров."""
    node = BHydraNode(difficulty=2)
    base_target = node.blockchain.genesis_target
    base_work = _HASH_SPACE // base_target

    works = []
    for _ in range(5):                       # каждый блок — новый майнер
        node.mine_pending(generate_wallet().address)
        target = node.blockchain.expected_target(node.height)
        works.append(_HASH_SPACE // target)

    # 1 майнер → base, 2 → 2×, 3 → 3× … строго пропорционально.
    assert works[0] == base_work             # 1 майнер
    assert works[1] == 2 * base_work          # 2 майнера
    assert works[2] == 3 * base_work          # 3 майнера
    assert works[-1] > works[0]               # больше майнеров — труднее


def test_fewer_miners_is_easier():
    """Меньше майнеров — проще (больше target = меньше работы)."""
    node = BHydraNode(difficulty=2)
    one = node.blockchain.genesis_target                    # 1 майнер (база)
    node.mine_pending(generate_wallet().address)
    node.mine_pending(generate_wallet().address)
    two = node.blockchain.expected_target(node.height)      # 2 майнера
    assert two < one                                        # target меньше → труднее


def test_single_miner_stays_at_base():
    node = BHydraNode(difficulty=2)
    alice = generate_wallet()
    for _ in range(3):
        node.mine_pending(alice.address)     # один и тот же майнер
    base = node.blockchain.genesis_target
    assert all(b.target == base for b in node.blockchain.chain)
    assert node.is_valid()


def test_chain_with_many_miners_is_valid():
    node = BHydraNode(difficulty=2)
    for _ in range(5):
        node.mine_pending(generate_wallet().address)
    assert node.is_valid()
