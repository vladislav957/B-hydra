"""
economics.py — B-hydra issuance economics.

Computes the block reward (with halving and rounding to the coin's
divisibility), the total issuance and the year mining ends. The parameters
come from blockchain.py so that the economics always match the consensus
rules:

    310,000 (halving interval) * 50 (reward) * 2  =  31,000,000 (maximum).

The reward is exactly 50 BHY and halves every HALVING_INTERVAL blocks (like
Bitcoin's halving); coin issuance is finite and ends around the year 3000.
"""

if __name__ == "__main__" and __package__ in (None, ""):
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    __package__ = "b_hydra"

from .blockchain import (
    INITIAL_REWARD, HALVING_INTERVAL, MAX_SUPPLY, DECIMALS,
    BLOCK_TIME_SECONDS, SECONDS_PER_YEAR, GENESIS_YEAR,
    MINING_END_HEIGHT,
)


def block_reward(height: int) -> float:
    """The block reward at a given height (rounded to the divisibility)."""
    halvings = height // HALVING_INTERVAL
    if halvings >= 64:
        return 0.0
    return round(INITIAL_REWARD / (2 ** halvings), DECIMALS)


def blocks_per_year() -> float:
    """How many blocks are mined per year at the current block time."""
    return SECONDS_PER_YEAR / BLOCK_TIME_SECONDS


def halving_years() -> float:
    """How many years pass between halvings at the TARGET block time.

    ⚠️ This is a CONSEQUENCE, not a rule. The consensus rule goes by HEIGHT:
    the reward halves every HALVING_INTERVAL blocks, and no calendar enters
    into it. The years follow from the target block time, while the actual
    time drifts around the target along with the network hashrate: more
    miners and the halving arrives early, fewer and it arrives late. Writing
    "a halving every N years" in documents without that caveat is not
    allowed, or the number reads as a promise the code does not make. (The
    white paper said "every 4 years" — Bitcoin's figure, unrelated to our
    parameters.)
    """
    return HALVING_INTERVAL * BLOCK_TIME_SECONDS / SECONDS_PER_YEAR


def year_of_height(height: int) -> float:
    """The calendar year by which a block at the given height will be mined."""
    return GENESIS_YEAR + height / blocks_per_year()


def mining_end_year() -> float:
    """The year in which the issuance of new coins stops."""
    return year_of_height(MINING_END_HEIGHT)


def total_supply_after(blocks: int) -> float:
    """Total issuance after `blocks` mined blocks (approximate)."""
    supply = 0.0
    height = 0
    remaining = blocks
    while remaining > 0:
        reward = block_reward(height)
        if reward == 0:
            break
        step = min(remaining, HALVING_INTERVAL - (height % HALVING_INTERVAL))
        supply += reward * step
        height += step
        remaining -= step
    return min(supply, MAX_SUPPLY)


def emission_schedule(max_halvings: int = 10):
    """Returns the table [(epoch, reward, issuance for the epoch)]."""
    schedule = []
    for era in range(max_halvings):
        reward = round(INITIAL_REWARD / (2 ** era), DECIMALS)
        era_emission = reward * HALVING_INTERVAL
        schedule.append((era, reward, era_emission))
    return schedule


if __name__ == "__main__":
    print(f"Интервал халвинга  : {HALVING_INTERVAL:,} блоков "
          f"(≈{halving_years():.1f} года при целевом времени блока)")
    print(f"Время блока        : {BLOCK_TIME_SECONDS / 60:.1f} мин")
    print(f"Блоков в год       : {blocks_per_year():,.0f}")
    print(f"Делимость монеты   : 1e-{DECIMALS} BHY")
    print(f"Максимум эмиссии   : {MAX_SUPPLY:,} BHY")
    print(f"Майнинг заканчивается на блоке {MINING_END_HEIGHT:,}")
    print(f"Год окончания майнинга: {mining_end_year():.0f}\n")

    print("Эпоха | Награда | Год начала эпохи")
    for era, reward, _ in emission_schedule(8):
        year = year_of_height(era * HALVING_INTERVAL)
        print(f"{era:5d} | {reward:9.4f} | {year:.0f}")

    total = sum(e for _, _, e in emission_schedule(64))
    print(f"\nИтоговая эмиссия по всем эпохам ≈ {total:,.0f} BHY")
