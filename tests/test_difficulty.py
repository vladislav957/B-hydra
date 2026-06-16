"""Тесты динамической сложности: чем больше участников, тем сложнее."""

from b_hydra.blockchain import difficulty_for_participants
from b_hydra.node import BHydraNode
from b_hydra.wallet import generate_wallet


def test_difficulty_grows_with_participants():
    assert difficulty_for_participants(0, base=3) == 3
    assert difficulty_for_participants(1, base=3) == 3
    assert difficulty_for_participants(2, base=3) == 4
    assert difficulty_for_participants(4, base=3) == 5


def test_difficulty_capped():
    assert difficulty_for_participants(10_000, base=3, cap=6) == 6


def test_more_miners_increase_block_difficulty():
    node = BHydraNode(difficulty=2)
    for miner in (generate_wallet() for _ in range(4)):
        node.mine_pending(miner.address)
    diffs = [b.difficulty for b in node.blockchain.chain]
    # С появлением новых майнеров сложность поздних блоков выросла.
    assert diffs[-1] > diffs[1]
    assert node.is_valid()


def test_single_participant_keeps_base_difficulty():
    node = BHydraNode(difficulty=2)
    alice = generate_wallet()
    node.mine_pending(alice.address)
    node.mine_pending(alice.address)     # тот же майнер — участник один
    assert all(b.difficulty == 2 for b in node.blockchain.chain)
    assert node.is_valid()
