"""Calibrating the fork target for a specific machine.

    python -m b_hydra.powcalib                # measure and show what to set
    python -m b_hydra.powcalib --threads 1    # as many threads as you will give
    python -m b_hydra.powcalib --hashrate 1.3e6   # know the number already

⚠️ WHY. `POW_FORK_TARGET` is a CONSENSUS CONSTANT: the target that takes
effect at the fork height. Measuring the speed at node startup and plugging it
in is impossible in principle — every machine would arrive at its own number,
and therefore its own target and its own chain. So the measurement is taken
ONCE by a person, and the result is what lands in `blockchain.py`.

⚠️ ERR ON THE HIGH SIDE. Overstate the hashrate and blocks come in slower
than the target, issuance lags, no harm done. Understate it and blocks pour
in faster than the target — which is exactly the issuance blowout the fork
exists to prevent. That is why the reported value is rounded UP.

⚠️ What gets measured is the engine the machine will actually mine with:
first the native C++ miner (the one used in production), and only if it is
absent, Python. Measuring with pure Python while mining with C++ would
understate the hashrate a thousandfold and produce a block every few seconds.
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
    """Pure-Python speed — the fallback when C++ has not been built."""
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
    """-> (hashes/s, which engine). Native if one is available."""
    miner = native_miner.default()
    if miner is not None:
        # `benchmark` hands back hashes per second already, not a raw answer.
        rate = miner.benchmark(seconds=seconds, threads=threads)
        if rate > 0:
            used = threads or "все"
            return rate, f"C++ ({used} потоков)"
    return _python_hashrate(seconds), f"чистый Python ({hashing.backend()})"


def target_for(hashrate: float) -> int:
    """The target at which a block takes roughly TARGET_BLOCK_TIME to find."""
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

    # ⚠️ Rounded UP, to two significant digits: erring high is safe, erring
    # low is not (see the module header).
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
