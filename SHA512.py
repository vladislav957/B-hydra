"""
SHA512.py — хеш-утилиты B-hydra.

Основной алгоритм сети — SHA-512. Здесь собраны тонкие обёртки над
стандартной библиотекой, которые используют остальные модули проекта.
"""

import hashlib


def sha512(data) -> str:
    """SHA-512 в виде hex-строки. Принимает str или bytes."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha512(data).hexdigest()


def sha512_bytes(data) -> bytes:
    """SHA-512 в виде сырых байтов."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha512(data).digest()


def double_sha512(data) -> bytes:
    """Двойной SHA-512 (используется в дереве Меркла)."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha512(hashlib.sha512(data).digest()).digest()


def ripemd160(data) -> bytes:
    """RIPEMD-160 с откатом на усечённый SHA-256, если алгоритм недоступен."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    try:
        h = hashlib.new("ripemd160")
        h.update(data)
        return h.digest()
    except (ValueError, TypeError):
        # Некоторые сборки OpenSSL не содержат ripemd160.
        return hashlib.sha256(data).digest()[:20]


if __name__ == "__main__":
    print("sha512('B-hydra') =", sha512("B-hydra"))
