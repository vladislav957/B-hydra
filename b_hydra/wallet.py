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


# Версии адресов (первый байт payload): обычный ECDSA и гибридный ECDSA+PQ.
ADDR_VERSION = 0x1f            # обычный кошелёк (ECDSA secp256k1)
HYBRID_VERSION = 0x2f         # гибридный: ECDSA + пост-квантовая XMSS-подпись


def _address_from_payload(version: int, digest: bytes) -> str:
    """Собирает адрес BHY… из версии и 20-байтного отпечатка ключей."""
    payload = bytes([version]) + digest
    checksum = hashing.double_sha512(payload)[:4]
    return "BHY" + _b58encode(payload + checksum)


def address_from_public_key_bytes(pub_bytes: bytes) -> str:
    """Обычный адрес B-hydra из несжатого публичного ключа (байты)."""
    return _address_from_payload(ADDR_VERSION,
                                 _ripemd160(hashing.sha512_bytes(pub_bytes)))


def address_from_public_key(public_key_hex: str) -> str:
    """Адрес B-hydra из публичного ключа (hex)."""
    return address_from_public_key_bytes(bytes.fromhex(public_key_hex))


def hybrid_address(pub_bytes: bytes, pq_root_hex: str) -> str:
    """Гибридный адрес: отпечаток ОТ ОБОИХ ключей — ECDSA-публичного и
    XMSS-корня. Потратить с него можно только предъявив обе подписи, поэтому
    квантовому атакующему (ломает лишь ECDSA) адрес недоступен."""
    material = pub_bytes + bytes.fromhex(pq_root_hex)
    return _address_from_payload(HYBRID_VERSION,
                                 _ripemd160(hashing.sha512_bytes(material)))


def _decode_address(address):
    """(version, digest) из адреса или None, если строка не адрес B-hydra."""
    if not isinstance(address, str) or not address.startswith("BHY"):
        return None
    body = address[3:]
    if not body or any(ch not in _B58_ALPHABET for ch in body):
        return None
    try:
        raw = _b58decode(body)
    except ValueError:
        return None
    if len(raw) != 1 + 20 + 4:
        return None
    payload, checksum = raw[:-4], raw[-4:]
    if hashing.double_sha512(payload)[:4] != checksum:
        return None
    return payload[0], payload[1:]


def address_version(address):
    """Версия адреса (0x1f обычный / 0x2f гибридный) или None."""
    decoded = _decode_address(address)
    return decoded[0] if decoded else None


def is_hybrid_address(address) -> bool:
    """True, если адрес гибридный (ECDSA + пост-квантовая защита)."""
    return address_version(address) == HYBRID_VERSION


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
    version = address_version(address)
    return version in (ADDR_VERSION, HYBRID_VERSION)


# --- Подключаемое ядро проверки подписи (скорость) --------------------------
def _verify_core_pure(x, y, z, r, s) -> bool:
    """Проверка уравнения ECDSA на чистом Python (эталон)."""
    w = _inverse_mod(s, _N)
    u1 = (z * w) % _N
    u2 = (r * w) % _N
    point = _point_add(_scalar_mult(u1, _G), _scalar_mult(u2, (x, y)))
    return point is not None and (point[0] % _N) == r


# По умолчанию — чистый Python. Ниже (после класса) при наличии coincurve и
# успешном self-test ядро заменяется на нативный libsecp256k1.
_VERIFY_CORE = _verify_core_pure
_BACKEND = "pure-python"


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
        # Само уравнение проверки считает подключаемое ядро: быстрый нативный
        # secp256k1 (libsecp256k1), если доступен и прошёл self-test, иначе
        # чистый Python. Множество принимаемых подписей у них одинаковое.
        return _VERIFY_CORE(x, y, z, r, s)

    # --- Баланс ----------------------------------------------------------
    def balance(self, node) -> float:
        """Удобный доступ к балансу адреса через ноду/блокчейн."""
        return node.get_balance(self.address)

    @classmethod
    def from_private_hex(cls, private_hex: str) -> "Wallet":
        """Восстанавливает кошелёк из приватного ключа (hex).

        Терпимо к вводу человека: убирает пробелы/переводы строк и
        необязательный префикс 0x. Бросает ValueError с понятным текстом,
        если ключ не той длины, содержит не-hex символы или вне диапазона.
        """
        if not private_hex:
            raise ValueError("пустой приватный ключ")
        cleaned = "".join(str(private_hex).split())     # убрать любые пробелы
        if cleaned[:2].lower() == "0x":
            cleaned = cleaned[2:]
        if len(cleaned) != 64:
            raise ValueError(
                f"приватный ключ должен быть 64 hex-символа, а тут {len(cleaned)}. "
                "Похоже, вы вставили не приватный ключ (например, адрес).")
        try:
            value = int.from_bytes(bytes.fromhex(cleaned), "big")
        except ValueError:
            raise ValueError("в приватном ключе есть лишние символы (не 0-9, a-f)")
        return cls(value)                               # проверит диапазон [1, N-1]

    def __repr__(self):
        return f"<Wallet {self.address}>"


def generate_wallet() -> Wallet:
    """Создаёт новый кошелёк."""
    return Wallet()


# --- Ускорение: нативный secp256k1 (libsecp256k1 через coincurve) ------------
# Включается ТОЛЬКО если библиотека есть и прошла self-test на байт-совместимость
# с эталонной проверкой. Иначе тихо остаётся чистый Python. Множество
# принимаемых подписей идентично: перед вызовом ядра verify() уже проверил
# принадлежность точки кривой и диапазон r, s, а высокий s нормализуется.
def _der_sig(r: int, s: int) -> bytes:
    def _int(i):
        b = i.to_bytes((i.bit_length() + 7) // 8 or 1, "big")
        if b[0] & 0x80:
            b = b"\x00" + b
        return b"\x02" + bytes([len(b)]) + b
    body = _int(r) + _int(s)
    return b"\x30" + bytes([len(body)]) + body


try:
    import coincurve as _coincurve

    def _verify_core_fast(x, y, z, r, s) -> bool:
        try:
            pub = _coincurve.PublicKey(
                b"\x04" + x.to_bytes(32, "big") + y.to_bytes(32, "big"))
            s_low = s if s <= _N // 2 else _N - s   # libsecp256k1 принимает low-s
            return pub.verify(_der_sig(r, s_low), z.to_bytes(32, "big"), hasher=None)
        except Exception:
            return False

    def _selftest_fast_backend() -> bool:
        """Быстрое ядро обязано совпасть с эталоном на нескольких примерах."""
        for _ in range(8):
            w = Wallet()
            msg = b"b-hydra ecdsa backend self-test"
            sig = bytes.fromhex(w.sign(msg))
            pb = w.public_key_bytes
            x = int.from_bytes(pb[1:33], "big")
            y = int.from_bytes(pb[33:65], "big")
            r = int.from_bytes(sig[:32], "big")
            s = int.from_bytes(sig[32:], "big")
            z = _hash_to_int(msg)
            z_bad = _hash_to_int(b"tampered payload")
            if not _verify_core_fast(x, y, z, r, s):
                return False                       # верную подпись отверг
            if _verify_core_fast(x, y, z_bad, r, s):
                return False                       # чужой payload принял
        return True

    if _selftest_fast_backend():
        _VERIFY_CORE = _verify_core_fast
        _BACKEND = "coincurve (libsecp256k1)"
    else:
        _BACKEND = "pure-python (self-test не пройден)"
except Exception:
    _BACKEND = "pure-python (coincurve недоступен)"


if __name__ == "__main__":
    w = generate_wallet()
    print("Адрес         :", w.address)
    print("Публичный ключ:", w.public_key_hex[:32], "…")
    msg = b"hello b-hydra"
    sig = w.sign(msg)
    print("Подпись верна :", Wallet.verify(w.public_key_hex, msg, sig))
    print("Чужой ключ    :", Wallet.verify(generate_wallet().public_key_hex, msg, sig))
