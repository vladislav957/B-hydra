"""
native_miner.py — the bridge to the native miner (`cpp/bhydra_miner.cpp`).

Nonce search is the one place in the project where Python does a LOT of the
computing itself: millions of SHA-512 in a row, all on one thread because of
the GIL. The native miner does the same thing on every core.

It works in time SLICES: Python asks it to "search for a second", gets the
result and decides whether to continue. That preserves everything the loop
was rewritten for — the ability to abandon a block when a peer found theirs
first, and the speed report. Handing over control for good is not an option:
the node would go deaf again for the duration of the mining.

⚠️ The native miner's result is VERIFIED (`Block._mine_native`): the hash is
recomputed by our own code and checked against the threshold. An external
program is not taken at its word here — otherwise a bug in it would sail
through and surface later as a block the network rejects.

Not built? No problem: `default()` returns None and mining falls back to
Python.
"""

import json
import os
import shutil
import subprocess

#: The binary's path can be given explicitly; `off`/`0` disables the native
#: path entirely (handy for tests and for speed comparisons).
MINER_ENV = "BHYDRA_MINER"
#: How many cores to give to the search. Empty = all but one.
THREADS_ENV = "BHYDRA_MINER_THREADS"
BINARY_NAME = "bhydra_miner"
#: How many seconds one search slice lasts. Smaller = faster reaction to
#: somebody else's block, larger = less process-startup overhead.
SLICE_SECONDS = 1.0

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_cached = False
_default = None


class NativeMiner:
    """Runs `bhydra_miner` and parses its answer."""

    def __init__(self, path, threads=0, slice_seconds=SLICE_SECONDS):
        self.path = path
        self.threads = int(threads)
        self.slice_seconds = float(slice_seconds)

    def selftest(self) -> bool:
        return bool(self._run("selftest").get("ok"))

    def mine(self, prefix_hex, target_hex, start_nonce, seconds=None):
        """One search slice. None if the binary did not deliver."""
        answer = self._run("mine", prefix_hex, target_hex, int(start_nonce),
                           self.threads, seconds or self.slice_seconds)
        if "error" in answer or "attempts" not in answer:
            return None
        return answer

    def benchmark(self, seconds=2.0, threads=None):
        """Search speed, in hashes per second."""
        answer = self._run("bench", seconds,
                           self.threads if threads is None else threads)
        elapsed = float(answer.get("seconds") or 0)
        return (answer.get("attempts", 0) / elapsed) if elapsed else 0.0

    def _run(self, *args):
        try:
            result = subprocess.run(
                [self.path, *[str(a) for a in args]],
                capture_output=True, text=True,
                # The slice plus a generous allowance for startup: a hung binary must
                # not stall the node forever.
                timeout=max(30.0, self.slice_seconds * 10))
        except (OSError, subprocess.SubprocessError):
            return {}
        try:
            answer = json.loads(result.stdout or "{}")
        except ValueError:
            return {}
        return answer if isinstance(answer, dict) else {}


def find(path=None):
    """Locates the binary: explicit path -> env var -> PATH -> project root."""
    candidate = path or os.environ.get(MINER_ENV)
    if candidate:
        if str(candidate).lower() in ("off", "0", "no", "none"):
            return None
        return candidate if os.path.exists(candidate) else None
    found = shutil.which(BINARY_NAME)
    if found:
        return found
    local = os.path.join(_ROOT, BINARY_NAME)
    return local if os.path.exists(local) else None


def default_threads() -> int:
    """How many cores to give the search: all but one.

    ⚠️ ALL BUT ONE specifically, not all of them. The search pins a core at
    100% with no pauses, and on an old dual-core laptop "all cores" means an
    unresponsive interface and a hot chassis — the machine is busy mining
    rather than serving its owner. One core is left to the system and the
    window.

    ⚠️ Zero is NOT allowed here: the native miner reads 0 as "decide for
    yourself", and then it takes `hardware_concurrency()`, i.e. every core —
    exactly what we are avoiding. So the number is always explicit and never
    below one.
    """
    override = os.environ.get(THREADS_ENV)
    if override:
        try:
            return max(1, int(override))
        except ValueError:
            pass                       # garbage in the variable — behave as if it were unset
    return max(1, (os.cpu_count() or 2) - 1)


def default():
    """A ready miner for this machine, or None. The result is memoised.

    It is not only the file's presence that is checked but `selftest` too: a
    broken or foreign binary with the same name must not silently become the
    miner.
    """
    global _cached, _default
    if _cached:
        return _default
    _cached = True
    path = find()
    if path is None:
        _default = None
        return None
    miner = NativeMiner(path, threads=default_threads())
    _default = miner if miner.selftest() else None
    return _default


def reset():
    """Forget the miner that was found (for tests and after a rebuild)."""
    global _cached, _default
    _cached = False
    _default = None
