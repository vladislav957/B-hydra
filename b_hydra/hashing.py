"""Хеш-утилиты B-hydra с переключаемым движком.

Все хеши в проекте проходят через этот модуль. Движок SHA-2 можно переключать:

  * "hashlib"  — быстрая реализация из стандартной библиотеки (по умолчанию);
  * "pure"     — реализация SHA-256/512 «с нуля» из :mod:`b_hydra.sha2`.

Значения хешей у обоих движков ПОБИТОВО одинаковые, поэтому переключение не
влияет на консенсус (блоки, адреса, txid) — меняется только скорость. Чистый
Python в сотни раз медленнее, поэтому майнинг с движком "pure" будет медленным.

Включить наш SHA «с нуля» можно:
  * переменной окружения  BHYDRA_PURE_SHA=1
  * или вызовом           hashing.use_pure_sha(True)
"""

from __future__ import annotations

import hashlib
import os

if __name__ == "__main__" and __package__ in (None, ""):
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    __package__ = "b_hydra"

from . import sha2

# Движок по умолчанию: SHA «с нуля» (pure). Вернуть быстрый hashlib можно
# переменной окружения BHYDRA_PURE_SHA=0 или вызовом use_pure_sha(False).
_PURE = os.environ.get("BHYDRA_PURE_SHA", "1").lower() in ("1", "true", "yes", "on")


def use_pure_sha(enabled: bool = True) -> None:
    """Включить (или выключить) SHA «с нуля» во всём проекте."""
    global _PURE
    _PURE = enabled


def is_pure() -> bool:
    """True, если используется реализация SHA «с нуля»."""
    return _PURE


def backend() -> str:
    """Имя текущего движка: 'pure' или 'hashlib'."""
    return "pure" if _PURE else "hashlib"


def _to_bytes(data: "str | bytes") -> bytes:
    return data.encode("utf-8") if isinstance(data, str) else data


# --- SHA-256 -----------------------------------------------------------------
def sha256_bytes(data: "str | bytes") -> bytes:
    raw = _to_bytes(data)
    return sha2.sha256_bytes(raw) if _PURE else hashlib.sha256(raw).digest()


def sha256(data: "str | bytes") -> str:
    return sha256_bytes(data).hex()


def double_sha256(data: "str | bytes") -> bytes:
    return sha256_bytes(sha256_bytes(data))


# --- SHA-512 -----------------------------------------------------------------
def sha512_bytes(data: "str | bytes") -> bytes:
    raw = _to_bytes(data)
    return sha2.sha512_bytes(raw) if _PURE else hashlib.sha512(raw).digest()


def sha512(data: "str | bytes") -> str:
    return sha512_bytes(data).hex()


def double_sha512(data: "str | bytes") -> bytes:
    return sha512_bytes(sha512_bytes(data))


# --- RIPEMD-160 (не SHA; всегда из hashlib, с откатом) -----------------------
def ripemd160(data: "str | bytes") -> bytes:
    raw = _to_bytes(data)
    try:
        digest = hashlib.new("ripemd160")
        digest.update(raw)
        return digest.digest()
    except (ValueError, TypeError):
        return sha256_bytes(raw)[:20]


if __name__ == "__main__":
    print("движок:", backend())
    print("sha256('B-hydra') =", sha256("B-hydra"))
    print("sha512('B-hydra') =", sha512("B-hydra"))
