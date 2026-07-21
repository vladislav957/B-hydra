"""
keystore.py — шифрование приватного ключа паролем (на нашем SHA, без зависимостей).

Раньше приватный ключ лежал в файле открытым hex — кто получил файл, тот
получил монеты. Здесь — зашифрованное хранилище на собственной криптографии:

  * KDF: ключ из пароля выводится растяжением (много итераций нашего SHA-512
    с солью) — перебор пароля дорогой, радужные таблицы бесполезны из-за соли;
  * шифр: потоковый — keystream генерируется SHA-512 в режиме счётчика (CTR),
    XOR с данными. Тот же keystream расшифровывает обратно;
  * целостность: HMAC-SHA512 (Encrypt-then-MAC) — неверный пароль или порча
    файла обнаруживаются ДО расшифровки, тихая подмена невозможна.

Формат файла (JSON): версия, соль, число итераций, nonce, ciphertext, mac —
всё hex. Приватный ключ в открытом виде на диск не пишется.

Здесь СОЗНАТЕЛЬНО используется быстрый `hashlib` (а не наш SHA-с-нуля): это
локальное шифрование файла, не консенсус, а KDF должен успевать растянуть
пароль на сотни тысяч итераций за доли секунды при разблокировке. Наша
реализация SHA остаётся там, где она в консенсусе (txid, PoW, Меркл, адреса).
"""

from __future__ import annotations

import hashlib
import json
import os

_VERSION = 1
_ITERATIONS = 200_000        # растяжение пароля (дороже перебор)
_SALT_LEN = 16
_NONCE_LEN = 16
_DIGEST = 64                 # длина выхода SHA-512 в байтах
_HMAC_BLOCK = 128            # внутренний размер блока SHA-512 (для HMAC, RFC 2104)


def _derive_key(password: str, salt: bytes, iterations: int) -> bytes:
    """KDF: пароль + соль → 64-байтный ключ растяжением SHA-512 (hashlib).

    Итеративное перехеширование (как в PBKDF1) делает подбор пароля во столько
    же раз дороже; соль исключает переиспользование предвычислений."""
    block = hashlib.sha512(salt + password.encode("utf-8")).digest()
    for _ in range(iterations):
        block = hashlib.sha512(block + salt).digest()
    return block


def _keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    """Псевдослучайный поток нужной длины: SHA-512(key || nonce || counter)."""
    out = bytearray()
    counter = 0
    while len(out) < length:
        out += hashlib.sha512(key + nonce + counter.to_bytes(8, "big")).digest()
        counter += 1
    return bytes(out[:length])


def _xor(data: bytes, stream: bytes) -> bytes:
    return bytes(a ^ b for a, b in zip(data, stream))


def _hmac_sha512(key: bytes, message: bytes) -> bytes:
    """HMAC-SHA512 (RFC 2104, hashlib) — целостность и подлинность."""
    if len(key) > _HMAC_BLOCK:
        key = hashlib.sha512(key).digest()
    key = key.ljust(_HMAC_BLOCK, b"\x00")
    o_pad = bytes(b ^ 0x5c for b in key)
    i_pad = bytes(b ^ 0x36 for b in key)
    inner = hashlib.sha512(i_pad + message).digest()
    return hashlib.sha512(o_pad + inner).digest()


def encrypt_secret(secret_hex: str, password: str,
                   iterations: int = _ITERATIONS) -> dict:
    """Шифрует приватный ключ (hex) паролем. Возвращает dict для JSON."""
    if not password:
        raise ValueError("пароль не может быть пустым")
    salt = os.urandom(_SALT_LEN)
    nonce = os.urandom(_NONCE_LEN)
    key = _derive_key(password, salt, iterations)
    plaintext = secret_hex.encode("utf-8")
    ciphertext = _xor(plaintext, _keystream(key, nonce, len(plaintext)))
    # Encrypt-then-MAC: MAC покрывает nonce + шифртекст (ключ MAC отделён).
    mac = _hmac_sha512(key + b"mac", nonce + ciphertext)
    return {
        "version": _VERSION,
        "kdf": "sha512-iter",
        "iterations": iterations,
        "salt": salt.hex(),
        "nonce": nonce.hex(),
        "ciphertext": ciphertext.hex(),
        "mac": mac.hex(),
    }


def decrypt_secret(store: dict, password: str) -> str:
    """Расшифровывает приватный ключ. ValueError при неверном пароле/порче."""
    try:
        salt = bytes.fromhex(store["salt"])
        nonce = bytes.fromhex(store["nonce"])
        ciphertext = bytes.fromhex(store["ciphertext"])
        expected = bytes.fromhex(store["mac"])
        iterations = int(store.get("iterations", _ITERATIONS))
    except (KeyError, ValueError, TypeError):
        raise ValueError("повреждённый файл кошелька") from None
    key = _derive_key(password, salt, iterations)
    # Сначала проверяем MAC — неверный пароль виден ДО расшифровки.
    mac = _hmac_sha512(key + b"mac", nonce + ciphertext)
    if not _consteq(mac, expected):
        raise ValueError("неверный пароль или файл повреждён")
    return _xor(ciphertext, _keystream(key, nonce, len(ciphertext))).decode("utf-8")


def _consteq(a: bytes, b: bytes) -> bool:
    """Сравнение за постоянное время (защита от timing-атак на MAC)."""
    if len(a) != len(b):
        return False
    diff = 0
    for x, y in zip(a, b):
        diff |= x ^ y
    return diff == 0


def is_encrypted(data) -> bool:
    """True, если содержимое файла — зашифрованное хранилище (а не голый hex)."""
    return isinstance(data, dict) and data.get("kdf") == "sha512-iter"


def save_encrypted(path: str, secret_hex: str, password: str,
                   iterations: int = _ITERATIONS) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(encrypt_secret(secret_hex, password, iterations), fh, indent=2)


def load_encrypted(path: str, password: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return decrypt_secret(json.load(fh), password)


if __name__ == "__main__":
    secret = "11" * 32
    store = encrypt_secret(secret, "correct horse", iterations=2000)
    print("зашифровано:", store["ciphertext"][:24], "…")
    print("расшифровка верным паролем:",
          decrypt_secret(store, "correct horse") == secret)
    try:
        decrypt_secret(store, "wrong password")
    except ValueError as e:
        print("неверный пароль отвергнут:", e)
