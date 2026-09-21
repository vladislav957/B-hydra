"""Нативная ПОДПИСЬ: та же, что у чистого Python, байт в байт.

`bhydra_ec.hpp` умел подписывать давно, но наружу через `extern "C"` выводилась
только проверка — подпись считалась чистым Python. Теперь выведена и она.

⚠️ ГЛАВНОЕ ЗДЕСЬ — НЕ СКОРОСТЬ, А СОВПАДЕНИЕ БАЙТ В БАЙТ, и требование это
строже, чем к проверке. Проверке достаточно совпадения ОТВЕТА («да/нет»), а
подпись в B-hydra намеренно ВОСПРОИЗВОДИМА: нонс детерминированный (RFC 6979),
и от байтов подписи зависит `txid`. Разойдись две реализации хоть на бит — один
и тот же перевод получил бы разные идентификаторы у узла со сборкой и без неё:
мемпул счёл бы их разными транзакциями, а заранее подписанные транзакции
перестали бы сходиться.

Тесты ПРОПУСКАЮТСЯ, если библиотека не собрана, — как тесты Bluetooth без
адаптера. Собрать:

    g++ -O2 -std=c++17 -shared -fPIC -I cpp -o libbhydra_ec.so cpp/bhydra_ec_lib.cpp
"""

import os

import pytest

from b_hydra import native_ec, wallet
from b_hydra.wallet import (Wallet, _CURVE, _hash_to_int, _rfc6979_nonces,
                            _verify_core_pure, generate_wallet)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_library = native_ec.default()

needs_native = pytest.mark.skipif(
    _library is None or not native_ec.has_sign(_library),
    reason="нативная библиотека не собрана или без bhydra_ec_sign")


def _pure_sign(private, z):
    """Эталон: подпись чистым Python."""
    return _CURVE.sign(private, z, _rfc6979_nonces(private, z), low_s=True)


def _native_sign(private, z):
    return native_ec.sign_core(_library, private, z)


def _point(w):
    raw = w.public_key_bytes
    return int.from_bytes(raw[1:33], "big"), int.from_bytes(raw[33:65], "big")


# --- Совпадение с эталоном -----------------------------------------------------
@needs_native
@pytest.mark.parametrize("index", range(12))
def test_native_signature_is_byte_identical_to_python(index):
    """⚠️ БАЙТ В БАЙТ, а не «тоже валидная».

    Валидных подписей у одного сообщения бесконечно много — любая пара (r, s),
    удовлетворяющая уравнению. Проверять «подпись верна» значило бы пропустить
    реализацию с ДРУГИМ нонсом, а она сломала бы воспроизводимость и развела
    бы txid у разных сборок.
    """
    w = generate_wallet()
    payload = f"перевод №{index}".encode("utf-8")
    z = _hash_to_int(payload)
    assert _native_sign(w._priv, z) == _pure_sign(w._priv, z)


@needs_native
def test_it_matches_on_many_random_keys():
    """Один ключ мог совпасть случайно — проверяем на сотне."""
    for index in range(100):
        w = generate_wallet()
        z = _hash_to_int(f"сообщение {index}".encode("utf-8"))
        assert _native_sign(w._priv, z) == _pure_sign(w._priv, z), index


@needs_native
@pytest.mark.parametrize("payload", [
    b"", b"\x00", b"\xff" * 64, "кириллица".encode("utf-8"),
    b"x" * 100_000, bytes(range(256)),
])
def test_edges_match_too(payload):
    """Края: пусто, нули, длинное, не-ASCII."""
    w = generate_wallet()
    z = _hash_to_int(payload)
    assert _native_sign(w._priv, z) == _pure_sign(w._priv, z)


@needs_native
def test_the_signature_is_actually_valid():
    """⚠️ Совпадения мало: оба могли ошибаться одинаково.

    Если бы обе реализации считали неверно ОДИНАКОВО, проверка на равенство
    прошла бы. Поэтому подпись ещё и проверяется — независимым кодом.
    """
    for _ in range(10):
        w = generate_wallet()
        payload = b"b-hydra native signature validity"
        z = _hash_to_int(payload)
        r, s = _native_sign(w._priv, z)
        x, y = _point(w)
        assert _verify_core_pure(x, y, z, r, s)


@needs_native
def test_low_s_is_respected():
    """s обязана лежать в нижней половине — защита от ковкости подписи.

    Без неё из валидной подписи делается вторая, тоже валидная, с другим
    txid: та самая ковкость, что стоила бирже Mt. Gox объяснений.
    """
    for index in range(30):
        w = generate_wallet()
        z = _hash_to_int(f"low-s {index}".encode("utf-8"))
        _, s = _native_sign(w._priv, z)
        assert s <= _CURVE.n // 2, "s из верхней половины — подпись ковкая"


@needs_native
def test_signing_is_deterministic():
    """Дважды одно и то же сообщение — одна и та же подпись (RFC 6979)."""
    w = generate_wallet()
    z = _hash_to_int(b"deterministic")
    assert _native_sign(w._priv, z) == _native_sign(w._priv, z)


@needs_native
def test_different_messages_give_different_nonces():
    """⚠️ Повтор нонса у двух сообщений РАСКРЫВАЕТ приватный ключ.

    Так уводили ключи из PS3 и ряда кошельков. Одинаковый `r` означает
    одинаковый `k` — этого быть не должно никогда.
    """
    w = generate_wallet()
    подписи = [_native_sign(w._priv, _hash_to_int(f"сообщение {i}".encode()))
               for i in range(25)]
    r_values = [r for r, _ in подписи]
    assert len(set(r_values)) == len(r_values), "нонс повторился"


# --- Отказы --------------------------------------------------------------------
@needs_native
def test_an_out_of_range_key_is_refused():
    """Ключ вне 1..n-1 — отказ, а не мусорная подпись."""
    z = _hash_to_int(b"x")
    assert _native_sign(0, z) is None
    assert _native_sign(_CURVE.n, z) is None
    assert _native_sign(_CURVE.n + 1, z) is None


@needs_native
def test_a_key_that_does_not_fit_in_32_bytes_is_refused():
    """Слишком большое число не должно ронять ctypes с OverflowError."""
    assert _native_sign(1 << 300, _hash_to_int(b"x")) is None


# --- Подключение бэкенда -------------------------------------------------------
@needs_native
def test_the_wallet_actually_uses_the_native_core():
    assert wallet._SIGN_CORE is not None, "нативная подпись не подключилась"
    assert "подпись" in wallet._BACKEND, wallet._BACKEND


@needs_native
def test_the_wallet_signature_matches_the_pure_path():
    """Через сам `Wallet.sign` результат тоже обязан совпасть с эталоном."""
    w = generate_wallet()
    payload = b"through the wallet"
    got = bytes.fromhex(w.sign(payload))
    r, s = _pure_sign(w._priv, _hash_to_int(payload))
    assert got == r.to_bytes(32, "big") + s.to_bytes(32, "big")


@needs_native
def test_the_wallet_falls_back_when_the_core_refuses(monkeypatch):
    """⚠️ Отказ нативного ядра НЕ должен ронять подпись.

    Библиотека может отказать (чужая сборка, урезанная версия). Кошелёк обязан
    посчитать сам, а не остаться без подписи — деньги важнее скорости.
    """
    monkeypatch.setattr(wallet, "_SIGN_CORE", lambda private, z: None)
    w = generate_wallet()
    payload = b"fallback path"
    got = bytes.fromhex(w.sign(payload))
    assert Wallet.verify(w.public_key_hex, payload, got.hex())
    r, s = _pure_sign(w._priv, _hash_to_int(payload))
    assert got == r.to_bytes(32, "big") + s.to_bytes(32, "big")


@needs_native
def test_the_selftest_rejects_a_lying_core():
    """⚠️ Самопроверка обязана отвергать ядро, которое врёт.

    Без этого достаточно было бы подсунуть библиотеку, возвращающую подпись с
    ДРУГИМ нонсом, — она бы включилась и развела txid по сети.
    """
    def врущее(private, z):
        r, s = _pure_sign(private, z)
        return (r ^ 1), s                    # на один бит мимо

    assert wallet._selftest_sign(врущее) is False
    assert wallet._selftest_sign(lambda private, z: None) is False
    # А настоящее — принимает.
    assert wallet._selftest_sign(_native_sign) is True


# --- Согласованность исходников ------------------------------------------------
def test_the_signing_code_is_not_duplicated_in_cpp():
    """⚠️ `sign()` и `sign_hash()` — ОДИН код, а не две копии.

    Это код консенсуса; две его копии однажды разъедутся, и ошибка в одной не
    всплывёт в тестах другой. Ровно поэтому в проекте уже сводили две
    реализации кривой в единый `ec.py`.
    """
    source = open(os.path.join(ROOT, "cpp", "bhydra_ec.hpp"),
                  encoding="utf-8").read()
    assert "inline Bytes sign_hash(" in source
    # `sign` обязан быть тонкой обёрткой над sign_hash.
    start = source.index("inline Bytes sign(const U256 &priv, const Bytes &payload)")
    body = source[start:start + 300]
    assert "sign_hash(priv, hash_to_int(payload))" in body, body[:200]
    assert source.count("Rfc6979 nonces(") == 1, "цикл подписи скопирован"


def test_the_bridge_survives_a_library_without_sign():
    """⚠️ У СТАРОЙ собранной библиотеки `bhydra_ec_sign` нет.

    Требовать его при загрузке значило бы отвергнуть такую библиотеку целиком
    и потерять уже работающее ускорение ПРОВЕРКИ. Подпись необязательна.
    """
    class Древняя:
        pass

    assert native_ec.has_sign(Древняя()) is False
    assert native_ec.has_sign(None) is False
