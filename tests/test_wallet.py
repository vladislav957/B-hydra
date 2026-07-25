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


def test_from_private_hex_is_lenient_and_clear():
    """Импорт ключа терпим к 0x/пробелам/регистру и даёт понятные ошибки."""
    import pytest
    from b_hydra.wallet import Wallet, generate_wallet
    w = generate_wallet()
    k = w.private_key_hex
    # Терпимость к человеческому вводу.
    for variant in (k, "0x" + k, "  " + k + "\n", k.upper()):
        assert Wallet.from_private_hex(variant).address == w.address
    # Понятные ошибки.
    for bad in ("", "0x", "BHYDabc", k[:-1], "zz" * 32):
        with pytest.raises(ValueError):
            Wallet.from_private_hex(bad)


# --- Детерминированный нонс RFC 6979 ----------------------------------------
# ECDSA раскрывает приватный ключ, если один и тот же k использован для двух
# сообщений. Пока k брался из ГСЧ, безопасность подписи зависела от качества
# генератора; RFC 6979 выводит k из ключа и хеша сообщения через HMAC.
import hashlib
import hmac as _ref_hmac
import os

from b_hydra.wallet import _hmac_sha512, _rfc6979_nonces


def test_our_hmac_matches_reference():
    """Наш HMAC-SHA512 байт-в-байт совпадает с hmac/hashlib."""
    for _ in range(50):
        key, message = os.urandom(20), os.urandom(64)
        assert _hmac_sha512(key, message) == _ref_hmac.new(
            key, message, hashlib.sha512).digest()


def test_our_hmac_handles_oversized_key():
    """Ключ длиннее блока SHA-512 сжимается хешем (RFC 2104)."""
    key, message = os.urandom(200), b"payload"
    assert _hmac_sha512(key, message) == _ref_hmac.new(
        key, message, hashlib.sha512).digest()


def test_matches_official_rfc6979_vectors():
    """Эталонные векторы RFC 6979 (приложение A.2.5: P-256 + SHA-256).

    Нонс зависит только от порядка кривой, ключа и хеша сообщения, поэтому
    вектор проверяется без арифметики на кривой. Совпадение доказывает, что
    HMAC-DRBG собран ровно по спецификации.
    """
    order = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551
    priv = 0xC9AFA9D845BA75166B5C215767B1D6934E50C3DB36E89B127B8A622B120F6721
    expected = {
        b"sample": 0xA6E3C57DD01ABE90086538398355DD4C3B17AA873382B0F24D6129493D8AAD60,
        b"test": 0xD16B6AE827F17175E040871A1C7EC3500192C4C92677336EC2537ACAEE0008E0,
    }
    for message, want in expected.items():
        digest = hashlib.sha256(message).digest()
        z = int.from_bytes(digest, "big")      # len(digest)*8 == qlen, сдвиг не нужен
        nonce = next(_rfc6979_nonces(
            priv, z, order=order,
            hmac_fn=lambda k, m: _ref_hmac.new(k, m, hashlib.sha256).digest(),
            hlen=32))
        assert nonce == want


def test_nonce_stays_in_range():
    from b_hydra.wallet import _N
    w = generate_wallet()
    priv = int(w.private_key_hex, 16)
    nonces = _rfc6979_nonces(priv, 12345)
    for _, nonce in zip(range(5), nonces):
        assert 1 <= nonce < _N


def test_signature_is_reproducible():
    """Одно сообщение и один ключ дают ровно ту же подпись."""
    w = generate_wallet()
    assert w.sign("перевод 10") == w.sign("перевод 10")


def test_different_messages_give_different_nonces():
    """Разные сообщения не должны переиспользовать нонс (иначе утечка ключа).

    Разные r в подписях означают разные k: r — это x-координата точки k·G.
    """
    w = generate_wallet()
    first = w.sign("перевод 10")[:64]     # r
    second = w.sign("перевод 20")[:64]
    assert first != second


def test_different_keys_give_different_nonces():
    """Нонс завязан на приватный ключ, а не только на сообщение."""
    a, b = generate_wallet(), generate_wallet()
    assert a.sign("одно и то же")[:64] != b.sign("одно и то же")[:64]


def test_deterministic_signature_still_verifies():
    w = generate_wallet()
    signature = w.sign("оплата")
    assert Wallet.verify(w.public_key_hex, "оплата", signature)
    assert not Wallet.verify(w.public_key_hex, "другая оплата", signature)


def test_signature_keeps_low_s():
    """Low-s сохранён — защита от ковкости подписи."""
    from b_hydra.wallet import _N
    w = generate_wallet()
    for i in range(10):
        s = int(w.sign(f"сообщение {i}")[64:], 16)
        assert 0 < s <= _N // 2
