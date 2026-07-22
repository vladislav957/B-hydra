"""
sha2.py — SHA-256 и SHA-512 «с нуля» по FIPS 180-4 (без hashlib).

Реализация написана вручную в учебных целях. SHA-256 и SHA-512 отличаются лишь
разрядностью слова (32 / 64 бита), числом раундов, длинами блока/поля длины и
величинами сдвигов в σ-функциях — поэтому обе схемы построены на ОДНОМ
параметризованном ядре (`_Sha2`), без дублирования кода.

Результат побитово совпадает со стандартом (сверено с векторами NIST и hashlib
в тестах). Помимо разовых функций есть потоковый API как у hashlib —
`Sha256()/Sha512()` с `update()/digest()/hexdigest()/copy()` для хеширования
данных по частям.

Внимание: чистый Python в сотни раз медленнее hashlib, поэтому в горячих путях
проекта используется подключаемый бэкенд (`hashing`). Этот модуль — корректный
эталон алгоритма.
"""

from __future__ import annotations


class _Sha2:
    """Общее ядро SHA-2, параметризованное набором констант конкретной схемы.

    Подклассы задают: разрядность слова, начальный хеш H0, раундовые константы
    K, число раундов, размеры блока/поля длины и сдвиги в Σ/σ-функциях.
    Экземпляр — потоковый хешер (update/digest), совместимый по духу с hashlib.
    """

    # Переопределяется в подклассах.
    _MASK = 0
    _WORD_BYTES = 0
    _ROUNDS = 0
    block_size = 0            # размер блока сжатия, байт
    digest_size = 0          # длина дайджеста, байт
    _LEN_BYTES = 0           # длина поля «размер сообщения», байт
    _H0: tuple = ()
    _K: tuple = ()
    # (Σ0), (Σ1), (σ0: r,r,shift), (σ1: r,r,shift) — величины сдвигов.
    _BIG_S0: tuple = ()
    _BIG_S1: tuple = ()
    _SMALL_S0: tuple = ()
    _SMALL_S1: tuple = ()

    def __init__(self, data: "bytes | str" = b""):
        self._h = list(self._H0)
        self._buffer = bytearray()      # неполный «хвост» < block_size
        self._length = 0                # всего байт скормлено (для паддинга)
        if data:
            self.update(data)

    # --- Побитовые примитивы --------------------------------------------
    def _rotr(self, x: int, n: int) -> int:
        bits = self._WORD_BYTES * 8
        return ((x >> n) | (x << (bits - n))) & self._MASK

    # --- Потоковый интерфейс --------------------------------------------
    def update(self, data: "bytes | str") -> "_Sha2":
        """Добавляет данные к сообщению (можно вызывать многократно)."""
        if isinstance(data, str):
            data = data.encode("utf-8")
        self._length += len(data)
        self._buffer += data
        # Сжимаем все накопившиеся полные блоки, остаток оставляем в буфере.
        full = len(self._buffer) - (len(self._buffer) % self.block_size)
        for base in range(0, full, self.block_size):
            self._compress(self._buffer[base:base + self.block_size])
        del self._buffer[:full]
        return self

    def _padded_tail(self) -> bytes:
        """Финальные блоки: 0x80, нули до выравнивания, затем длина в битах."""
        bit_len = (self._length * 8) & ((1 << (self._LEN_BYTES * 8)) - 1)
        pad = bytearray(self._buffer)
        pad.append(0x80)
        target = self.block_size - self._LEN_BYTES
        while len(pad) % self.block_size != target:
            pad.append(0x00)
        pad += bit_len.to_bytes(self._LEN_BYTES, "big")
        return bytes(pad)

    def digest(self) -> bytes:
        """Дайджест текущего сообщения (не меняет состояние — можно продолжать)."""
        clone = self.copy()
        tail = clone._padded_tail()
        for base in range(0, len(tail), clone.block_size):
            clone._compress(tail[base:base + clone.block_size])
        return b"".join(w.to_bytes(clone._WORD_BYTES, "big") for w in clone._h)

    def hexdigest(self) -> str:
        return self.digest().hex()

    def copy(self) -> "_Sha2":
        """Независимая копия состояния (как hashlib) — для ветвления хеша."""
        clone = self.__class__()
        clone._h = list(self._h)
        clone._buffer = bytearray(self._buffer)
        clone._length = self._length
        return clone

    # --- Сжатие одного блока (общее для обеих схем) ---------------------
    def _compress(self, block: "bytes | bytearray") -> None:
        wb, mask, rotr = self._WORD_BYTES, self._MASK, self._rotr
        (s0r0, s0r1, s0r2) = self._BIG_S0
        (s1r0, s1r1, s1r2) = self._BIG_S1
        (l0r0, l0r1, l0sh) = self._SMALL_S0
        (l1r0, l1r1, l1sh) = self._SMALL_S1

        w = [int.from_bytes(block[i * wb:i * wb + wb], "big") for i in range(16)]
        for i in range(16, self._ROUNDS):
            s0 = rotr(w[i - 15], l0r0) ^ rotr(w[i - 15], l0r1) ^ (w[i - 15] >> l0sh)
            s1 = rotr(w[i - 2], l1r0) ^ rotr(w[i - 2], l1r1) ^ (w[i - 2] >> l1sh)
            w.append((w[i - 16] + s0 + w[i - 7] + s1) & mask)

        a, b, c, d, e, f, g, h = self._h
        for i in range(self._ROUNDS):
            big_s1 = rotr(e, s1r0) ^ rotr(e, s1r1) ^ rotr(e, s1r2)
            ch = (e & f) ^ (~e & mask & g)
            t1 = (h + big_s1 + ch + self._K[i] + w[i]) & mask
            big_s0 = rotr(a, s0r0) ^ rotr(a, s0r1) ^ rotr(a, s0r2)
            maj = (a & b) ^ (a & c) ^ (b & c)
            t2 = (big_s0 + maj) & mask
            h, g, f, e = g, f, e, (d + t1) & mask
            d, c, b, a = c, b, a, (t1 + t2) & mask

        for i, val in enumerate((a, b, c, d, e, f, g, h)):
            self._h[i] = (self._h[i] + val) & mask


# --- SHA-256 -----------------------------------------------------------------
class Sha256(_Sha2):
    _MASK = 0xFFFFFFFF
    _WORD_BYTES = 4
    _ROUNDS = 64
    block_size = 64
    digest_size = 32
    _LEN_BYTES = 8
    _H0 = (0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
           0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19)
    _K = (
        0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1,
        0x923f82a4, 0xab1c5ed5, 0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3,
        0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786,
        0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
        0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147,
        0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13,
        0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b,
        0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
        0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a,
        0x5b9cca4f, 0x682e6ff3, 0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208,
        0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
    )
    _BIG_S0 = (2, 13, 22)
    _BIG_S1 = (6, 11, 25)
    _SMALL_S0 = (7, 18, 3)
    _SMALL_S1 = (17, 19, 10)


# --- SHA-512 -----------------------------------------------------------------
class Sha512(_Sha2):
    _MASK = 0xFFFFFFFFFFFFFFFF
    _WORD_BYTES = 8
    _ROUNDS = 80
    block_size = 128
    digest_size = 64
    _LEN_BYTES = 16
    _H0 = (0x6a09e667f3bcc908, 0xbb67ae8584caa73b, 0x3c6ef372fe94f82b,
           0xa54ff53a5f1d36f1, 0x510e527fade682d1, 0x9b05688c2b3e6c1f,
           0x1f83d9abfb41bd6b, 0x5be0cd19137e2179)
    _K = (
        0x428a2f98d728ae22, 0x7137449123ef65cd, 0xb5c0fbcfec4d3b2f, 0xe9b5dba58189dbbc,
        0x3956c25bf348b538, 0x59f111f1b605d019, 0x923f82a4af194f9b, 0xab1c5ed5da6d8118,
        0xd807aa98a3030242, 0x12835b0145706fbe, 0x243185be4ee4b28c, 0x550c7dc3d5ffb4e2,
        0x72be5d74f27b896f, 0x80deb1fe3b1696b1, 0x9bdc06a725c71235, 0xc19bf174cf692694,
        0xe49b69c19ef14ad2, 0xefbe4786384f25e3, 0x0fc19dc68b8cd5b5, 0x240ca1cc77ac9c65,
        0x2de92c6f592b0275, 0x4a7484aa6ea6e483, 0x5cb0a9dcbd41fbd4, 0x76f988da831153b5,
        0x983e5152ee66dfab, 0xa831c66d2db43210, 0xb00327c898fb213f, 0xbf597fc7beef0ee4,
        0xc6e00bf33da88fc2, 0xd5a79147930aa725, 0x06ca6351e003826f, 0x142929670a0e6e70,
        0x27b70a8546d22ffc, 0x2e1b21385c26c926, 0x4d2c6dfc5ac42aed, 0x53380d139d95b3df,
        0x650a73548baf63de, 0x766a0abb3c77b2a8, 0x81c2c92e47edaee6, 0x92722c851482353b,
        0xa2bfe8a14cf10364, 0xa81a664bbc423001, 0xc24b8b70d0f89791, 0xc76c51a30654be30,
        0xd192e819d6ef5218, 0xd69906245565a910, 0xf40e35855771202a, 0x106aa07032bbd1b8,
        0x19a4c116b8d2d0c8, 0x1e376c085141ab53, 0x2748774cdf8eeb99, 0x34b0bcb5e19b48a8,
        0x391c0cb3c5c95a63, 0x4ed8aa4ae3418acb, 0x5b9cca4f7763e373, 0x682e6ff3d6b2b8a3,
        0x748f82ee5defb2fc, 0x78a5636f43172f60, 0x84c87814a1f0ab72, 0x8cc702081a6439ec,
        0x90befffa23631e28, 0xa4506cebde82bde9, 0xbef9a3f7b2c67915, 0xc67178f2e372532b,
        0xca273eceea26619c, 0xd186b8c721c0c207, 0xeada7dd6cde0eb1e, 0xf57d4f7fee6ed178,
        0x06f067aa72176fba, 0x0a637dc5a2c898a6, 0x113f9804bef90dae, 0x1b710b35131c471b,
        0x28db77f523047d84, 0x32caab7b40c72493, 0x3c9ebe0a15c9bebc, 0x431d67c49c100d4c,
        0x4cc5d4becb3e42b6, 0x597f299cfc657e2a, 0x5fcb6fab3ad6faec, 0x6c44198c4a475817,
    )
    _BIG_S0 = (28, 34, 39)
    _BIG_S1 = (14, 18, 41)
    _SMALL_S0 = (1, 8, 7)
    _SMALL_S1 = (19, 61, 6)


# --- Разовые функции (обратная совместимость API) ----------------------------
def sha256_bytes(message: "bytes | str") -> bytes:
    """SHA-256 сырыми байтами (32 байта)."""
    return Sha256(message).digest()


def sha256(message: "bytes | str") -> str:
    """SHA-256 в виде hex-строки."""
    return Sha256(message).hexdigest()


def sha512_bytes(message: "bytes | str") -> bytes:
    """SHA-512 сырыми байтами (64 байта)."""
    return Sha512(message).digest()


def sha512(message: "bytes | str") -> str:
    """SHA-512 в виде hex-строки."""
    return Sha512(message).hexdigest()


if __name__ == "__main__":
    print("SHA-256('abc') =", sha256("abc"))
    print("SHA-512('abc') =", sha512("abc"))
    # Потоковый API идентичен разовому:
    h = Sha256()
    h.update("ab"); h.update("c")
    print("stream == oneshot:", h.hexdigest() == sha256("abc"))
