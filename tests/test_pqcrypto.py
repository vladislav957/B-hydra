"""Пост-квантовые хеш-подписи: Lamport, WOTS, XMSS-lite, QuantumWallet."""

import pytest

from b_hydra import hashing
from b_hydra import pqcrypto as pq
from b_hydra.pqcrypto import (
    MerkleSigner, QuantumWallet,
    lamport_keygen, lamport_sign, lamport_verify,
    wots_keygen, wots_sign, wots_verify,
)


@pytest.fixture(autouse=True)
def _fast_sha():
    """XMSS/WOTS-keygen делают тысячи хешей; гоняем на быстром движке ради
    скорости CI. Байт-совместимость pure↔fast отдельно проверяет
    test_signatures_identical_on_pure_and_fast_sha (и test_hashing.py)."""
    original = hashing.is_pure()
    hashing.use_pure_sha(False)
    try:
        yield
    finally:
        hashing.use_pure_sha(original)


# --- Lamport OTS ------------------------------------------------------------

def test_lamport_sign_verify():
    sk, pk = lamport_keygen(seed=b"seed-a")
    msg = b"perevod 10 BHY"
    sig = lamport_sign(sk, msg)
    assert lamport_verify(pk, msg, sig)
    assert not lamport_verify(pk, b"perevod 11 BHY", sig)   # другое сообщение
    assert len(sig) == 256


def test_lamport_deterministic_from_seed():
    _, pk1 = lamport_keygen(seed=b"same")
    _, pk2 = lamport_keygen(seed=b"same")
    assert pk1 == pk2                                        # детерминизм по seed


def test_lamport_forged_signature_rejected():
    sk, pk = lamport_keygen(seed=b"seed-b")
    sig = lamport_sign(sk, b"hello")
    sig[0] = b"\x00" * pq.N                                  # порча одного секрета
    assert not lamport_verify(pk, b"hello", sig)


# --- WOTS -------------------------------------------------------------------

def test_wots_sign_verify():
    sk, pk = wots_keygen(seed=b"w-seed")
    msg = b"escrow 25 BHY"
    sig = wots_sign(sk, msg)
    assert wots_verify(pk, msg, sig)
    assert len(sig) == pq.WOTS_LEN


def test_wots_checksum_blocks_forgery():
    """Меняем сообщение так, что часть цифр падает (форджабельное направление) —
    контрольная сумма растёт, и подпись под другое сообщение не проходит."""
    sk, pk = wots_keygen(seed=b"w-seed2")
    sig = wots_sign(sk, b"message one")
    for other in (b"message two", b"", b"MESSAGE ONE", b"message onf"):
        assert not wots_verify(pk, other, sig)


def test_wots_pk_recovery():
    sk, pk = wots_keygen(seed=b"w-seed3")
    sig = wots_sign(sk, b"data")
    assert pq.wots_pk_from_sig(b"data", sig) == list(pk)


# --- XMSS-lite (многоразовая) -----------------------------------------------

def test_xmss_many_signatures_one_pubkey():
    signer = MerkleSigner(height=3, seed=b"xmss")   # 8 подписей
    root = signer.public_key
    assert signer.remaining == 8
    for i in range(8):
        msg = f"tx #{i}".encode()
        sig = signer.sign(msg)
        assert sig["index"] == i
        # проверка без секретов, только по публичному корню
        assert MerkleSigner.verify(root, msg, sig)
        # чужое сообщение с тем же путём не проходит
        assert not MerkleSigner.verify(root, b"tampered", sig)
    assert signer.remaining == 0


def test_xmss_exhaustion_raises():
    signer = MerkleSigner(height=1, seed=b"tiny")   # всего 2 ключа
    signer.sign(b"a")
    signer.sign(b"b")
    with pytest.raises(RuntimeError):
        signer.sign(b"c")


def test_xmss_wrong_root_rejected():
    signer = MerkleSigner(height=2, seed=b"r1")
    other = MerkleSigner(height=2, seed=b"r2")
    sig = signer.sign(b"hi")
    assert MerkleSigner.verify(signer.public_key, b"hi", sig)
    assert not MerkleSigner.verify(other.public_key, b"hi", sig)   # чужой корень


def test_xmss_tampered_auth_path_rejected():
    signer = MerkleSigner(height=2, seed=b"r3")
    sig = signer.sign(b"payload")
    assert sig["auth"], "путь включения не должен быть пустым"
    sig["auth"][0]["hash"] = "00" * 64                            # порча пути
    assert not MerkleSigner.verify(signer.public_key, b"payload", sig)


# --- QuantumWallet ----------------------------------------------------------

def test_quantum_wallet_address_and_sign():
    w = QuantumWallet(height=3, seed=b"qw-seed")
    assert w.address.startswith("BHYQ")
    assert QuantumWallet(height=3, seed=b"qw-seed").address == w.address  # детерм.
    sig = w.sign("оплата за кофе")
    assert w.verify("оплата за кофе", sig)
    assert not w.verify("оплата за чай", sig)                    # подделка


def test_quantum_wallet_distinct_seeds_distinct_addresses():
    a = QuantumWallet(height=2, seed=b"A").address
    b = QuantumWallet(height=2, seed=b"B").address
    assert a != b


def test_signatures_identical_on_pure_and_fast_sha():
    """Подписи одинаковы на нашем SHA «с нуля» и на hashlib — как и вся крипта."""
    from b_hydra import hashing
    original = hashing.is_pure()
    try:
        results = []
        for pure in (True, False):
            hashing.use_pure_sha(pure)
            sk, pk = wots_keygen(seed=b"cross")
            sig = wots_sign(sk, b"quantum")
            results.append((pk, sig, wots_verify(pk, b"quantum", sig)))
        assert results[0] == results[1]        # pure и fast дают одно и то же
        assert results[0][2] is True
    finally:
        hashing.use_pure_sha(original)


# --- Параметризация: режим SHA-512 (P512) -----------------------------------

def test_wots_p512_sign_verify():
    from b_hydra.pqcrypto import P512
    sk, pk = wots_keygen(seed=b"w512", params=P512)
    msg = b"paranoid transfer"
    sig = wots_sign(sk, msg, params=P512)
    assert wots_verify(pk, msg, sig, params=P512)
    assert len(sig) == P512["len1"] + P512["len2"]          # 131 цепочка
    assert all(len(s) == 64 for s in sig)                   # элементы по 64 байта
    # чужое сообщение не проходит (контрольная сумма)
    assert not wots_verify(pk, b"other", sig, params=P512)


def test_lamport_p512_has_512_pairs():
    from b_hydra.pqcrypto import P512
    sk, pk = lamport_keygen(seed=b"l512", params=P512)
    sig = lamport_sign(sk, b"data", params=P512)
    assert len(sig) == 512                                  # бит на каждый бит SHA-512
    assert lamport_verify(pk, b"data", sig, params=P512)


def test_xmss_p512_roundtrip():
    from b_hydra.pqcrypto import P512
    signer = MerkleSigner(height=2, seed=b"x512", params=P512)
    root = signer.public_key
    for i in range(4):
        msg = f"tx-{i}".encode()
        sig = signer.sign(msg)
        assert sig["alg"] == "sha512"
        assert MerkleSigner.verify(root, msg, sig)
        assert not MerkleSigner.verify(root, b"nope", sig)


def test_quantum_wallet_strong_mode():
    strong = QuantumWallet(height=2, seed=b"qs", strong=True)
    weak = QuantumWallet(height=2, seed=b"qs", strong=False)
    assert strong.address.startswith("BHYQ")
    assert strong.address != weak.address                  # разный хеш → разный ключ
    sig = strong.sign("оплата")
    assert sig["alg"] == "sha512"
    assert strong.verify("оплата", sig)


def test_p256_and_p512_produce_different_signatures():
    from b_hydra.pqcrypto import P256, P512
    a, _ = wots_keygen(seed=b"same-seed", params=P256)
    b, _ = wots_keygen(seed=b"same-seed", params=P512)
    # один seed, но разный хеш → разные ключи и разная длина
    assert len(a) != len(b)
