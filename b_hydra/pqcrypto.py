"""
pqcrypto.py — пост-квантовые подписи B-hydra на хешах (экспериментально).

ECDSA (secp256k1) ломается алгоритмом Шора на квантовом компьютере. Хеш-подписи
квантово-устойчивы: алгоритм Гровера лишь вдвое ослабляет стойкость хеша
(SHA-256 → 128 бит, SHA-512 → 256 бит — всё ещё непробиваемо), а против самих
хешей квантовый компьютер бессилен. Здесь — три уровня, все на НАШЕМ SHA «с
нуля» (`hashing`), ноль зависимостей:

    Lamport  — простейшая ОДНОРАЗОВА подпись (256 пар секретов);
    WOTS     — Winternitz OTS: компактнее Lamport, с контрольной суммой,
               которая не даёт подделать подпись под другое сообщение;
    XMSS     — МНОГОРАЗОВАЯ: дерево Меркла над 2^h ключами WOTS даёт один
               публичный ключ (корень) на 2^h подписей. Это строительный блок
               SPHINCS+ (ML-DSA/FIPS 205) — и он красиво переиспользует наше
               дерево Меркла (`merkle.py`).

⚠️ Это ОТДЕЛЬНЫЙ экспериментальный модуль: рабочая ECDSA-цепочка (`wallet.py`,
консенсус) не меняется. XMSS/WOTS — подписи с СОСТОЯНИЕМ (каждый одноразовый
ключ нельзя использовать дважды), поэтому для интеграции в консенсус нужен учёт
израсходованных индексов. Здесь — корректная, проверяемая криптографическая
основа и `QuantumWallet` для демонстрации.

Замечание о WOTS+: «полный» WOTS+ добавляет к цепочкам публичные битовые маски
против мульти-target-атак. Здесь реализован классический WOTS — контрольная
сумма уже обеспечивает стойкость к подделке под другое сообщение (главное
свойство); маски WOTS+ — усиление, отмеченное в коде.
"""

from __future__ import annotations

import os

if __name__ == "__main__" and __package__ in (None, ""):
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    __package__ = "b_hydra"

from . import hashing
from .merkle import merkle_proof, merkle_root, verify_proof

# --- Общие параметры ---------------------------------------------------------
N = 32                     # длина хеш-элемента в байтах (SHA-256 → 128 бит PQ)


def _h(data: bytes) -> bytes:
    """Базовый хеш модуля: наш SHA-256 «с нуля» (32 байта)."""
    return hashing.sha256_bytes(data)


def _digest_bits(message: bytes):
    """256 бит дайджеста сообщения (старший бит первым)."""
    d = _h(message)
    return [(byte >> (7 - i)) & 1 for byte in d for i in range(8)]


# =============================================================================
# 1. Lamport — одноразовая подпись (самая наглядная)
# =============================================================================
def lamport_keygen(seed: bytes | None = None):
    """Пара ключей Lamport: 256 пар секретов (по одному на бит дайджеста).

    seed — для детерминированной генерации (иначе os.urandom). Возвращает
    (secret_key, public_key); публичный — хеши всех секретов.
    """
    def _rand(i):
        return _h(seed + i.to_bytes(4, "big")) if seed else os.urandom(N)
    sk = [(_rand(2 * i), _rand(2 * i + 1)) for i in range(256)]
    pk = [(_h(a), _h(b)) for a, b in sk]
    return sk, pk


def lamport_sign(sk, message: bytes):
    """Подпись: для каждого бита раскрываем соответствующий секрет из пары."""
    bits = _digest_bits(message)
    return [sk[i][bit] for i, bit in enumerate(bits)]


def lamport_verify(pk, message: bytes, sig) -> bool:
    """Проверка: хеш раскрытого секрета совпадает с публичным для нужного бита."""
    if len(sig) != 256:
        return False
    bits = _digest_bits(message)
    return all(_h(sig[i]) == pk[i][bit] for i, bit in enumerate(bits))


# =============================================================================
# 2. WOTS — Winternitz One-Time Signature (компактная одноразовая)
# =============================================================================
W = 16                     # основание Винтерница (цифра = 4 бита)
LOG_W = 4
LEN1 = 64                  # 256-битный дайджест в базе-16 = 64 цифры
LEN2 = 3                   # длина контрольной суммы в базе-16 (макс. 64·15=960)
WOTS_LEN = LEN1 + LEN2     # 67 цепочек


def _chain(x: bytes, steps: int) -> bytes:
    """Применяет хеш к x подряд `steps` раз (звено цепочки Винтерница).

    (WOTS+ добавил бы здесь XOR с публичной маской, зависящей от номера шага, —
    защита от мульти-target-атак; для нашей учебной схемы достаточно классики.)
    """
    for _ in range(steps):
        x = _h(x)
    return x


def _wots_digits(message: bytes):
    """Дайджест + контрольная сумма как 67 цифр базы-16.

    Контрольная сумма растёт, когда цифры дайджеста уменьшаются — поэтому
    подделать подпись под другое сообщение нельзя: часть цепочек пришлось бы
    «отмотать назад», а это обращение хеша.
    """
    d = _h(message)
    digits = []
    for byte in d:
        digits.append(byte >> 4)          # старшая тетрада
        digits.append(byte & 0x0F)        # младшая тетрада
    checksum = sum(W - 1 - x for x in digits)
    csum = [(checksum >> (LOG_W * (LEN2 - 1 - i))) & (W - 1) for i in range(LEN2)]
    return digits + csum                  # 64 + 3 = 67


def wots_keygen(seed: bytes | None = None):
    """Пара ключей WOTS: 67 секретов; публичный — каждый прохеширован W-1 раз."""
    def _rand(i):
        return _h(seed + b"wots" + i.to_bytes(4, "big")) if seed else os.urandom(N)
    sk = [_rand(i) for i in range(WOTS_LEN)]
    pk = [_chain(x, W - 1) for x in sk]
    return sk, pk


def wots_sign(sk, message: bytes):
    """Подпись WOTS: i-й секрет прохеширован d_i раз (d_i — i-я цифра)."""
    ds = _wots_digits(message)
    return [_chain(sk[i], ds[i]) for i in range(WOTS_LEN)]


def wots_pk_from_sig(message: bytes, sig):
    """Восстанавливает публичный ключ WOTS из подписи (для XMSS и проверки)."""
    ds = _wots_digits(message)
    return [_chain(sig[i], W - 1 - ds[i]) for i in range(WOTS_LEN)]


def wots_verify(pk, message: bytes, sig) -> bool:
    """Проверка WOTS: домотанная до конца подпись равна публичному ключу."""
    if len(sig) != WOTS_LEN:
        return False
    return wots_pk_from_sig(message, sig) == list(pk)


def _wots_pk_hash(pk) -> bytes:
    """Компактный хеш публичного ключа WOTS (лист дерева XMSS)."""
    return _h(b"".join(pk))


# =============================================================================
# 3. XMSS-lite — многоразовая подпись (дерево Меркла над ключами WOTS)
# =============================================================================
class MerkleSigner:
    """XMSS-lite: 2^height одноразовых ключей WOTS под одним публичным ключом
    (корнем дерева Меркла). Подписи с СОСТОЯНИЕМ — индекс не переиспользуется.

    Публичный ключ = корень Меркла (hex). Подпись = {index, wots, auth}: WOTS-
    подпись листа + путь включения к корню (наш `merkle_proof`). Проверяющий
    восстанавливает публичный ключ WOTS из подписи, хеширует в лист и по пути
    доходит до корня — знать все листья не нужно.
    """

    def __init__(self, height: int = 4, seed: bytes | None = None):
        if not 1 <= height <= 20:
            raise ValueError("height должен быть в диапазоне 1..20")
        self.height = height
        self.n_leaves = 1 << height
        self.index = 0
        self._seed = seed or os.urandom(N)
        self._wots_sk = []
        self._leaves = []
        for i in range(self.n_leaves):
            sk, pk = wots_keygen(seed=self._seed + i.to_bytes(4, "big"))
            self._wots_sk.append(sk)
            self._leaves.append(_wots_pk_hash(pk))
        self.public_key = merkle_root(self._leaves)     # корень = наш merkle.py

    @property
    def remaining(self) -> int:
        """Сколько подписей ещё можно поставить (одноразовость WOTS)."""
        return self.n_leaves - self.index

    def sign(self, message: bytes) -> dict:
        """Подписывает сообщение следующим неиспользованным ключом WOTS."""
        if self.index >= self.n_leaves:
            raise RuntimeError("ключи XMSS исчерпаны — нужно новое дерево")
        i = self.index
        self.index += 1
        return {
            "index": i,
            "wots": [s.hex() for s in wots_sign(self._wots_sk[i], message)],
            "auth": merkle_proof(self._leaves, i),
        }

    @staticmethod
    def verify(public_key: str, message: bytes, sig: dict) -> bool:
        """Проверка XMSS без секретов: восстановить лист и дойти до корня."""
        try:
            wsig = [bytes.fromhex(s) for s in sig["wots"]]
            leaf = _wots_pk_hash(wots_pk_from_sig(message, wsig))
            return verify_proof(leaf, sig["auth"], public_key)
        except (KeyError, TypeError, ValueError):
            return False


# =============================================================================
# QuantumWallet — демонстрационный квантово-устойчивый кошелёк
# =============================================================================
def _b58(data: bytes) -> str:
    from .wallet import _b58encode
    return _b58encode(data)


class QuantumWallet:
    """Квантово-устойчивый кошелёк на XMSS (адрес с префиксом BHYQ).

    Экспериментальный: подписи с состоянием (следит за израсходованными
    ключами). Для СВОЕЙ цепочки/консенсуса потребовался бы учёт индексов на
    уровне узла — здесь показан рабочий примитив и деривация адреса.
    """

    def __init__(self, height: int = 6, seed: bytes | None = None):
        self.signer = MerkleSigner(height=height, seed=seed)

    @property
    def public_key(self) -> str:
        return self.signer.public_key

    @property
    def address(self) -> str:
        """Адрес: BHYQ + base58(0x51 || ripemd160(sha512(pk)) || checksum)."""
        pk = bytes.fromhex(self.signer.public_key)
        h = hashing.ripemd160(hashing.sha512_bytes(pk))
        payload = b"\x51" + h                       # 0x51 — версия PQ-адреса
        checksum = hashing.double_sha512(payload)[:4]
        return "BHYQ" + _b58(payload + checksum)

    @property
    def remaining(self) -> int:
        return self.signer.remaining

    def sign(self, message) -> dict:
        if isinstance(message, str):
            message = message.encode("utf-8")
        return self.signer.sign(message)

    def verify(self, message, sig: dict) -> bool:
        if isinstance(message, str):
            message = message.encode("utf-8")
        return MerkleSigner.verify(self.public_key, message, sig)


if __name__ == "__main__":
    print("Пост-квантовые подписи B-hydra (на нашем SHA «с нуля»)\n")

    msg1 = "привет, квант".encode("utf-8")
    sk, pk = lamport_keygen()
    s = lamport_sign(sk, msg1)
    print("Lamport OTS: подпись верна:", lamport_verify(pk, msg1, s))

    msg2 = "перевод 10 BHY".encode("utf-8")
    sk, pk = wots_keygen()
    s = wots_sign(sk, msg2)
    print("WOTS      : подпись верна:", wots_verify(pk, msg2, s),
          f"| размер {WOTS_LEN * N} байт")

    w = QuantumWallet(height=4)
    print("\nQuantumWallet:")
    print("  адрес :", w.address)
    print("  ключей:", w.remaining, "подписей под одним публичным ключом")
    sig = w.sign("оплата за кофе")
    print("  подпись #%d верна:" % sig["index"],
          w.verify("оплата за кофе", sig))
    print("  подделка отвергнута:", not w.verify("оплата за ЧАЙ", sig))
