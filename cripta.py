"""
cripta.py — экономика эмиссии B-hydra.

Считает награду за блок с учётом халвинга и суммарную эмиссию. Параметры берутся
из Blockchain.py, чтобы экономика всегда совпадала с правилами консенсуса:

    310 000 (интервал халвинга) * 50 (награда) * 2  =  31 000 000 (максимум).
"""

from Blockchain import INITIAL_REWARD, HALVING_INTERVAL, MAX_SUPPLY, BLOCK_TIME_SECONDS


def block_reward(height: int) -> float:
    """Награда за блок на заданной высоте (с учётом халвингов)."""
    halvings = height // HALVING_INTERVAL
    if halvings >= 64:
        return 0.0
    return INITIAL_REWARD / (2 ** halvings)


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
        reward = INITIAL_REWARD / (2 ** era)
        era_emission = reward * HALVING_INTERVAL
        schedule.append((era, reward, era_emission))
    return schedule


if __name__ == "__main__":
    print(f"Интервал халвинга : {HALVING_INTERVAL:,} блоков")
    print(f"Целевое время блока: {BLOCK_TIME_SECONDS // 60} мин")
    print(f"Максимум эмиссии   : {MAX_SUPPLY:,} BHY\n")
    print("Эпоха | Награда | Эмиссия за эпоху")
    for era, reward, emission in emission_schedule(6):
        print(f"{era:5d} | {reward:7.4f} | {emission:,.0f} BHY")
    total = sum(e for _, _, e in emission_schedule(64))
    print(f"\nИтоговая эмиссия по всем эпохам ≈ {total:,.0f} BHY")
