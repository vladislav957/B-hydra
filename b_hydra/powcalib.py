"""Калибровка цели развилки под конкретную машину.

    python -m b_hydra.powcalib                # замерить и показать, что ставить
    python -m b_hydra.powcalib --threads 1    # столько потоков, сколько отдашь
    python -m b_hydra.powcalib --hashrate 1.3e6   # уже знаешь число — посчитать

⚠️ ЗАЧЕМ. `POW_FORK_TARGET` — КОНСТАНТА КОНСЕНСУСА: цель, которая встаёт на
высоте развилки. Померить скорость при старте узла и подставить её нельзя в
принципе — у каждой машины получилось бы своё число, а значит своя цель и своя
цепочка. Поэтому замер делается ОДИН раз человеком, а в `blockchain.py`
попадает результат.

⚠️ ОШИБАТЬСЯ НАДО В БОЛЬШУЮ СТОРОНУ. Завысил хешрейт — блоки идут реже цели,
эмиссия отстаёт, ничего страшного. Занизил — блоки посыплются чаще цели, а
это ровно тот выброс эмиссии, ради которого развилка и вводится. Поэтому
показанное значение округляется ВВЕРХ.

⚠️ Замеряется тот движок, которым машина и будет майнить: сначала нативный
майнер на C++ (он же используется в бою), и только если его нет — Python.
Мерить чистым Python, а майнить на C++ значило бы занизить хешрейт в тысячу
раз и получить блоки раз в несколько секунд.
"""

import argparse
import math
import os
import sys
import time

from . import hashing, native_miner
from .blockchain import (POW_FORK_HASHRATE, POW_FORK_HEIGHT, TARGET_BLOCK_TIME,
                         _HASH_SPACE)

DEFAULT_SECONDS = 5.0


def _python_hashrate(seconds: float) -> float:
    """Скорость чистого Python — запасной путь, если C++ не собран."""
    from .blockchain import Block

    block = Block(1, [], "0" * 128, target=0)
    base = hashing.sha512_hasher(block.header_prefix())
    done = 0
    started = time.monotonic()
    while time.monotonic() - started < seconds:
        for _ in range(2000):
            block.nonce += 1
            block._nonce_digest(base)
        done += 2000
    return done / (time.monotonic() - started)


def measure(seconds=DEFAULT_SECONDS, threads=0):
    """→ (хеш/с, каким движком). Нативный, если он есть."""
    miner = native_miner.default()
    if miner is not None:
        # `benchmark` отдаёт уже готовые хеши в секунду, а не сырой ответ.
        rate = miner.benchmark(seconds=seconds, threads=threads)
        if rate > 0:
            used = threads or "все"
            return rate, f"C++ ({used} потоков)"
    return _python_hashrate(seconds), f"чистый Python ({hashing.backend()})"


def target_for(hashrate: float) -> int:
    """Цель, при которой блок ищется примерно TARGET_BLOCK_TIME."""
    hashes = int(hashrate * TARGET_BLOCK_TIME)
    return _HASH_SPACE // max(1, hashes)


def describe(target: int) -> str:
    hashes = _HASH_SPACE // target
    zeros = 129 - len(f"{target:x}")
    return (f"{hashes:,} хешей в среднем, «сложность» {zeros} "
            f"(так её покажет обозреватель)")


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="b-hydra-powcalib",
        description="Калибровка POW_FORK_HASHRATE под эту машину")
    parser.add_argument("--seconds", type=float, default=DEFAULT_SECONDS)
    parser.add_argument("--threads", type=int, default=0,
                        help="0 — все ядра; на старом ноутбуке ставь на одно меньше")
    parser.add_argument("--hashrate", type=float,
                        help="не мерить, а посчитать для этого числа")
    args = parser.parse_args(argv)

    if args.hashrate:
        rate, engine = args.hashrate, "задано вручную"
    else:
        print(f"Замер {args.seconds:g} с…", file=sys.stderr)
        rate, engine = measure(args.seconds, args.threads)

    # ⚠️ Округление ВВЕРХ, до двух значащих цифр: ошибка в большую сторону
    # безопасна, в меньшую — нет (см. шапку модуля).
    step = 10 ** (math.floor(math.log10(rate)) - 1)
    safe = math.ceil(rate / step) * step

    print(f"\nДвижок        : {engine}")
    print(f"Замер         : {rate:,.0f} хеш/с")
    print(f"С запасом вверх: {safe:,.0f} хеш/с")
    print(f"\nСейчас в blockchain.py: POW_FORK_HASHRATE = {POW_FORK_HASHRATE:,}")
    print(f"  → {describe(target_for(POW_FORK_HASHRATE))}")
    print(f"  → на этой машине блок искался бы "
          f"{_HASH_SPACE // target_for(POW_FORK_HASHRATE) / rate / 60:.1f} мин "
          f"(цель {TARGET_BLOCK_TIME / 60:.1f})")

    print(f"\nПод эту машину поставить:")
    print(f"    POW_FORK_HASHRATE = {int(safe):_}".replace("_", "_"))
    print(f"  → {describe(target_for(safe))}")

    if abs(safe - POW_FORK_HASHRATE) / POW_FORK_HASHRATE > 0.25:
        print(f"\n⚠️ Расхождение больше четверти — стоит поправить константу "
              f"ДО того, как цепочка дорастёт до высоты {POW_FORK_HEIGHT}.")
        print("⚠️ Это правило сети: поменяешь после развилки — уже добытые "
              "послефорковые блоки станут невалидными.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
