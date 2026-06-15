"""Тесты кошелька: генерация ключей, адрес, ECDSA-подпись."""

from wallet import Wallet, address_from_public_key, generate_wallet


def test_keypair_sizes():
    w = generate_wallet()
    assert len(bytes.fromhex(w.private_key_hex)) == 32
    pub = bytes.fromhex(w.public_key_hex)
    assert pub[0] == 0x04 and len(pub) == 65   # несжатый публичный ключ


def test_address_prefix():
    assert generate_wallet().address.startswith("BHY")


def test_wallet_is_deterministic_from_private_key():
    w = generate_wallet()
    restored = Wallet.from_private_hex(w.private_key_hex)
    assert restored.address == w.address
    assert restored.public_key_hex == w.public_key_hex


def test_address_from_public_key_matches():
    w = generate_wallet()
    assert address_from_public_key(w.public_key_hex) == w.address


def test_sign_and_verify():
    w = generate_wallet()
    sig = w.sign(b"hello b-hydra")
    assert Wallet.verify(w.public_key_hex, b"hello b-hydra", sig)


def test_verify_fails_with_wrong_key():
    w, other = generate_wallet(), generate_wallet()
    sig = w.sign(b"hello")
    assert not Wallet.verify(other.public_key_hex, b"hello", sig)


def test_verify_fails_with_tampered_message():
    w = generate_wallet()
    sig = w.sign(b"hello")
    assert not Wallet.verify(w.public_key_hex, b"hello!", sig)


def test_unique_wallets():
    assert generate_wallet().address != generate_wallet().address
