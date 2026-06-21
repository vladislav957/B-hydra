"""Тесты экономики: награда за блок, халвинг, потолок эмиссии."""

from b_hydra.blockchain import (
    GENESIS_YEAR, HALVING_INTERVAL, HALVING_YEARS, MAX_SUPPLY,
)
from b_hydra.economics import (
    block_reward, blocks_per_year, emission_schedule, mining_end_year,
    total_supply_after,
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


def test_halving_happens_every_4_years():
    # Награда строго 50, и каждые HALVING_YEARS лет делится пополам.
    assert block_reward(0) == 50.0
    # HALVING_INTERVAL блоков добываются ровно за HALVING_YEARS лет.
    assert round(blocks_per_year() * HALVING_YEARS) == HALVING_INTERVAL
    # …и на этой высоте награда падает с 50 до 25.
    assert block_reward(HALVING_INTERVAL - 1) == 50.0
    assert block_reward(HALVING_INTERVAL) == 25.0


def test_mining_ends_after_finite_time():
    # Выпуск монет конечен (как у Bitcoin) — заканчивается в обозримом будущем.
    assert mining_end_year() > GENESIS_YEAR
    assert mining_end_year() < 3000
