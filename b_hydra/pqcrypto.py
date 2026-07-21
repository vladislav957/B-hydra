"""
pqcrypto.py — пост-квантовые подписи B-hydra на хешах (экспериментально).

ECDSA (secp256k1) ломается алгоритмом Шора на квантовом компьютере. Хеш-подписи
квантово-устойчивы: алгоритм Гровера лишь вдвое ослабляет стойкость хеша
(SHA-256 → 128 бит, SHA-512 → 256 бит — всё ещё непробиваемо), а против самих
хешей квантовый компьютер бессилен. Здесь — три уровня, все на НАШЕМ SHA «с
нуля» (`hashing`/`sha2.py`), ноль зависимостей:

    Lamport  — простейшая ОДНОРАЗОВАЯ подпись (пара секретов на каждый бит);
    WOTS     — Winternitz OTS: компактнее Lamport, с контрольной суммой,
               которая не даёт подделать подпись под другое сообщение;
    XMSS     — МНОГОРАЗОВАЯ: дерево Меркла над 2^h ключами WOTS даёт один
               публичный ключ (корень) на 2^h подписей. Это строительный блок
               SPHINCS+ (FIPS 205) — и он переиспользует наше дерево Меркла.

В проекте есть ОБА наших хеша, поэтому схемы параметризованы:
    P256 (по умолчанию) — элементы SHA-256: 128 бит квантовой стойкости,
                          уровень NIST (как SPHINCS+-128), компактные подписи;
    P512 («параноидальный») — элементы SHA-512: 256 бит стойкости даже после
                          Гровера, подписи вдвое-вчетверо больше.
Дерево Меркла поверх листьев в обоих режимах — SHA-512 (как весь консенсус).

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

# --- Наборы параметров -------------------------------------------------------
# n — байт в элементе подписи; len1 — цифр базы-16 в дайджесте (2 на байт);
# len2 — цифр контрольной суммы: максимум len1·15 (1920 для SHA-512) < 16³.
P256 = {"name": "sha256", "n": 32, "hash": hashing.sha256_bytes,
        "len1": 64, "len2": 3}
P512 = {"name": "sha512", "n": 64, "hash": hashing.sha512_bytes,
        "len1": 128, "len2": 3}
_BY_NAME = {"sha256": P256, "sha512": P512}

# Исторические константы режима по умолчанию (P256) — для совместимости.
N = P256["n"]
W = 16                     # основание Винтерница (цифра = 4 бита)
LOG_W = 4
WOTS_LEN = P256["len1"] + P256["len2"]     # 67 цепочек в режиме SHA-256


def _h(data: bytes, p=P256) -> bytes:
    """Базовый хеш схемы: наш SHA-256 или SHA-512 «с нуля»."""
    return p["hash"](data)


def _digest_bits(message: bytes, p=P256):
    """Биты дайджеста сообщения (старший бит первым): 256 или 512 штук."""
    d = _h(message, p)
    return [(byte >> (7 - i)) & 1 for byte in d for i in range(8)]


# =============================================================================
# 1. Lamport — одноразовая подпись (самая наглядная)
# =============================================================================
def lamport_keygen(seed: bytes | None = None, params=P256):
    """Пара ключей Lamport: пара секретов на каждый бит дайджеста.

    seed — для детерминированной генерации (иначе os.urandom). Возвращает
    (secret_key, public_key); публичный — хеши всех секретов.
    """
    bits = params["n"] * 8

    def _rand(i):
        return (_h(seed + i.to_bytes(4, "big"), params) if seed
                else os.urandom(params["n"]))

    sk = [(_rand(2 * i), _rand(2 * i + 1)) for i in range(bits)]
    pk = [(_h(a, params), _h(b, params)) for a, b in sk]
    return sk, pk


def lamport_sign(sk, message: bytes, params=P256):
    """Подпись: для каждого бита раскрываем соответствующий секрет из пары."""
    bits = _digest_bits(message, params)
    return [sk[i][bit] for i, bit in enumerate(bits)]


def lamport_verify(pk, message: bytes, sig, params=P256) -> bool:
    """Проверка: хеш раскрытого секрета совпадает с публичным для нужного бита."""
    if len(sig) != params["n"] * 8:
        return False
    bits = _digest_bits(message, params)
    return all(_h(sig[i], params) == pk[i][bit] for i, bit in enumerate(bits))


# =============================================================================
# 2. WOTS — Winternitz One-Time Signature (компактная одноразовая)
# =============================================================================
def _wots_len(p) -> int:
    return p["len1"] + p["len2"]


def _chain(x: bytes, steps: int, p=P256) -> bytes:
    """Применяет хеш к x подряд `steps` раз (звено цепочки Винтерница).

    (WOTS+ добавил бы здесь XOR с публичной маской, зависящей от номера шага, —
    защита от мульти-target-атак; для нашей учебной схемы достаточно классики.)
    """
    for _ in range(steps):
        x = _h(x, p)
    return x


def _wots_digits(message: bytes, p=P256):
    """Дайджест + контрольная сумма как len1+len2 цифр базы-16.

    Контрольная сумма растёт, когда цифры дайджеста уменьшаются — поэтому
    подделать подпись под другое сообщение нельзя: часть цепочек пришлось бы
    «отмотать назад», а это обращение хеша.
    """
    d = _h(message, p)
    digits = []
    for byte in d:
        digits.append(byte >> 4)          # старшая тетрада
        digits.append(byte & 0x0F)        # младшая тетрада
    checksum = sum(W - 1 - x for x in digits)
    len2 = p["len2"]
    csum = [(checksum >> (LOG_W * (len2 - 1 - i))) & (W - 1)
            for i in range(len2)]
    return digits + csum


def wots_keygen(seed: bytes | None = None, params=P256):
    """Пара ключей WOTS; публичный — каждый секрет прохеширован W-1 раз."""
    def _rand(i):
        return (_h(seed + b"wots" + i.to_bytes(4, "big"), params) if seed
                else os.urandom(params["n"]))
    sk = [_rand(i) for i in range(_wots_len(params))]
    pk = [_chain(x, W - 1, params) for x in sk]
    return sk, pk


def wots_sign(sk, message: bytes, params=P256):
    """Подпись WOTS: i-й секрет прохеширован d_i раз (d_i — i-я цифра)."""
    ds = _wots_digits(message, params)
    return [_chain(sk[i], ds[i], params) for i in range(_wots_len(params))]


def wots_pk_from_sig(message: bytes, sig, params=P256):
    """Восстанавливает публичный ключ WOTS из подписи (для XMSS и проверки)."""
    ds = _wots_digits(message, params)
    return [_chain(sig[i], W - 1 - ds[i], params)
            for i in range(_wots_len(params))]


def wots_verify(pk, message: bytes, sig, params=P256) -> bool:
    """Проверка WOTS: домотанная до конца подпись равна публичному ключу."""
    if len(sig) != _wots_len(params):
        return False
    return wots_pk_from_sig(message, sig, params) == list(pk)


def _wots_pk_hash(pk, p=P256) -> bytes:
    """Компактный хеш публичного ключа WOTS (лист дерева XMSS)."""
    return _h(b"".join(pk), p)


# =============================================================================
# 3. XMSS-lite — многоразовая подпись (дерево Меркла над ключами WOTS)
# =============================================================================
class MerkleSigner:
    """XMSS-lite: 2^height одноразовых ключей WOTS под одним публичным ключом
    (корнем дерева Меркла). Подписи с СОСТОЯНИЕМ — индекс не переиспользуется.

    Публичный ключ = корень Меркла (hex, SHA-512 — как весь консенсус).
    Подпись = {index, wots, auth, alg}: WOTS-подпись листа + путь включения к
    корню (наш `merkle_proof`) + имя хеша схемы. Проверяющий восстанавливает
    публичный ключ WOTS из подписи, хеширует в лист и по пути доходит до
    корня — знать все листья не нужно.
    """

    def __init__(self, height: int = 4, seed: bytes | None = None, params=P256):
        if not 1 <= height <= 20:
            raise ValueError("height должен быть в диапазоне 1..20")
        self.height = height
        self.params = params
        self.n_leaves = 1 << height
        self.index = 0
        self._seed = seed or os.urandom(params["n"])
        self._wots_sk = []
        self._leaves = []
        for i in range(self.n_leaves):
            sk, pk = wots_keygen(seed=self._seed + i.to_bytes(4, "big"),
                                 params=params)
            self._wots_sk.append(sk)
            self._leaves.append(_wots_pk_hash(pk, params))
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
            "alg": self.params["name"],
            "wots": [s.hex() for s in wots_sign(self._wots_sk[i], message,
                                                self.params)],
            "auth": merkle_proof(self._leaves, i),
        }

    @staticmethod
    def verify(public_key: str, message: bytes, sig: dict) -> bool:
        """Проверка XMSS без секретов: восстановить лист и дойти до корня.

        Режим хеша берётся из подписи (поле alg; по умолчанию sha256)."""
        try:
            p = _BY_NAME.get(sig.get("alg", "sha256"), P256)
            wsig = [bytes.fromhex(s) for s in sig["wots"]]
            leaf = _wots_pk_hash(wots_pk_from_sig(message, wsig, p), p)
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

    strong=False — элементы SHA-256 (128 бит квантовой стойкости, компактно);
    strong=True  — элементы SHA-512 (256 бит даже после Гровера, «паранойя»).

    Экспериментальный: подписи с состоянием (следит за израсходованными
    ключами). Для СВОЕЙ цепочки/консенсуса потребовался бы учёт индексов на
    уровне узла — здесь показан рабочий примитив и деривация адреса.
    """

    def __init__(self, height: int = 6, seed: bytes | None = None,
                 strong: bool = False):
        self.signer = MerkleSigner(height=height, seed=seed,
                                   params=P512 if strong else P256)

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
    for p in (P256, P512):
        sk, pk = wots_keygen(params=p)
        s = wots_sign(sk, msg2, params=p)
        size = len(s) * p["n"]
        print(f"WOTS-{p['name']}: подпись верна:",
              wots_verify(pk, msg2, s, params=p),
              f"| {len(s)} цепочек × {p['n']} Б = {size} байт")

    for strong in (False, True):
        w = QuantumWallet(height=4, strong=strong)
        sig = w.sign("оплата за кофе")
        print(f"\nQuantumWallet(strong={strong}):")
        print("  адрес :", w.address)
        print("  ключей:", w.remaining, "| режим:", sig["alg"])
        print("  подпись верна:", w.verify("оплата за кофе", sig),
              "| подделка отвергнута:", not w.verify("оплата за ЧАЙ", sig))
