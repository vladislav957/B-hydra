"""
native_ec.py — the bridge to our own ECDSA in C++ (`cpp/bhydra_ec_lib.cpp`).

Measured on accepting one transaction: 23.6 ms, of which 94% is signature
verification in pure Python and only 5% is the hash. So it is the curve that
needs speeding up.

⚠️ This is NOT a third-party library. It runs the same `bhydra_ec.hpp` that
already serves the transport handshake — our algorithm, merely compiled. No
foreign cryptography is added.

Why a library rather than a command, the way the miner works: for the miner
one process launch covers a second of work and vanishes into the background,
whereas here the work takes half a millisecond and launching a process would
cost more than the verification itself. Through ctypes the overhead is
microseconds.

⚠️ It is enabled ONLY after a self-test on live signatures. The set of
accepted signatures must match pure Python exactly: were they to diverge,
nodes with the library and without it would disagree about which transaction
is valid — and that is a network split.
"""

import ctypes
import os
import sys

#: An explicit path to the library; `off` disables the native path entirely.
LIB_ENV = "BHYDRA_EC_LIB"

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_cached = False
_library = None


def _candidates(path=None):
    """Where to look for the library: the explicit path, then next to the project."""
    if path:
        return [path]
    given = os.environ.get(LIB_ENV)
    if given:
        return [] if str(given).lower() in ("off", "0", "no", "none") else [given]
    names = ["bhydra_ec.dll"] if sys.platform.startswith("win") \
        else ["libbhydra_ec.so", "libbhydra_ec.dylib"]
    return [os.path.join(_ROOT, name) for name in names] + names


def load(path=None):
    """Loads the library and declares the types. None if it is absent.

    `argtypes` are mandatory: without them ctypes passes pointers as int and
    the verification starts reading from the wrong place — silent corruption
    instead of a refusal.
    """
    for candidate in _candidates(path):
        try:
            library = ctypes.CDLL(candidate)
        except OSError:
            continue
        try:
            library.bhydra_ec_verify.argtypes = [ctypes.c_char_p] * 5
            library.bhydra_ec_verify.restype = ctypes.c_int
            library.bhydra_ec_selftest.argtypes = []
            library.bhydra_ec_selftest.restype = ctypes.c_int
            # ⚠️ `sign` arrived after `verify`: an OLD build of the library does
            # not have it, and demanding it here would mean rejecting that build
            # outright, losing the verification speedup that already works. So
            # signing is optional — `has_sign()` reports whether it is there.
            if hasattr(library, "bhydra_ec_sign"):
                library.bhydra_ec_sign.argtypes = [ctypes.c_char_p,
                                                   ctypes.c_char_p,
                                                   ctypes.c_char_p]
                library.bhydra_ec_sign.restype = ctypes.c_int
        except AttributeError:
            continue          # the library exists, but it is not ours
        return library
    return None


def has_sign(library) -> bool:
    """Whether this build can sign (and not merely verify)."""
    return library is not None and hasattr(library, "bhydra_ec_sign")


def verify_core(library, x: int, y: int, z: int, r: int, s: int) -> bool:
    """The ECDSA equation, natively. Same interface as `wallet._VERIFY_CORE`."""
    try:
        return library.bhydra_ec_verify(
            x.to_bytes(32, "big"), y.to_bytes(32, "big"), z.to_bytes(32, "big"),
            r.to_bytes(32, "big"), s.to_bytes(32, "big")) == 1
    except (OverflowError, ValueError):
        # The number does not fit in 32 bytes — no such signature can exist,
        # and this is exactly the answer pure Python would give.
        return False


def sign_core(library, private: int, z: int):
    """ECDSA signing, natively -> (r, s) or None.

    ⚠️ The hash arrives here ALREADY COMPUTED (as in `verify_core`): Python
    has it already, and computing SHA-512 a second time in C++ would be pure
    waste.

    ⚠️ The nonce is derived per RFC 6979 from (private, z) — the same
    HMAC-SHA512 chain as in pure Python, so the signature matches BYTE FOR
    BYTE. Otherwise one and the same transfer would get different txids on
    nodes with the library and without it. The match is checked on live
    signatures before the backend is enabled.
    """
    out = ctypes.create_string_buffer(64)
    try:
        ok = library.bhydra_ec_sign(private.to_bytes(32, "big"),
                                    z.to_bytes(32, "big"), out)
    except (OverflowError, ValueError):
        return None               # the number does not fit in 32 bytes — not our case
    if ok != 1:
        return None
    raw = out.raw
    return int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")


def default():
    """A ready library for this machine, or None. The result is memoised."""
    global _cached, _library
    if _cached:
        return _library
    _cached = True
    library = load()
    if library is None:
        _library = None
        return None
    # The library's own check (it signed and verified for itself) — before
    # Python starts comparing it against the reference.
    _library = library if library.bhydra_ec_selftest() == 0 else None
    return _library


def reset():
    """Forget the library that was found (for tests and after a rebuild)."""
    global _cached, _library
    _cached = False
    _library = None
