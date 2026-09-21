"""
native_xmss.py — мост к нативной генерации листьев XMSS (`cpp/bhydra_xmss_lib.cpp`).

Публичный ключ XMSS ЕСТЬ корень дерева Меркла над всеми 2^h листьями, поэтому
каждый лист обязан быть посчитан хотя бы раз — это неустранимо, и обход BDS
здесь не помогает вовсе (он удешевил ПОДПИСЬ, а не генерацию). Один лист — это
len·W + 1 хешей: 1073 в режиме P256 и 2097 в P512.

Замер на этой машине (4 ядра, режим P256, один лист):

    чистый Python-SHA      292,23 мс     h=16 — 308 минут
    hashlib (OpenSSL)        0,98 мс     h=16 — 1,08 минуты
    C++, один поток          0,39 мс     h=16 — 0,42 минуты
    C++, три потока          0,13 мс     h=16 — 11 секунд

⚠️ ВЫВОД ЗДЕСЬ ДРУГОЙ, ЧЕМ У МАЙНЕРА, и это стоит понимать. У майнера C++ в один
поток оказался ВРОВЕНЬ с `hashlib`: там один огромный хеш за другим, и `hashlib`
— это OpenSSL, обогнать который своим SHA не выйдет. Здесь же C++ в один поток
быстрее `hashlib` в 2,5 раза, потому что лист — это тысяча КРОШЕЧНЫХ хешей, и
время съедает не SHA, а накладные расходы интерпретатора на каждый вызов:
создание объекта, аллокация bytes, возврат значения. Их нативный код убирает
целиком, ещё до всякого параллелизма.

⚠️ Листья берутся ПАЧКАМИ, а не все разом (`CHUNK`): генерация h=20 идёт
минутами, и между пачками Python показывает прогресс и проверяет, не отменил ли
человек создание кошелька. Та же причина, по которой нативный майнер работает
срезами по времени.

⚠️ Ядер берётся все КРОМЕ ОДНОГО — правило общее с майнером
(`native_miner.default_threads`). Замер это подтвердил: на четырёх ядрах три
потока дали 0,13 мс на лист, а четыре — 0,16, то есть ХУЖЕ.

⚠️ Байты обязаны совпадать с чистым Python ТОЧНО, а не «быть правдоподобными».
Корень дерева — это публичный ключ, он же часть гибридного адреса (версия
0x2f): ядро с чужим SHA дало бы другой адрес, и монеты ушли бы на адрес, от
которого ни у кого нет ключа. Сверку делает `pqcrypto._selftest_leaves` перед
включением бэкенда; сама библиотека вдобавок проверяет себя векторами NIST.

Не собрана — не беда: `default()` вернёт None, и листья посчитает Python.
"""

import ctypes
import os
import sys

#: Явный путь к библиотеке; `off` полностью выключает нативный путь.
LIB_ENV = "BHYDRA_XMSS_LIB"

#: Сколько листьев берём за один вызов. Между пачками Python снова получает
#: управление — отчёт о прогрессе и возможность отменить генерацию.
CHUNK = 2048

#: Имя режима из `pqcrypto` → число, которое понимает библиотека.
_ALG = {"sha256": 256, "sha512": 512}

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
    names = ["bhydra_xmss.dll"] if sys.platform.startswith("win") \
        else ["libbhydra_xmss.so", "libbhydra_xmss.dylib"]
    return [os.path.join(_ROOT, name) for name in names] + names


def load(path=None):
    """Загружает библиотеку и объявляет типы. None — если её нет.

    ⚠️ `argtypes` обязательны. Без них ctypes передаст указатели как int, и
    библиотека начнёт писать листья не туда — молчаливая порча памяти вместо
    честного отказа. У `count` и `start` тип тоже важен: это uint32, а Python
    отдал бы сколь угодно большое число.
    """
    for candidate in _candidates(path):
        try:
            library = ctypes.CDLL(candidate)
        except OSError:
            continue
        try:
            library.bhydra_xmss_selftest.argtypes = []
            library.bhydra_xmss_selftest.restype = ctypes.c_int
            library.bhydra_xmss_leaf_size.argtypes = [ctypes.c_int]
            library.bhydra_xmss_leaf_size.restype = ctypes.c_int
            library.bhydra_xmss_leaves.argtypes = [
                ctypes.c_char_p, ctypes.c_int, ctypes.c_int,
                ctypes.c_uint32, ctypes.c_uint32, ctypes.c_int, ctypes.c_char_p]
            library.bhydra_xmss_leaves.restype = ctypes.c_int
        except AttributeError:
            continue              # библиотека есть, но это не наша
        return library
    return None


def default_threads() -> int:
    """Сколько ядер отдать генерации: все, кроме одного.

    Правило общее с нативным майнером, и по той же причине: генерация грузит
    ядра на 100% минутами, и «все ядра» означают неотзывчивое окно ровно в тот
    момент, когда человек создаёт кошелёк и ждёт ответа.
    """
    from .native_miner import default_threads as miner_threads
    return miner_threads()


def leaf_size(library, alg: str) -> int:
    """Размер листа в байтах для режима. 0 — режим библиотеке неизвестен."""
    code = _ALG.get(alg)
    return 0 if code is None else int(library.bhydra_xmss_leaf_size(code))


def leaves(library, seed: bytes, alg: str, start: int, count: int,
           threads: int | None = None):
    """Листья [start, start+count) списком bytes. None — библиотека отказала.

    Отказ (чужой режим, слишком длинный seed, непройденная самопроверка) — это
    не повод уронить генерацию: вызывающий досчитает на Python.
    """
    code = _ALG.get(alg)
    if code is None or count <= 0:
        return None
    size = int(library.bhydra_xmss_leaf_size(code))
    if size <= 0:
        return None
    if threads is None:
        threads = default_threads()
    buffer = ctypes.create_string_buffer(count * size)
    ok = library.bhydra_xmss_leaves(seed, len(seed), code,
                                    ctypes.c_uint32(start),
                                    ctypes.c_uint32(count),
                                    int(threads), buffer)
    if ok != 1:
        return None
    raw = buffer.raw[:count * size]
    return [raw[k * size:(k + 1) * size] for k in range(count)]


def default():
    """Готовая библиотека для этой машины или None. Результат запоминается.

    Проверяется не только наличие файла, но и самопроверка хешей: библиотека с
    тем же именем, но чужим SHA, не должна молча начать считать адреса.
    """
    global _cached, _library
    if _cached:
        return _library
    _cached = True
    library = load()
    if library is None:
        _library = None
        return None
    _library = library if library.bhydra_xmss_selftest() == 1 else None
    return _library


def reset():
    """Забыть найденную библиотеку (для тестов и после пересборки)."""
    global _cached, _library
    _cached = False
    _library = None
