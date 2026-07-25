"""Тесты RIPEMD-160 «с нуля» и устойчивости адреса к сборке Python.

RIPEMD-160 участвует в выводе адреса (`ripemd160(sha512(pub))`), но в сборках
OpenSSL 3 его часто нет. Раньше проект в этом случае молча подставлял
`sha256(...)[:20]` — и узел без RIPEMD-160 выводил бы из того же ключа ДРУГОЙ
адрес, то есть попросту не увидел бы своих монет. Теперь алгоритм всегда свой,
а hashlib берётся лишь как ускоритель после сверки байтов.
"""

import hashlib
import os
import random

import pytest

from b_hydra import hashing
from b_hydra.ripemd import Ripemd160, ripemd160, ripemd160_bytes
from b_hydra.wallet import Wallet, generate_wallet


def _native_available():
    try:
        hashlib.new("ripemd160")
        return True
    except (ValueError, TypeError):
        return False


# --- Соответствие стандарту --------------------------------------------------
def test_reference_vectors():
    """Эталонные векторы из оригинальной спецификации RIPEMD-160."""
    assert ripemd160("") == "9c1185a5c5e9fc54612808977ee8f548b2258d31"
    assert ripemd160("a") == "0bdc9d2d256b3ee9daae347be6f4dc835a467ffe"
    assert ripemd160("abc") == "8eb208f7e05d987a9b044a8e98c6b087f15a0bfc"
    assert ripemd160("message digest") == "5d0689ef49d2fae572b881b123a85ffa21595f36"
    assert ripemd160("abcdefghijklmnopqrstuvwxyz") == \
        "f71c27109c692c1b56bbdceb5b9d2865b3708dbc"
    assert ripemd160("abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq") == \
        "12a053384a9c0c88e405a06c27dcf49ada62eb2b"
    assert ripemd160("1234567890" * 8) == "9b752e45573d4b39f4dbd3323cab82bf63326bfb"


def test_digest_size_and_type():
    digest = ripemd160_bytes(b"B-hydra")
    assert isinstance(digest, bytes) and len(digest) == 20
    assert Ripemd160.digest_size == 20 and Ripemd160.block_size == 64


@pytest.mark.skipif(not _native_available(),
                    reason="в этой сборке hashlib не умеет ripemd160")
def test_matches_hashlib_on_padding_boundaries():
    """Совпадает с hashlib на границах паддинга (55/56/57, 63/64/65…)."""
    for length in list(range(0, 130)) + [200, 500, 1000]:
        data = os.urandom(length)
        assert ripemd160_bytes(data) == hashlib.new("ripemd160", data).digest(), length


@pytest.mark.skipif(not _native_available(),
                    reason="в этой сборке hashlib не умеет ripemd160")
def test_matches_hashlib_on_random_data():
    rnd = random.Random(11)
    for _ in range(300):
        data = bytes(rnd.randrange(256) for _ in range(rnd.randrange(0, 300)))
        assert ripemd160_bytes(data) == hashlib.new("ripemd160", data).digest()


# --- Потоковый API -----------------------------------------------------------
def test_streaming_matches_one_shot():
    """update() по произвольным кускам даёт то же, что и разовый вызов."""
    rnd = random.Random(12)
    data = bytes(rnd.randrange(256) for _ in range(777))
    hasher, offset = Ripemd160(), 0
    while offset < len(data):
        step = rnd.randrange(1, 100)
        hasher.update(data[offset:offset + step])
        offset += step
    assert hasher.hexdigest() == ripemd160(data)


def test_digest_does_not_consume_state():
    hasher = Ripemd160("часть 1")
    first = hasher.hexdigest()
    assert hasher.hexdigest() == first        # повторный вызов не меняет состояние
    hasher.update("часть 2")
    assert hasher.hexdigest() == ripemd160("часть 1часть 2")


def test_copy_branches_independently():
    hasher = Ripemd160("общее начало")
    clone = hasher.copy()
    clone.update("только в копии")
    assert hasher.hexdigest() == ripemd160("общее начало")
    assert clone.hexdigest() == ripemd160("общее началотолько в копии")


# --- Устойчивость адреса -----------------------------------------------------
def test_hashing_layer_uses_real_ripemd_not_sha_substitute():
    """Слой хеширования отдаёт настоящий RIPEMD-160, а не обрезанный SHA-256."""
    data = b"B-hydra"
    assert hashing.ripemd160(data) == ripemd160_bytes(data)
    assert hashing.ripemd160(data) != hashing.sha256_bytes(data)[:20]


def test_address_is_identical_without_native_ripemd(monkeypatch):
    """Главная регрессия: адрес не зависит от наличия RIPEMD-160 в сборке.

    Раньше на такой машине подставлялся sha256(...)[:20], и адрес получался
    другим — кошелёк «терял» монеты. Теперь считает своя реализация.
    """
    wallet = generate_wallet()
    expected = wallet.address

    # Притворяемся сборкой Python без RIPEMD-160 в hashlib.
    monkeypatch.setattr(hashing, "_NATIVE_RIPEMD", False)
    monkeypatch.setattr(hashing, "_PURE", False)
    assert hashing.ripemd_backend() == "pure"
    assert Wallet.from_private_hex(wallet.private_key_hex).address == expected


@pytest.mark.skipif(not _native_available(),
                    reason="в этой сборке hashlib не умеет ripemd160")
def test_both_backends_give_the_same_address(monkeypatch):
    """Свой RIPEMD-160 и hashlib дают один и тот же адрес."""
    wallet = generate_wallet()
    monkeypatch.setattr(hashing, "_PURE", False)     # ускоритель включён
    assert hashing.ripemd_backend() == "hashlib"
    with_native = Wallet.from_private_hex(wallet.private_key_hex).address
    monkeypatch.setattr(hashing, "_PURE", True)      # своя реализация
    assert hashing.ripemd_backend() == "pure"
    assert Wallet.from_private_hex(wallet.private_key_hex).address == with_native


def test_native_backend_is_verified_before_use():
    """Ускоритель включается только после сверки байтов с нашей реализацией."""
    assert hashing._native_ripemd160_available() is _native_available()
