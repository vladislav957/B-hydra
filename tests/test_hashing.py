"""Тесты хеширования: SHA-256/512, дерево Меркла, hashcash."""

import hashlib

from b_hydra import hashcash, hashing
from b_hydra.merkle import MerkleTree, merkle_root


def test_pure_backend_matches_hashlib():
    """Движок 'pure' (SHA с нуля) даёт те же хеши, что и hashlib."""
    original = hashing.is_pure()
    try:
        hashing.use_pure_sha(True)
        assert hashing.backend() == "pure"
        for data in (b"", b"abc", b"B-hydra block header", b"x" * 200):
            assert hashing.sha256(data) == hashlib.sha256(data).hexdigest()
            assert hashing.sha512(data) == hashlib.sha512(data).hexdigest()
    finally:
        hashing.use_pure_sha(original)  # восстановить исходный движок


def test_default_backend_is_pure():
    """По умолчанию проект использует SHA «с нуля»."""
    assert hashing.backend() == "pure"


def test_sha256_known_vector():
    # Эталон NIST: SHA-256("abc")
    assert hashing.sha256("abc") == (
        "ba7816bf8f01cfea414140de5dae2223"
        "b00361a396177a9cb410ff61f20015ad"
    )


def test_sha512_known_vector():
    # Эталон NIST: SHA-512("abc")
    assert hashing.sha512("abc") == (
        "ddaf35a193617abacc417349ae204131"
        "12e6fa4e89a97ea20a9eeee64b55d39a"
        "2192992a274fc1a836ba3c23a3feebbd"
        "454d4423643ce80e2a9ac94fa54ca49f"
    )


def test_sha512_accepts_bytes_and_str():
    assert hashing.sha512("x") == hashing.sha512(b"x")


def test_merkle_root_changes_with_data():
    r1 = merkle_root(["a", "b", "c"])
    r2 = merkle_root(["a", "b", "d"])
    assert r1 != r2
    assert len(r1) == 128  # SHA-512 hex


def test_merkle_tree_root():
    assert len(MerkleTree(["x", "y", "z"]).root) == 128


def test_hashcash_mint_and_check():
    _, stamp, _ = hashcash.mint("resource", bits=12)
    assert hashcash.check(stamp, bits=12)
    assert not hashcash.check(stamp, bits=24)   # требуем больше — не проходит


def test_proof_of_work_difficulty():
    _, digest = hashcash.proof_of_work("data", difficulty=2)
    assert digest.startswith("00")
