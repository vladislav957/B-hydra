"""
hashcash.py — Hashcash-style proof-of-work over SHA-512.

Used as a standalone PoW primitive: "mint" a stamp with a given number of
leading zeros and verify it. Block mining lives in Blockchain.py; what is
here is the general proof-of-work mechanism.
"""

import time

if __name__ == "__main__" and __package__ in (None, ""):
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    __package__ = "b_hydra"

from . import hashing


def _leading_zero_bits(digest: bytes) -> int:
    """The number of leading zero bits in the hash."""
    bits = 0
    for byte in digest:
        if byte == 0:
            bits += 8
            continue
        for i in range(7, -1, -1):
            if byte & (1 << i):
                return bits
            bits += 1
    return bits


def mint(resource: str, bits: int = 20):
    """
    Mints a Hashcash stamp for a resource at the given difficulty (in bits).

    Returns (nonce, stamp, digest_hex).
    """
    nonce = 0
    prefix = f"1:{bits}:{resource}:"
    start = time.time()
    while True:
        stamp = f"{prefix}{nonce}"
        digest = hashing.sha512_bytes(stamp)
        if _leading_zero_bits(digest) >= bits:
            return nonce, stamp, digest.hex()
        nonce += 1
        # Guard against an endless loop at extreme difficulty.
        if nonce % 5_000_000 == 0 and time.time() - start > 60:
            raise TimeoutError("Hashcash mint timed out")


def check(stamp: str, bits: int = 20, resource: str = None) -> bool:
    """Checks that the stamp is valid and has the required difficulty."""
    parts = stamp.split(":")
    if len(parts) != 4:
        return False
    _, claimed_bits, claimed_resource, _ = parts
    if resource is not None and claimed_resource != resource:
        return False
    if int(claimed_bits) < bits:
        return False
    digest = hashing.sha512_bytes(stamp)
    return _leading_zero_bits(digest) >= bits


def proof_of_work(data: str, difficulty: int = 4):
    """
    PoW by leading zeros in the hex representation (as in block mining).

    Returns (nonce, hash_hex).
    """
    target = "0" * difficulty
    nonce = 0
    while True:
        digest = hashing.sha512(f"{data}{nonce}")
        if digest.startswith(target):
            return nonce, digest
        nonce += 1


if __name__ == "__main__":
    nonce, stamp, digest = mint("b-hydra@example.com", bits=16)
    print(f"Марка: {stamp}")
    print(f"Хеш  : {digest[:32]}…")
    print(f"Nonce: {nonce}")
    print(f"Проверка: {check(stamp, bits=16)}")
