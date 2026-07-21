"""Шифрование приватного ключа паролем (на нашем SHA-512, без зависимостей)."""

import pytest

from b_hydra import hashing, keystore
from b_hydra.keystore import (
    decrypt_secret, encrypt_secret, is_encrypted, _hmac_sha512,
)

SECRET = "a3f1" * 16          # 64-символьный приватный ключ (hex)
FAST = 500                    # мало итераций — тесты быстрые


@pytest.fixture(autouse=True)
def _fast_sha():
    """KDF растягивает пароль тысячами хешей — на быстром движке ради CI."""
    original = hashing.is_pure()
    hashing.use_pure_sha(False)
    try:
        yield
    finally:
        hashing.use_pure_sha(original)


def test_roundtrip_correct_password():
    store = encrypt_secret(SECRET, "s3cret pass", iterations=FAST)
    assert decrypt_secret(store, "s3cret pass") == SECRET


def test_wrong_password_rejected():
    store = encrypt_secret(SECRET, "right", iterations=FAST)
    with pytest.raises(ValueError):
        decrypt_secret(store, "wrong")


def test_secret_not_in_plaintext():
    """Приватный ключ не должен встречаться в зашифрованном хранилище."""
    store = encrypt_secret(SECRET, "pw", iterations=FAST)
    blob = str(store)
    assert SECRET not in blob
    assert store["ciphertext"] != SECRET


def test_salt_makes_ciphertext_unique():
    """Тот же ключ и пароль → разный шифртекст (уникальные соль/nonce)."""
    a = encrypt_secret(SECRET, "pw", iterations=FAST)
    b = encrypt_secret(SECRET, "pw", iterations=FAST)
    assert a["salt"] != b["salt"]
    assert a["ciphertext"] != b["ciphertext"]      # нет детерминизма/утечки
    assert decrypt_secret(a, "pw") == decrypt_secret(b, "pw") == SECRET


def test_tampered_ciphertext_detected():
    """Порча шифртекста ловится MAC до расшифровки."""
    store = encrypt_secret(SECRET, "pw", iterations=FAST)
    ba = bytearray(bytes.fromhex(store["ciphertext"]))
    ba[0] ^= 0xFF
    store["ciphertext"] = ba.hex()
    with pytest.raises(ValueError):
        decrypt_secret(store, "pw")


def test_tampered_mac_detected():
    store = encrypt_secret(SECRET, "pw", iterations=FAST)
    store["mac"] = "00" * 64
    with pytest.raises(ValueError):
        decrypt_secret(store, "pw")


def test_corrupted_store_raises():
    with pytest.raises(ValueError):
        decrypt_secret({"kdf": "sha512-iter"}, "pw")   # нет полей


def test_empty_password_rejected():
    with pytest.raises(ValueError):
        encrypt_secret(SECRET, "")


def test_is_encrypted_detection():
    store = encrypt_secret(SECRET, "pw", iterations=FAST)
    assert is_encrypted(store)
    assert not is_encrypted(SECRET)                # голый hex — не хранилище
    assert not is_encrypted({"foo": "bar"})


def test_file_roundtrip(tmp_path):
    path = str(tmp_path / "wallet.enc")
    keystore.save_encrypted(path, SECRET, "filepass", iterations=FAST)
    assert keystore.load_encrypted(path, "filepass") == SECRET
    with pytest.raises(ValueError):
        keystore.load_encrypted(path, "nope")
    # на диске нет открытого ключа
    assert SECRET not in open(path).read()


def test_hmac_matches_reference():
    """Наш HMAC-SHA512 совпадает со стандартной hmac/hashlib (RFC 2104)."""
    import hashlib
    import hmac as ref
    key, msg = b"key material", b"message bytes"
    assert _hmac_sha512(key, msg) == ref.new(key, msg, hashlib.sha512).digest()
    # и на длинном ключе (> размера блока — путь с предварительным хешем)
    longk = b"x" * 200
    assert _hmac_sha512(longk, msg) == ref.new(longk, msg, hashlib.sha512).digest()
