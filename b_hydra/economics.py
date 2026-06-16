"""
cripta.py — экономика эмиссии B-hydra.

Считает награду за блок (с халвингом и округлением до делимости монеты),
суммарную эмиссию и — главное — год окончания майнинга. Параметры берутся
из Blockchain.py, чтобы экономика всегда совпадала с правилами консенсуса:

    310 000 (интервал халвинга) * 50 (награда) * 2  =  31 000 000 (максимум).

Выпуск новых монет завершается примерно в TARGET_END_YEAR (3010) — время блока
подобрано так, чтобы эта цель сошлась с потолком 31 млн и наградой 50.
"""

from .blockchain import (
    INITIAL_REWARD, HALVING_INTERVAL, MAX_SUPPLY, DECIMALS,
    BLOCK_TIME_SECONDS, SECONDS_PER_YEAR, GENESIS_YEAR,
    MINING_END_HEIGHT,
)


def block_reward(height: int) -> float:
    """Награда за блок на заданной высоте (с округлением до делимости)."""
    halvings = height // HALVING_INTERVAL
    if halvings >= 64:
        return 0.0
    return round(INITIAL_REWARD / (2 ** halvings), DECIMALS)


def blocks_per_year() -> float:
    """Сколько блоков добывается за год при текущем времени блока."""
    return SECONDS_PER_YEAR / BLOCK_TIME_SECONDS


def year_of_height(height: int) -> float:
    """Календарный год, к которому будет добыт блок с данной высотой."""
    return GENESIS_YEAR + height / blocks_per_year()


def mining_end_year() -> float:
    """Год, в котором прекращается выпуск новых монет."""
    return year_of_height(MINING_END_HEIGHT)


def total_supply_after(blocks: int) -> float:
    """Суммарная эмиссия после `blocks` добытых блоков (приближённо)."""
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
    """Возвращает таблицу [(эпоха, награда, эмиссия за эпоху)]."""
    schedule = []
    for era in range(max_halvings):
        reward = round(INITIAL_REWARD / (2 ** era), DECIMALS)
        era_emission = reward * HALVING_INTERVAL
        schedule.append((era, reward, era_emission))
    return schedule


if __name__ == "__main__":
    print(f"Интервал халвинга  : {HALVING_INTERVAL:,} блоков")
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
