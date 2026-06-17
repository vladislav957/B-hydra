"""Тесты кошелька: генерация ключей, адрес, ECDSA-подпись."""

from b_hydra.wallet import Wallet, address_from_public_key, generate_wallet


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


def test_verify_rejects_off_curve_public_key():
    """Подпись не должна проходить с публичным ключом не на кривой secp256k1."""
    from b_hydra.wallet import _P

    w = generate_wallet()
    sig = w.sign(b"hello")
    assert Wallet.verify(w.public_key_hex, b"hello", sig)        # валидный ключ

    pub = bytes.fromhex(w.public_key_hex)
    x = int.from_bytes(pub[1:33], "big")
    y = int.from_bytes(pub[33:65], "big")
    off_curve = b"\x04" + x.to_bytes(32, "big") + ((y + 1) % _P).to_bytes(32, "big")
    assert not Wallet.verify(off_curve.hex(), b"hello", sig)     # точка не на кривой


def test_rejects_out_of_range_private_key():
    import pytest

    from b_hydra.wallet import _N
    with pytest.raises(ValueError):
        Wallet(0)
    with pytest.raises(ValueError):
        Wallet(_N)


def test_is_valid_address():
    from b_hydra.wallet import is_valid_address

    assert is_valid_address(generate_wallet().address)
    # Инъекции и мусор отвергаются.
    assert not is_valid_address("BHY<script>alert(1)</script>")
    assert not is_valid_address("<svg onload=alert(1)>")
    assert not is_valid_address("not-an-address")
    assert not is_valid_address(generate_wallet().address + "x")  # битый checksum
    assert not is_valid_address(123)
