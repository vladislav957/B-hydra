"""Тесты хеширования: SHA-256/512, дерево Меркла, hashcash."""

import hashlib
import os
import random

from b_hydra import hashcash, hashing, sha2
from b_hydra.merkle import MerkleTree, merkle_root, verify_proof


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


def test_sha2_from_scratch_nist_vectors():
    """Наш SHA-2 «с нуля» совпадает с официальными векторами NIST (FIPS 180-4)."""
    assert sha2.sha256("") == (
        "e3b0c44298fc1c149afbf4c8996fb924"
        "27ae41e4649b934ca495991b7852b855")
    assert sha2.sha512("") == (
        "cf83e1357eefb8bdf1542850d66d8007d620e4050b5715dc83f4a921d36ce9ce"
        "47d0d13c5d85f2b0ff8318d2877eec2f63b931bd47417a81a538327af927da3e")
    # Двухблочное сообщение (проверяет расписание и второй блок сжатия).
    assert sha2.sha256(
        "abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq") == (
        "248d6a61d20638b8e5c026930c3e6039"
        "a33ce45964ff2167f6ecedd419db06c1")


def test_sha2_from_scratch_matches_hashlib_fuzz():
    """Фаззинг: наш SHA-2 = hashlib на случайных сообщениях и на границах паддинга."""
    random.seed(1234)
    for _ in range(400):
        data = os.urandom(random.randint(0, 260))
        assert sha2.sha256_bytes(data) == hashlib.sha256(data).digest()
        assert sha2.sha512_bytes(data) == hashlib.sha512(data).digest()
    # Критичные длины у границ блоков (55/56/63/64 для 512-бит; 111/112/127/128 для 1024).
    for n in (0, 1, 55, 56, 57, 63, 64, 65, 111, 112, 113, 127, 128, 129):
        data = b"\x5a" * n
        assert sha2.sha256_bytes(data) == hashlib.sha256(data).digest()
        assert sha2.sha512_bytes(data) == hashlib.sha512(data).digest()


def test_crypto_stack_identical_on_pure_sha_and_hashlib():
    """Весь путь крипты (txid, корень Меркла, хеш блока, SPV) байт-в-байт
    одинаков на нашем SHA-2 «с нуля» и на hashlib — блокчейн реально работает
    на собственном алгоритме."""
    from b_hydra.node import BHydraNode
    from b_hydra.wallet import Wallet

    original = hashing.is_pure()
    try:
        hashing.use_pure_sha(True)
        a = Wallet.from_private_hex("11" * 32)
        b = Wallet.from_private_hex("22" * 32)
        node = BHydraNode(difficulty=1)
        node.mine_pending(a.address)
        tx = node.create_transaction(a, b.address, 7, fee=0.5)
        node.add_transaction(tx)
        node.mine_pending(b.address)
        block = node.blockchain.chain[2]
        proof = node.merkle_proof(tx.txid)

        # Снимок неизменного входа, затем пересчёт обоими движками.
        def recompute():
            return (block.calculate_hash(), block._calculate_merkle_root(),
                    tx.txid,
                    verify_proof(bytes.fromhex(proof["leaf"]),
                                 proof["proof"], block.merkle_root))

        hashing.use_pure_sha(True)
        pure = recompute()
        hashing.use_pure_sha(False)
        fast = recompute()
        assert pure == fast                     # движки дают идентичный результат
        assert pure[0] == block.hash            # и совпадают с сохранённым блоком
        assert pure[3] is True                  # SPV-доказательство проходит
    finally:
        hashing.use_pure_sha(original)


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
