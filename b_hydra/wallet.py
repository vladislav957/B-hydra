"""
wallet.py — кошелёк B-hydra с ECDSA-подписями на кривой secp256k1.

Реализация ECDSA выполнена на чистом Python и использует только стандартную
библиотеку (hashlib, secrets) — внешние зависимости не требуются, поэтому
кошелёк работает в любом окружении.

Кошелёк хранит приватный ключ, выводит публичный ключ и адрес, подписывает
транзакции и проверяет чужие подписи.
"""

import hashlib
import secrets

if __name__ == "__main__" and __package__ in (None, ""):
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    __package__ = "b_hydra"

from . import hashing

# --- Параметры кривой secp256k1 ---------------------------------------------
_P = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFC2F
_N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
_GX = 0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798
_GY = 0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8
_G = (_GX, _GY)

_B58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


# --- Арифметика на эллиптической кривой --------------------------------------
def _inverse_mod(k, p):
    return pow(k, -1, p)


def _point_add(point1, point2):
    if point1 is None:
        return point2
    if point2 is None:
        return point1
    x1, y1 = point1
    x2, y2 = point2
    if x1 == x2 and (y1 + y2) % _P == 0:
        return None  # точка на бесконечности
    if x1 == x2:
        m = (3 * x1 * x1) * _inverse_mod(2 * y1, _P)
    else:
        m = (y1 - y2) * _inverse_mod(x1 - x2, _P)
    x3 = (m * m - x1 - x2) % _P
    y3 = (m * (x1 - x3) - y1) % _P
    return (x3, y3)


def _scalar_mult(k, point):
    result = None
    addend = point
    while k:
        if k & 1:
            result = _point_add(result, addend)
        addend = _point_add(addend, addend)
        k >>= 1
    return result


def _is_on_curve(point) -> bool:
    """Проверяет, что точка лежит на кривой secp256k1: y^2 = x^3 + 7 (mod P)."""
    if point is None:
        return False
    x, y = point
    if not (0 <= x < _P and 0 <= y < _P):
        return False
    return (y * y - (x * x * x + 7)) % _P == 0


def _hash_to_int(payload: bytes) -> int:
    """SHA-512 сообщения -> целое, усечённое до битности порядка N."""
    digest = hashing.sha512_bytes(payload)
    z = int.from_bytes(digest, "big")
    shift = len(digest) * 8 - _N.bit_length()
    if shift > 0:
        z >>= shift
    return z


# --- Кодирование адреса ------------------------------------------------------
def _b58encode(data: bytes) -> str:
    num = int.from_bytes(data, "big")
    encoded = ""
    while num > 0:
        num, rem = divmod(num, 58)
        encoded = _B58_ALPHABET[rem] + encoded
    pad = len(data) - len(data.lstrip(b"\x00"))
    return "1" * pad + encoded


def _ripemd160(data: bytes) -> bytes:
    try:
        h = hashlib.new("ripemd160")
        h.update(data)
        return h.digest()
    except (ValueError, TypeError):
        return hashing.sha256_bytes(data)[:20]


def address_from_public_key_bytes(pub_bytes: bytes) -> str:
    """Адрес B-hydra из несжатого публичного ключа (байты)."""
    payload = b"\x1f" + _ripemd160(hashing.sha512_bytes(pub_bytes))
    checksum = hashing.double_sha512(payload)[:4]
    return "BHY" + _b58encode(payload + checksum)


def address_from_public_key(public_key_hex: str) -> str:
    """Адрес B-hydra из публичного ключа (hex)."""
    return address_from_public_key_bytes(bytes.fromhex(public_key_hex))


def _b58decode(text: str) -> bytes:
    num = 0
    for ch in text:
        num = num * 58 + _B58_ALPHABET.index(ch)   # ValueError, если символ чужой
    body = num.to_bytes((num.bit_length() + 7) // 8, "big")
    pad = len(text) - len(text.lstrip("1"))         # ведущие '1' → нулевые байты
    return b"\x00" * pad + body


def is_valid_address(address) -> bool:
    """Проверяет, что строка — корректный адрес B-hydra (префикс + checksum).

    Заодно отсекает любые посторонние символы (включая HTML/JS), поэтому узел
    не принимает «адреса» с инъекциями.
    """
    if not isinstance(address, str) or not address.startswith("BHY"):
        return False
    body = address[3:]
    if not body or any(ch not in _B58_ALPHABET for ch in body):
        return False
    try:
        raw = _b58decode(body)
    except ValueError:
        return False
    if len(raw) != 1 + 20 + 4:                       # версия + ripemd160 + checksum
        return False
    payload, checksum = raw[:-4], raw[-4:]
    if payload[0] != 0x1f:
        return False
    return hashing.double_sha512(payload)[:4] == checksum


class Wallet:
    """Кошелёк B-hydra: пара ключей ECDSA (secp256k1) + адрес."""

    def __init__(self, private_value: int = None):
        if private_value is None:
            private_value = secrets.randbelow(_N - 1) + 1
        if not 1 <= private_value < _N:
            raise ValueError("private key out of range [1, N-1]")
        self._priv = private_value
        self._pub = _scalar_mult(self._priv, _G)

    # --- Ключи -----------------------------------------------------------
    @property
    def public_key_bytes(self) -> bytes:
        """Несжатый публичный ключ: 0x04 || X(32) || Y(32)."""
        x, y = self._pub
        return b"\x04" + x.to_bytes(32, "big") + y.to_bytes(32, "big")

    @property
    def public_key_hex(self) -> str:
        return self.public_key_bytes.hex()

    @property
    def private_key_hex(self) -> str:
        return self._priv.to_bytes(32, "big").hex()

    # --- Адрес -----------------------------------------------------------
    @property
    def address(self) -> str:
        """Адрес = 'BHY' + Base58(version || RIPEMD160(SHA-512(pub)) || checksum)."""
        return address_from_public_key_bytes(self.public_key_bytes)

    @staticmethod
    def address_from_public_key(public_key_hex: str) -> str:
        """Вычисляет адрес по публичному ключу (hex) — для проверки входов."""
        return address_from_public_key(public_key_hex)

    # --- Подпись / проверка ----------------------------------------------
    def sign(self, payload: bytes) -> str:
        """Подписывает байты ECDSA, возвращает hex (r||s, по 32 байта)."""
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
        z = _hash_to_int(payload)
        while True:
            k = secrets.randbelow(_N - 1) + 1
            point = _scalar_mult(k, _G)
            r = point[0] % _N
            if r == 0:
                continue
            s = (_inverse_mod(k, _N) * (z + r * self._priv)) % _N
            if s == 0:
                continue
            if s > _N // 2:          # low-s (защита от ковкости подписи)
                s = _N - s
            return r.to_bytes(32, "big").hex() + s.to_bytes(32, "big").hex()

    @staticmethod
    def verify(public_key_hex: str, payload: bytes, signature_hex: str) -> bool:
        """Проверяет ECDSA-подпись по публичному ключу отправителя (hex)."""
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
        try:
            pub_bytes = bytes.fromhex(public_key_hex)
            sig = bytes.fromhex(signature_hex)
            if len(pub_bytes) != 65 or pub_bytes[0] != 0x04 or len(sig) != 64:
                return False
            x = int.from_bytes(pub_bytes[1:33], "big")
            y = int.from_bytes(pub_bytes[33:65], "big")
            r = int.from_bytes(sig[:32], "big")
            s = int.from_bytes(sig[32:], "big")
        except ValueError:
            return False

        # Публичный ключ обязан быть валидной точкой кривой (защита от
        # invalid-curve атак), а r, s — лежать в допустимом диапазоне.
        if not _is_on_curve((x, y)):
            return False
        if not (1 <= r < _N and 1 <= s < _N):
            return False
        z = _hash_to_int(payload)
        w = _inverse_mod(s, _N)
        u1 = (z * w) % _N
        u2 = (r * w) % _N
        point = _point_add(_scalar_mult(u1, _G), _scalar_mult(u2, (x, y)))
        if point is None:
            return False
        return (point[0] % _N) == r

    # --- Баланс ----------------------------------------------------------
    def balance(self, node) -> float:
        """Удобный доступ к балансу адреса через ноду/блокчейн."""
        return node.get_balance(self.address)

    @classmethod
    def from_private_hex(cls, private_hex: str) -> "Wallet":
        return cls(int.from_bytes(bytes.fromhex(private_hex), "big"))

    def __repr__(self):
        return f"<Wallet {self.address}>"


def generate_wallet() -> Wallet:
    """Создаёт новый кошелёк."""
    return Wallet()


if __name__ == "__main__":
    w = generate_wallet()
    print("Адрес         :", w.address)
    print("Публичный ключ:", w.public_key_hex[:32], "…")
    msg = b"hello b-hydra"
    sig = w.sign(msg)
    print("Подпись верна :", Wallet.verify(w.public_key_hex, msg, sig))
    print("Чужой ключ    :", Wallet.verify(generate_wallet().public_key_hex, msg, sig))
