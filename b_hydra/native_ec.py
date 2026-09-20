"""
native_ec.py — мост к нашей же ECDSA на C++ (`cpp/bhydra_ec_lib.cpp`).

Замер приёма одной транзакции: 23,6 мс, из них 94% — проверка подписи на
чистом Python, и только 5% — хеш. Значит ускорять надо кривую.

⚠️ Это НЕ сторонняя библиотека. Считает тот же `bhydra_ec.hpp`, что уже
обслуживает рукопожатие транспорта, — наш алгоритм, просто скомпилированный.
Чужой криптографии не добавляется.

Почему библиотека, а не команда, как у майнера: у майнера один запуск процесса
покрывает секунду работы и теряется в фоне, а здесь работы на полмиллисекунды,
и запуск процесса стоил бы дороже самой проверки. Через ctypes накладные
расходы — микросекунды.

⚠️ Включается ТОЛЬКО после self-test на живых подписях. Множество принимаемых
подписей обязано совпадать с чистым Python: разойдись они, узлы с собранной
библиотекой и без неё по-разному решали бы, какая транзакция валидна, — а это
раскол сети.
"""

import ctypes
import os
import sys

#: Явный путь к библиотеке; `off` полностью выключает нативный путь.
LIB_ENV = "BHYDRA_EC_LIB"

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_cached = False
_library = None


def _candidates(path=None):
    """Где искать библиотеку: явный путь, потом рядом с проектом."""
    if path:
        return [path]
    given = os.environ.get(LIB_ENV)
    if given:
        return [] if str(given).lower() in ("off", "0", "no", "none") else [given]
    names = ["bhydra_ec.dll"] if sys.platform.startswith("win") \
        else ["libbhydra_ec.so", "libbhydra_ec.dylib"]
    return [os.path.join(_ROOT, name) for name in names] + names


def load(path=None):
    """Загружает библиотеку и объявляет типы. None — если её нет.

    `argtypes` обязательны: без них ctypes передаст указатели как int, и
    проверка начнёт читать не оттуда — молчаливая порча вместо отказа.
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
            # ⚠️ `sign` появился позже `verify`: у СТАРОЙ собранной библиотеки
            # его нет, и требовать его здесь значило бы отвергнуть её целиком,
            # потеряв уже работающее ускорение проверки. Поэтому подпись
            # необязательна — её наличие проверяет `has_sign()`.
            if hasattr(library, "bhydra_ec_sign"):
                library.bhydra_ec_sign.argtypes = [ctypes.c_char_p,
                                                   ctypes.c_char_p,
                                                   ctypes.c_char_p]
                library.bhydra_ec_sign.restype = ctypes.c_int
        except AttributeError:
            continue          # библиотека есть, но это не наша
        return library
    return None


def has_sign(library) -> bool:
    """Умеет ли эта сборка подписывать (а не только проверять)."""
    return library is not None and hasattr(library, "bhydra_ec_sign")


def verify_core(library, x: int, y: int, z: int, r: int, s: int) -> bool:
    """Уравнение ECDSA нативно. Интерфейс тот же, что у `wallet._VERIFY_CORE`."""
    try:
        return library.bhydra_ec_verify(
            x.to_bytes(32, "big"), y.to_bytes(32, "big"), z.to_bytes(32, "big"),
            r.to_bytes(32, "big"), s.to_bytes(32, "big")) == 1
    except (OverflowError, ValueError):
        # Число не влезло в 32 байта — такой подписи быть не может, и это
        # ровно тот же ответ, что дал бы чистый Python.
        return False


def sign_core(library, private: int, z: int):
    """Подпись ECDSA нативно → (r, s) или None.

    ⚠️ Хеш сюда приходит ПОСЧИТАННЫМ (как и в `verify_core`): в Python он уже
    есть, и считать SHA-512 второй раз в C++ было бы чистой потерей.

    ⚠️ Нонс выводится по RFC 6979 из (private, z) — той же цепочкой HMAC-SHA512,
    что и в чистом Python, поэтому подпись совпадает БАЙТ В БАЙТ. Иначе один и
    тот же перевод получал бы разные txid у узлов с библиотекой и без неё.
    Совпадение проверяется на живых подписях перед включением бэкенда.
    """
    out = ctypes.create_string_buffer(64)
    try:
        ok = library.bhydra_ec_sign(private.to_bytes(32, "big"),
                                    z.to_bytes(32, "big"), out)
    except (OverflowError, ValueError):
        return None               # число не влезло в 32 байта — не наш случай
    if ok != 1:
        return None
    raw = out.raw
    return int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")


def default():
    """Готовая библиотека для этой машины или None. Результат запоминается."""
    global _cached, _library
    if _cached:
        return _library
    _cached = True
    library = load()
    if library is None:
        _library = None
        return None
    # Своя проверка библиотеки (подписала и проверила сама себя) — до того,
    # как Python начнёт сверять её с эталоном.
    _library = library if library.bhydra_ec_selftest() == 0 else None
    return _library


def reset():
    """Забыть найденную библиотеку (для тестов и после пересборки)."""
    global _cached, _library
    _cached = False
    _library = None
