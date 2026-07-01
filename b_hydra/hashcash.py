"""
hashcash.py — proof-of-work по схеме Hashcash на SHA-512.

Используется как самостоятельный PoW-примитив: «отчеканить» марку с заданным
числом ведущих нулей и проверить её. Майнинг блоков реализован в Blockchain.py,
а здесь — общий механизм доказательства работы.
"""

import time

if __name__ == "__main__" and __package__ in (None, ""):
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    __package__ = "b_hydra"

from . import hashing


def _leading_zero_bits(digest: bytes) -> int:
    """Количество ведущих нулевых бит в хеше."""
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
    Чеканит Hashcash-марку для ресурса с заданной сложностью (в битах).

    Возвращает (nonce, stamp, digest_hex).
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
        # Защита от вечного цикла при экстремальной сложности.
        if nonce % 5_000_000 == 0 and time.time() - start > 60:
            raise TimeoutError("Hashcash mint timed out")


def check(stamp: str, bits: int = 20, resource: str = None) -> bool:
    """Проверяет, что марка валидна и имеет нужную сложность."""
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
    PoW по ведущим нулям в hex-представлении (как при майнинге блоков).

    Возвращает (nonce, hash_hex).
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
