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
from .merkle import _sha512d, verify_proof

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
class _Treehash:
    """Инстанс treehash: считает ОДИН узел уровня `level` по листу за шаг.

    Смысл в том, чтобы узел, который понадобится пути через 2^(level+1) подписей,
    строился ЗАРАНЕЕ и понемногу, а не целиком в тот момент, когда он нужен.
    Без этого одна подпись посреди дерева стоила бы половину всех листьев.
    """

    __slots__ = ("level", "next_leaf", "stack", "node", "_end")

    def __init__(self, level: int):
        self.level = level
        self.next_leaf = 0
        self.stack = []          # частичные узлы (хеш, уровень)
        self.node = None         # готовый узел уровня `level`
        self._end = 0

    def schedule(self, start: int, n_leaves: int) -> None:
        """Нацелить инстанс на поддерево, начинающееся с листа `start`.

        Если поддерево выходит за край дерева, инстанс просто не нужен: такой
        путь никогда не понадобится (у последнего листа соседей справа нет).
        """
        width = 1 << self.level
        self.stack = []
        self.node = None
        if start < 0 or start + width > n_leaves:
            self.next_leaf = self._end = 0     # неактивен
            return
        self.next_leaf = start
        self._end = start + width

    @property
    def active(self) -> bool:
        return self.node is None and self.next_leaf < self._end

    def update(self, leaf_of) -> None:
        """Один шаг: взять очередной лист и свернуть стек, пока можно."""
        node, level = leaf_of(self.next_leaf), 0
        self.next_leaf += 1
        while self.stack and self.stack[-1][1] == level:
            left, _ = self.stack.pop()
            node = _sha512d(left + node)
            level += 1
        if level == self.level:
            self.node = node
        else:
            self.stack.append((node, level))


class MerkleSigner:
    """XMSS-lite: 2^height одноразовых ключей WOTS под одним публичным ключом
    (корнем дерева Меркла). Подписи с СОСТОЯНИЕМ — индекс не переиспользуется.

    Публичный ключ = корень Меркла (hex, SHA-512 — как весь консенсус).
    Подпись = {index, wots, auth, alg}: WOTS-подпись листа + путь включения к
    корню + имя хеша схемы. Проверяющий восстанавливает публичный ключ WOTS из
    подписи, хеширует в лист и по пути доходит до корня — знать все листья не
    нужно.

    ОБХОД BDS. Листья НЕ ХРАНЯТСЯ: секретный ключ WOTS детерминированно выводится
    из `seed || i`, поэтому лист считается по требованию. Путь включения
    строится инкрементально (Buchmann–Dahmen–Szydlo, тот же приём, что в
    RFC 8391): состояние — O(h²) вместо O(2^h), подпись — O(h) хешей узлов
    вместо O(2^h).

    Замер:

        память состояния     было           стало
        h=4                    83 КБ          2 КБ
        h=8                  1275 КБ          4 КБ
        h=16              319 МБ (оценка)    ~6 КБ
        h=20                ~5 ТБ (оценка)   ~7 КБ

        хешей узлов на одну подпись   было (2^h − 1)   стало (≈0,65·h)
        h=4                                       15              1,2
        h=6                                       63              2,7
        h=8                                      255              4,6
        h=10                                    1023              6,5

    Память перестала зависеть от размера дерева вовсе — именно она, а не
    время, делала h ≥ 16 невозможным в принципе.

    ⚠️ ГЕНЕРАЦИЯ ОСТАЁТСЯ O(2^h), и это неустранимо: публичный ключ ЕСТЬ корень
    дерева над всеми 2^h листьями, значит каждый лист обязан быть посчитан хотя
    бы раз. Обход касается ПОДПИСЕЙ, а не генерации, и никакой алгоритм обхода
    этого не меняет. Измеренная цена листа WOTS — 227 мс на нашем чистом
    SHA-256 и 1,0 мс на быстром бэкенде (`BHYDRA_PURE_SHA=0`, байты те же):

        h=6       64 ключа       14,4 с  (замер)   /  0,07 с
        h=8      256 ключей      58,1 с  (замер)   /  0,26 с
        h=12    4096 ключей      15 мин            /  4 с
        h=16   65 536 ключей     4,1 ч             /  1,1 мин
        h=20    1 048 576        66 ч              /  17 мин

    То есть после BDS высоту ограничивает ОДНО ожидание при создании кошелька,
    а не память и не стоимость подписи. На чистом SHA практичны h ≤ 12, на
    быстром бэкенде — h ≤ 20.
    """

    def __init__(self, height: int = 4, seed: bytes | None = None, params=P256,
                 index: int = 0):
        if not 1 <= height <= 20:
            raise ValueError("height должен быть в диапазоне 1..20")
        self.height = height
        self.params = params
        self.n_leaves = 1 << height
        if not 0 <= index <= self.n_leaves:
            raise ValueError("index вне дерева")
        self.index = index
        self._seed = seed or os.urandom(params["n"])
        self._auth = [None] * height
        self._treehash = [_Treehash(level) for level in range(height)]
        self.public_key = self._rebuild(min(index, self.n_leaves - 1))

    # --- Листья по требованию -------------------------------------------
    def _wots_keys(self, i: int):
        """Пара ключей WOTS листа №i — выводится из seed, не хранится."""
        return wots_keygen(seed=self._seed + i.to_bytes(4, "big"),
                           params=self.params)

    def _leaf(self, i: int) -> bytes:
        """Лист №i дерева: хеш публичного ключа WOTS."""
        return _wots_pk_hash(self._wots_keys(i)[1], self.params)

    # --- Построение состояния обхода ------------------------------------
    @staticmethod
    def _treehash_target(index: int, level: int) -> int:
        """Номер узла уровня `level`, который понадобится пути СЛЕДУЮЩИМ.

        Путь на уровне l — это N(l, (i>>l) ^ 1). Когда (i>>l) чётный, сосед
        справа (свежее поддерево); когда нечётный — слева, а такой узел путь
        достраивает сам из уже известных. Поэтому заранее готовить надо только
        правых соседей, и ближайший из них — вот этот.
        """
        return ((index >> level) | 1) + 2

    def _rebuild(self, index: int) -> str:
        """Один проход по всем листьям: корень, путь для `index` и инстансы.

        ⚠️ Проход РОВНО ОДИН, поэтому восстановление кошелька с любого индекса
        стоит столько же, сколько создание нового, — 2^h листьев, не больше.
        Наивная альтернатива «проиграть все подписи от 0 до index» обошлась бы
        в index·h/2 листьев: для h=16 и index=50 000 это 400 000 листьев против
        65 536, то есть в шесть раз ДОРОЖЕ полной генерации.
        """
        height, n_leaves = self.height, self.n_leaves
        targets = [self._treehash_target(index, level) for level in range(height)]
        for level in range(height):
            self._treehash[level].schedule(targets[level] << level, n_leaves)

        stack = []
        for i in range(n_leaves):
            node, level = self._leaf(i), 0
            self._capture(node, level, i, index, targets)
            while stack and stack[-1][1] == level:
                left, _ = stack.pop()
                node = _sha512d(left + node)
                level += 1
                self._capture(node, level, i, index, targets)
            stack.append((node, level))
        return stack[-1][0].hex()

    def _capture(self, node: bytes, level: int, i: int, index: int,
                 targets: list) -> None:
        """Забрать узел, если он нужен пути или инстансу treehash.

        В потоковом обходе каждый внутренний узел появляется ровно один раз, и
        его номер на своём уровне равен i >> level. Достаточно сравнить.
        """
        if level >= self.height:
            return
        position = i >> level
        if position == (index >> level) ^ 1:
            self._auth[level] = node
        elif position == targets[level]:
            # Инстанс опережает график — это законно: важно лишь, чтобы узел
            # был готов к моменту, когда путь его затребует.
            self._treehash[level].node = node
            self._treehash[level].next_leaf = self._treehash[level]._end

    def _advance(self, i: int, leaf: bytes) -> None:
        """Перевести состояние с листа i на i+1.

        `leaf` — уже посчитанный лист №i: подпись его только что вывела, и
        считать второй раз незачем (это самая дорогая операция здесь).
        """
        height = self.height
        tau = 0
        while (i >> tau) & 1:
            tau += 1                       # число младших единиц в i

        # Узел, который ЗАВЕРШИЛСЯ этим листом: N(tau, i >> tau). Он собирается
        # из листа i и старых элементов пути — новых листьев не нужно вовсе.
        completed = leaf
        for level in range(tau):
            completed = _sha512d(self._auth[level] + completed)

        # Уровни ниже tau забирают готовые узлы у своих инстансов…
        for level in range(tau):
            self._auth[level] = self._treehash[level].node
        # …а уровень tau — только что достроенный узел.
        self._auth[tau] = completed

        nxt = i + 1
        for level in range(tau + 1):
            target = self._treehash_target(nxt, level)
            self._treehash[level].schedule(target << level, self.n_leaves)

        # Продвигаем незавершённые инстансы на один лист. Инстанс уровня l имеет
        # 2^(l+1) шагов на 2^l листьев, поэтому одного шага за подпись хватает с
        # запасом вдвое, а в среднем активна половина инстансов — это и даёт
        # обещанные O(h) вместо O(2^h).
        for instance in self._treehash:
            if instance.active:
                instance.update(self._leaf)

    # --- Подпись ---------------------------------------------------------
    @property
    def remaining(self) -> int:
        """Сколько подписей ещё можно поставить (одноразовость WOTS)."""
        return self.n_leaves - self.index

    def sign(self, message: bytes) -> dict:
        """Подписывает сообщение следующим неиспользованным ключом WOTS."""
        if self.index >= self.n_leaves:
            raise RuntimeError("ключи XMSS исчерпаны — нужно новое дерево")
        i = self.index
        sk, pk = self._wots_keys(i)
        signature = {
            "index": i,
            "alg": self.params["name"],
            "wots": [s.hex() for s in wots_sign(sk, message, self.params)],
            "auth": [{"hash": self._auth[level].hex(),
                      "position": "right" if (i >> level) % 2 == 0 else "left"}
                     for level in range(self.height)],
        }
        self.index = i + 1
        if self.index < self.n_leaves:
            self._advance(i, _wots_pk_hash(pk, self.params))
        return signature

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


class HybridWallet:
    """Гибридный кошелёк: ECDSA secp256k1 + пост-квантовая XMSS-подпись.

    Адрес привязан к ОБОИМ ключам, и трата требует ОБЕ подписи. Квантовый
    компьютер ломает лишь ECDSA (алгоритмом Шора), а XMSS на хешах устоит —
    поэтому монеты на гибридном адресе остаются недоступны атакующему. Это
    рекомендованная отраслью схема перехода: рабочая ECDSA не выбрасывается,
    квантовая защита добавляется поверх.

    XMSS-ключи одноразовые (2^height подписей), поэтому кошелёк следит за
    остатком, а узел — за израсходованными индексами (учёт в консенсусе).
    """

    def __init__(self, ecdsa_wallet=None, height: int = 8,
                 seed: bytes | None = None, strong: bool = False,
                 index: int = 0):
        from .wallet import generate_wallet
        self.ecdsa = ecdsa_wallet or generate_wallet()
        self.signer = MerkleSigner(height=height, seed=seed,
                                   params=P512 if strong else P256,
                                   index=index)

    @property
    def ecdsa_public_key_hex(self) -> str:
        return self.ecdsa.public_key_hex

    @property
    def pq_public_key(self) -> str:
        """Публичный пост-квантовый ключ (корень дерева XMSS)."""
        return self.signer.public_key

    @property
    def address(self) -> str:
        """Гибридный адрес BHY… (версия 0x2f), отпечаток обоих ключей."""
        from .wallet import hybrid_address
        return hybrid_address(self.ecdsa.public_key_bytes, self.signer.public_key)

    @property
    def remaining(self) -> int:
        """Сколько трат ещё доступно (одноразовость XMSS-ключей)."""
        return self.signer.remaining

    def sign(self, payload: bytes):
        """Подписывает байты обеими схемами → (ecdsa_sig_hex, pq_sig_dict)."""
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
        return self.ecdsa.sign(payload), self.signer.sign(payload)

    @staticmethod
    def verify(ecdsa_pub_hex: str, pq_root: str, payload: bytes,
               ecdsa_sig: str, pq_sig: dict) -> bool:
        """Проверяет ОБЕ подписи гибридного входа (ECDSA + XMSS)."""
        from .wallet import Wallet
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
        return (Wallet.verify(ecdsa_pub_hex, payload, ecdsa_sig)
                and MerkleSigner.verify(pq_root, payload, pq_sig))

    # --- Персистентность (с учётом израсходованных ключей) ---------------
    def to_dict(self) -> dict:
        """Состояние кошелька для сохранения. ВАЖНО: включает индекс — иначе
        при загрузке одноразовые XMSS-ключи начали бы переиспользоваться."""
        return {
            "ecdsa_private": self.ecdsa.private_key_hex,
            "seed": self.signer._seed.hex(),
            "height": self.signer.height,
            "alg": self.signer.params["name"],
            "index": self.signer.index,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "HybridWallet":
        """Восстанавливает кошелёк, включая состояние обхода дерева.

        ⚠️ Индекс передаётся В КОНСТРУКТОР, а не присваивается после. Раньше
        второго варианта хватало: дерево строилось целиком, и путь для любого
        листа брался из готового списка. Теперь путь — часть состояния обхода,
        и присвоение `index` задним числом оставило бы путь от нулевого листа:
        подписи выглядели бы нормально, но не сходились бы с корнем.
        Состояние в файл не пишется — оно детерминированно выводится из
        (seed, height, index), поэтому формат `bhydra_hybrid.json` не меняется.
        """
        from .wallet import Wallet
        return cls(
            ecdsa_wallet=Wallet.from_private_hex(data["ecdsa_private"]),
            height=data["height"],
            seed=bytes.fromhex(data["seed"]),
            strong=(data.get("alg") == "sha512"),
            index=data.get("index", 0),
        )


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
