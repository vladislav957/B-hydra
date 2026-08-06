"""Наша же ECDSA на C++ как ускоритель проверки подписи.

Замер приёма транзакции: 15,5 мс, из них проверка подписи 12,2 мс — то есть
почти всё. Хеш там 5%. `cpp/bhydra_ec_lib.cpp` считает то же уравнение из
`bhydra_ec.hpp` (тот же код, что обслуживает рукопожатие транспорта), поэтому
чужой криптографии не добавляется — наш алгоритм просто скомпилирован.

⚠️ Главная проверка здесь — НЕ скорость, а ОДИНАКОВОЕ МНОЖЕСТВО принимаемых
подписей. Разойдись оно, узлы с собранной библиотекой и без неё по-разному
решали бы, какая транзакция валидна: одни приняли бы блок, другие отвергли —
раскол сети на ровном месте.

Тесты пропускаются, если нет компилятора C++.
"""

import os
import shutil
import subprocess

import pytest

from b_hydra import native_ec, wallet
from b_hydra.wallet import Wallet, _hash_to_int, generate_wallet

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(ROOT, "cpp", "bhydra_ec_lib.cpp")

COMPILER = None
for candidate in ("g++", "clang++"):
    if shutil.which(candidate):
        COMPILER = candidate
        break


@pytest.fixture(scope="module")
def library(tmp_path_factory):
    if COMPILER is None:
        pytest.skip("нет компилятора C++")
    out = str(tmp_path_factory.mktemp("ec") / "libbhydra_ec.so")
    result = subprocess.run(
        [COMPILER, "-O2", "-std=c++17", "-shared", "-fPIC",
         "-I", os.path.join(ROOT, "cpp"), "-o", out, SOURCE],
        capture_output=True, text=True, timeout=600)
    assert result.returncode == 0, result.stderr
    loaded = native_ec.load(out)
    assert loaded is not None, "библиотека собралась, но не загрузилась"
    return loaded


def _parts(w: Wallet, message: bytes):
    """(x, y, z, r, s) — ровно то, что получает ядро проверки."""
    signature = bytes.fromhex(w.sign(message))
    public = w.public_key_bytes
    return (int.from_bytes(public[1:33], "big"),
            int.from_bytes(public[33:65], "big"),
            _hash_to_int(message),
            int.from_bytes(signature[:32], "big"),
            int.from_bytes(signature[32:], "big"))


# --- Сама библиотека -----------------------------------------------------------
def test_library_passes_its_own_selftest(library):
    """Подписала и проверила сама себя, испорченное сообщение отвергла."""
    assert library.bhydra_ec_selftest() == 0


def test_missing_library_is_not_an_error():
    """Не собрана — просто нет, и проверка идёт на Python."""
    assert native_ec.load("/несуществующая/libbhydra_ec.so") is None


def test_a_foreign_library_is_refused(tmp_path):
    """Чужая библиотека без наших функций не должна стать бэкендом."""
    import ctypes.util

    libm = ctypes.util.find_library("m")
    if not libm:
        pytest.skip("нет системной libm для проверки")
    assert native_ec.load(libm) is None


def test_switch_off_by_environment(monkeypatch):
    monkeypatch.setenv(native_ec.LIB_ENV, "off")
    native_ec.reset()
    try:
        assert native_ec.load() is None
        assert native_ec.default() is None
    finally:
        native_ec.reset()


# --- Совпадение с эталоном (главное) -------------------------------------------
def test_valid_signatures_are_accepted_by_both(library):
    """Что принимает Python — обязана принять и библиотека."""
    for _ in range(12):
        w = generate_wallet()
        x, y, z, r, s = _parts(w, b"b-hydra")
        assert native_ec.verify_core(library, x, y, z, r, s) is True


def test_tampered_payload_is_refused_by_both(library):
    """И наоборот: подделку обязаны отвергнуть оба.

    Ядро, отвечающее «да» на всё, прошло бы проверку из одной половины —
    поэтому обе половины проверяются отдельно.
    """
    for _ in range(12):
        w = generate_wallet()
        x, y, _z, r, s = _parts(w, b"b-hydra")
        bad = _hash_to_int("подделка".encode("utf-8"))
        assert native_ec.verify_core(library, x, y, bad, r, s) is False


@pytest.mark.parametrize("field", ["x", "y", "r", "s"])
def test_broken_signature_is_refused(library, field):
    """Порча любого поля обязана давать отказ, а не случайный ответ."""
    w = generate_wallet()
    x, y, z, r, s = _parts(w, b"b-hydra")
    values = {"x": x, "y": y, "r": r, "s": s}
    values[field] ^= 1
    assert native_ec.verify_core(library, values["x"], values["y"], z,
                                 values["r"], values["s"]) is False


def test_point_off_the_curve_is_refused(library):
    """Invalid-curve: точка не на кривой — отказ, как и в Python.

    Это защита, а не формальность: на чужой кривой уравнение проверки может
    сойтись для подписи, которую владелец ключа не делал.
    """
    w = generate_wallet()
    x, y, z, r, s = _parts(w, b"b-hydra")
    assert native_ec.verify_core(library, x, (y + 1) % wallet._P, z, r, s) is False


def test_zero_and_out_of_range_are_refused(library):
    """r и s обязаны лежать в (0, N) — по обе стороны."""
    w = generate_wallet()
    x, y, z, r, s = _parts(w, b"b-hydra")
    assert native_ec.verify_core(library, x, y, z, 0, s) is False
    assert native_ec.verify_core(library, x, y, z, r, 0) is False
    assert native_ec.verify_core(library, x, y, z, wallet._N, s) is False
    assert native_ec.verify_core(library, x, y, z, r, wallet._N) is False


def test_huge_numbers_do_not_crash(library):
    """Число больше 32 байт — отказ, а не исключение из ctypes."""
    w = generate_wallet()
    x, y, z, r, s = _parts(w, b"b-hydra")
    assert native_ec.verify_core(library, 1 << 300, y, z, r, s) is False


def test_random_signatures_agree_with_pure_python(library):
    """Сплошная сверка: на каждом примере ответ ОДИН И ТОТ ЖЕ.

    Смешаны верные подписи и порченые — иначе проверялась бы только половина
    таблицы истинности.
    """
    reference = wallet._verify_core_pure
    for index in range(20):
        w = generate_wallet()
        x, y, z, r, s = _parts(w, f"payload {index}".encode("utf-8"))
        if index % 2:
            r ^= 0xDEAD                       # половину примеров ломаем
        assert (native_ec.verify_core(library, x, y, z, r, s)
                == reference(x, y, z, r, s)), index


# --- Подключение как бэкенда ---------------------------------------------------
def test_wallet_uses_the_native_backend(library, monkeypatch, tmp_path):
    """`Wallet.verify` через нативное ядро принимает те же подписи."""
    monkeypatch.setattr(wallet, "_VERIFY_CORE",
                        lambda x, y, z, r, s: native_ec.verify_core(
                            library, x, y, z, r, s))
    w = generate_wallet()
    message = "через нативное ядро".encode("utf-8")
    signature = w.sign(message)
    assert Wallet.verify(w.public_key_hex, message, signature)
    assert not Wallet.verify(w.public_key_hex, "другое".encode("utf-8"), signature)


def test_selftest_catches_a_backend_that_says_yes_to_everything():
    """Ядро, принимающее всё подряд, обязано быть отвергнуто.

    Это самый опасный вид поломки: узел с таким ядром принимал бы любую
    транзакцию, включая чужие траты.
    """
    assert wallet._selftest_backend(lambda x, y, z, r, s: True) is False


def test_selftest_catches_a_backend_that_says_no_to_everything():
    assert wallet._selftest_backend(lambda x, y, z, r, s: False) is False


def test_selftest_accepts_the_reference_core():
    """А эталон обязан пройти — иначе проверка бессмысленна."""
    assert wallet._selftest_backend(wallet._verify_core_pure) is True
