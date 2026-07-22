"""Тесты SHA-256/512 «с нуля»: эталоны NIST и сверка с hashlib."""

import hashlib
import os

from b_hydra import sha2


def test_sha256_nist_vectors():
    assert sha2.sha256("abc") == (
        "ba7816bf8f01cfea414140de5dae2223"
        "b00361a396177a9cb410ff61f20015ad"
    )
    assert sha2.sha256("") == (
        "e3b0c44298fc1c149afbf4c8996fb924"
        "27ae41e4649b934ca495991b7852b855"
    )


def test_sha512_nist_vectors():
    assert sha2.sha512("abc") == (
        "ddaf35a193617abacc417349ae204131"
        "12e6fa4e89a97ea20a9eeee64b55d39a"
        "2192992a274fc1a836ba3c23a3feebbd"
        "454d4423643ce80e2a9ac94fa54ca49f"
    )
    assert sha2.sha512("") == (
        "cf83e1357eefb8bdf1542850d66d8007"
        "d620e4050b5715dc83f4a921d36ce9ce"
        "47d0d13c5d85f2b0ff8318d2877eec2f"
        "63b931bd47417a81a538327af927da3e"
    )


def test_matches_hashlib_on_block_boundaries():
    # Длины вокруг границ блоков (64 байта для 256, 128 для 512) и дополнения.
    lengths = [0, 1, 55, 56, 57, 63, 64, 65, 111, 112, 113, 127, 128, 129, 1000]
    for n in lengths:
        data = os.urandom(n)
        assert sha2.sha256(data) == hashlib.sha256(data).hexdigest()
        assert sha2.sha512(data) == hashlib.sha512(data).hexdigest()


def test_accepts_str_and_bytes():
    assert sha2.sha256("B-hydra") == sha2.sha256(b"B-hydra")
    assert sha2.sha512("B-hydra") == sha2.sha512(b"B-hydra")


def test_streaming_api_matches_oneshot():
    """Потоковый update() любыми кусками == разовому хешу (и hashlib)."""
    import random
    random.seed(11)
    for _ in range(200):
        data = os.urandom(random.randint(0, 300))
        h256, h512 = sha2.Sha256(), sha2.Sha512()
        i = 0
        while i < len(data):
            step = random.randint(1, 50)
            h256.update(data[i:i + step])
            h512.update(data[i:i + step])
            i += step
        assert h256.hexdigest() == hashlib.sha256(data).hexdigest()
        assert h512.hexdigest() == hashlib.sha512(data).hexdigest()


def test_digest_does_not_mutate_state():
    """digest() можно звать многократно и продолжать update()."""
    h = sha2.Sha512()
    h.update(b"abc")
    assert h.digest() == hashlib.sha512(b"abc").digest()
    assert h.digest() == hashlib.sha512(b"abc").digest()   # повтор — тот же
    h.update(b"def")
    assert h.hexdigest() == hashlib.sha512(b"abcdef").hexdigest()


def test_copy_forks_state_independently():
    h = sha2.Sha256()
    h.update(b"hello")
    branch = h.copy()
    branch.update(b" world")
    h.update(b"!!!")
    assert branch.hexdigest() == hashlib.sha256(b"hello world").hexdigest()
    assert h.hexdigest() == hashlib.sha256(b"hello!!!").hexdigest()


def test_sizes_match_hashlib():
    assert sha2.Sha256().digest_size == 32
    assert sha2.Sha256().block_size == 64
    assert sha2.Sha512().digest_size == 64
    assert sha2.Sha512().block_size == 128
