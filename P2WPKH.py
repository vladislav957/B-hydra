"""
P2WPKH.py — деривация адреса B-hydra из публичного ключа.

Упрощённый аналог P2WPKH (pay-to-witness-public-key-hash): из публичного ключа
кошелька получаем «программу-свидетель» (хеш ключа) и удобочитаемый адрес.
Полноценный сегвит-формат Bitcoin не реализуется — это демонстрация принципа.
"""

import hashlib

from wallet import Wallet


def witness_program(public_key_bytes: bytes) -> bytes:
    """Программа-свидетель = RIPEMD160(SHA-512(pubkey)) (20 байт)."""
    sha = hashlib.sha512(public_key_bytes).digest()
    try:
        ripe = hashlib.new("ripemd160")
        ripe.update(sha)
        return ripe.digest()
    except (ValueError, TypeError):
        return hashlib.sha256(sha).digest()[:20]


def p2wpkh_address(wallet: Wallet) -> str:
    """Возвращает адрес кошелька (использует деривацию из wallet.py)."""
    return wallet.address


def describe(wallet: Wallet) -> dict:
    """Сводка по ключам и адресу кошелька."""
    program = witness_program(wallet.public_key_bytes)
    return {
        "public_key": wallet.public_key_hex,
        "witness_program": program.hex(),
        "address": wallet.address,
    }


if __name__ == "__main__":
    w = Wallet()
    info = describe(w)
    print("Публичный ключ   :", info["public_key"][:32], "…")
    print("Witness program  :", info["witness_program"])
    print("Адрес B-hydra    :", info["address"])
