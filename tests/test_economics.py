"""Тесты экономики: награда за блок, халвинг, потолок эмиссии."""

from b_hydra.blockchain import HALVING_INTERVAL, MAX_SUPPLY, TARGET_END_YEAR
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
