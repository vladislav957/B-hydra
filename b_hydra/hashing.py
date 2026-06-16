"""Хеш-утилиты B-hydra поверх стандартной библиотеки.

Основной алгоритм сети — SHA-512; здесь собраны тонкие, типизированные обёртки,
которыми пользуются остальные модули пакета. Реализация SHA-2 «с нуля» (без
hashlib) находится в :mod:`b_hydra.sha2`.
"""

from __future__ import annotations

import hashlib

Data = "str | bytes"


def _to_bytes(data: "str | bytes") -> bytes:
    return data.encode("utf-8") if isinstance(data, str) else data


def sha256(data: "str | bytes") -> str:
    """SHA-256 в виде hex-строки."""
    return hashlib.sha256(_to_bytes(data)).hexdigest()


def sha256_bytes(data: "str | bytes") -> bytes:
    """SHA-256 в виде сырых байтов."""
    return hashlib.sha256(_to_bytes(data)).digest()


def double_sha256(data: "str | bytes") -> bytes:
    """Двойной SHA-256."""
    return hashlib.sha256(hashlib.sha256(_to_bytes(data)).digest()).digest()


def sha512(data: "str | bytes") -> str:
    """SHA-512 в виде hex-строки."""
    return hashlib.sha512(_to_bytes(data)).hexdigest()


def sha512_bytes(data: "str | bytes") -> bytes:
    """SHA-512 в виде сырых байтов."""
    return hashlib.sha512(_to_bytes(data)).digest()


def double_sha512(data: "str | bytes") -> bytes:
    """Двойной SHA-512 (используется в дереве Меркла)."""
    return hashlib.sha512(hashlib.sha512(_to_bytes(data)).digest()).digest()


def ripemd160(data: "str | bytes") -> bytes:
    """RIPEMD-160 с откатом на усечённый SHA-256, если алгоритм недоступен."""
    raw = _to_bytes(data)
    try:
        digest = hashlib.new("ripemd160")
        digest.update(raw)
        return digest.digest()
    except (ValueError, TypeError):
        # Некоторые сборки OpenSSL не содержат ripemd160.
        return hashlib.sha256(raw).digest()[:20]


if __name__ == "__main__":
    print("sha256('B-hydra') =", sha256("B-hydra"))
    print("sha512('B-hydra') =", sha512("B-hydra"))
