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

from . import ripemd
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


# --- RIPEMD-160 --------------------------------------------------------------
# Раньше при отсутствии RIPEMD-160 в сборке OpenSSL сюда молча подставлялся
# sha256(...)[:20] — и узлы с разными сборками Python выводили бы РАЗНЫЕ адреса
# из одного ключа. Теперь есть своя реализация (:mod:`b_hydra.ripemd`), поэтому
# подменять алгоритм не нужно: hashlib берётся только как ускоритель и только
# после проверки, что он даёт те же байты.
def _native_ripemd160_available() -> bool:
    """True, если hashlib умеет RIPEMD-160 и совпадает с нашей реализацией."""
    try:
        hashlib.new("ripemd160")
    except (ValueError, TypeError):
        return False
    samples = (b"", b"abc", b"B-hydra", bytes(range(64)), b"a" * 200)
    return all(hashlib.new("ripemd160", s).digest() == ripemd.ripemd160_bytes(s)
               for s in samples)


_NATIVE_RIPEMD = _native_ripemd160_available()


def ripemd160(data: "str | bytes") -> bytes:
    raw = _to_bytes(data)
    # Тот же переключатель, что и у SHA: по умолчанию считаем сами.
    if _PURE or not _NATIVE_RIPEMD:
        return ripemd.ripemd160_bytes(raw)
    return hashlib.new("ripemd160", raw).digest()


def ripemd_backend() -> str:
    """Имя движка RIPEMD-160: 'pure' или 'hashlib'."""
    return "hashlib" if (not _PURE and _NATIVE_RIPEMD) else "pure"


if __name__ == "__main__":
    print("движок:", backend())
    print("sha256('B-hydra') =", sha256("B-hydra"))
    print("sha512('B-hydra') =", sha512("B-hydra"))
