"""Тесты экономики: награда за блок, халвинг, потолок эмиссии."""

from b_hydra.blockchain import (
    Blockchain, HALVING_INTERVAL, MAX_SUPPLY, MINING_END_HEIGHT, TARGET_END_YEAR,
)
from b_hydra.economics import (
    block_reward, emission_schedule, mining_end_year, total_supply_after,
)


def test_initial_reward():
    assert block_reward(0) == 50.0


def test_halving():
    assert block_reward(HALVING_INTERVAL) == 25.0
    assert block_reward(HALVING_INTERVAL * 2) == 12.5


def test_reward_zero_after_many_halvings():
    assert block_reward(HALVING_INTERVAL * 64) == 0.0


def test_total_emission_hits_cap():
    total = sum(emission for _, _, emission in emission_schedule(64))
    assert round(total) == MAX_SUPPLY     # 31 000 000 BHY


def test_supply_capped():
    assert total_supply_after(10 ** 9) <= MAX_SUPPLY


def test_reward_halves_at_interval():
    # Награда строго 50 и делится пополам на границе интервала халвинга.
    assert block_reward(HALVING_INTERVAL - 1) == 50.0
    assert block_reward(HALVING_INTERVAL) == 25.0


def test_mining_ends_around_target_year():
    # Майнеры получают награду примерно до TARGET_END_YEAR (~3000).
    assert round(mining_end_year()) == TARGET_END_YEAR == 3000


def test_consensus_and_economics_reward_agree():
    """Консенсусный Blockchain.block_reward и модульный economics.block_reward
    обязаны совпадать на всех границах эпох — иначе разъедется эмиссия и
    проверка coinbase начнёт отвергать честные блоки (или пропускать печать)."""
    bc = Blockchain(difficulty=1)
    for era in range(0, 66):
        for offset in (-1, 0, 1):
            height = era * HALVING_INTERVAL + offset
            if height >= 0:
                assert bc.block_reward(height) == block_reward(height)


def test_exact_total_emission():
    """Полная эмиссия по расписанию — фиксированное значение (недобор до
    круглых 31M — следствие округления до 1e-8, как у Bitcoin ~20.99999M)."""
    total = sum(block_reward(era * HALVING_INTERVAL) * HALVING_INTERVAL
                for era in range(64))
    assert round(total, 8) == 30_999_999.9969
    assert total < MAX_SUPPLY                       # потолок никогда не превышен


def test_emission_ends_at_expected_height():
    """34 халвинга до обнуления награды → конец эмиссии на этой высоте."""
    assert MINING_END_HEIGHT == 34 * HALVING_INTERVAL == 10_540_000
    assert block_reward(MINING_END_HEIGHT) == 0.0
    assert block_reward(MINING_END_HEIGHT - HALVING_INTERVAL) > 0.0


def test_mined_supply_matches_schedule():
    """Фактически начеканенное = сумме запланированных наград (майнер берёт
    ровно награду блока; комиссии — переработка старых монет, не эмиссия)."""
    from b_hydra.node import BHydraNode
    from b_hydra.wallet import generate_wallet
    node = BHydraNode(difficulty=1)
    miner = generate_wallet()
    for _ in range(5):
        node.mine_pending(miner.address)
    scheduled = sum(node.blockchain.block_reward(b.index)
                    for b in node.blockchain.chain[1:])
    assert node.get_balance(miner.address) == scheduled == node.blockchain.total_supply
