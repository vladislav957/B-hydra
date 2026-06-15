"""
SHA256.py — вспомогательные SHA-256 утилиты B-hydra.

Сеть работает на SHA-512, но SHA-256 используется в адресах и контрольных
суммах, поэтому оставлена отдельная аккуратная обёртка.
"""

import hashlib


def sha256(data) -> str:
    """SHA-256 в виде hex-строки. Принимает str или bytes."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def sha256_bytes(data) -> bytes:
    """SHA-256 в виде сырых байтов."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).digest()


def double_sha256(data) -> bytes:
    """Двойной SHA-256."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(hashlib.sha256(data).digest()).digest()


if __name__ == "__main__":
    print("sha256('B-hydra') =", sha256("B-hydra"))
