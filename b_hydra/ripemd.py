"""
ripemd.py — RIPEMD-160 «с нуля» (без hashlib), по спецификации Доббертина,
Боссэлерса и Пренееля (1996).

Зачем свой: RIPEMD-160 участвует в вычислении АДРЕСА
(`ripemd160(sha512(pub))`), но в сборках OpenSSL 3 его часто нет — там
`hashlib.new("ripemd160")` падает. Раньше проект в этом случае молча
подставлял `sha256(...)[:20]`, и узлы с разными сборками Python выводили бы
РАЗНЫЕ адреса из одного ключа. Теперь алгоритм есть всегда и считается
одинаково везде.

Устройство: 32-битные слова, порядок little-endian (в отличие от SHA-2), 80
шагов в ДВУХ параллельных линиях со своими таблицами порядка слов, сдвигов и
констант; результаты линий перемешиваются в конце каждого блока.

API как у hashlib: `Ripemd160()` с `update()/digest()/hexdigest()/copy()` плюс
разовые `ripemd160_bytes()/ripemd160()`. Вывод побитово совпадает с
эталонными векторами и с hashlib там, где тот умеет RIPEMD-160 (сверено в
тестах).
"""

from __future__ import annotations

_MASK32 = 0xFFFFFFFF

# Порядок слов сообщения: левая и правая линии берут их по разным таблицам.
_ORDER_LEFT = (
    0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15,
    7, 4, 13, 1, 10, 6, 15, 3, 12, 0, 9, 5, 2, 14, 11, 8,
    3, 10, 14, 4, 9, 15, 8, 1, 2, 7, 0, 6, 13, 11, 5, 12,
    1, 9, 11, 10, 0, 8, 12, 4, 13, 3, 7, 15, 14, 5, 6, 2,
    4, 0, 5, 9, 7, 12, 2, 10, 14, 1, 3, 8, 11, 6, 15, 13,
)
_ORDER_RIGHT = (
    5, 14, 7, 0, 9, 2, 11, 4, 13, 6, 15, 8, 1, 10, 3, 12,
    6, 11, 3, 7, 0, 13, 5, 10, 14, 15, 8, 12, 4, 9, 1, 2,
    15, 5, 1, 3, 7, 14, 6, 9, 11, 8, 12, 2, 10, 0, 4, 13,
    8, 6, 4, 1, 3, 11, 15, 0, 5, 12, 2, 13, 9, 7, 10, 14,
    12, 15, 10, 4, 1, 5, 8, 7, 6, 2, 13, 14, 0, 3, 9, 11,
)

# Величины циклических сдвигов на каждом из 80 шагов.
_SHIFT_LEFT = (
    11, 14, 15, 12, 5, 8, 7, 9, 11, 13, 14, 15, 6, 7, 9, 8,
    7, 6, 8, 13, 11, 9, 7, 15, 7, 12, 15, 9, 11, 7, 13, 12,
    11, 13, 6, 7, 14, 9, 13, 15, 14, 8, 13, 6, 5, 12, 7, 5,
    11, 12, 14, 15, 14, 15, 9, 8, 9, 14, 5, 6, 8, 6, 5, 12,
    9, 15, 5, 11, 6, 8, 13, 12, 5, 12, 13, 14, 11, 8, 5, 6,
)
_SHIFT_RIGHT = (
    8, 9, 9, 11, 13, 15, 15, 5, 7, 7, 8, 11, 14, 14, 12, 6,
    9, 13, 15, 7, 12, 8, 9, 11, 7, 7, 12, 7, 6, 15, 13, 11,
    9, 7, 15, 11, 8, 6, 6, 14, 12, 13, 5, 14, 13, 13, 7, 5,
    15, 5, 8, 11, 14, 14, 6, 14, 6, 9, 12, 9, 12, 5, 15, 8,
    8, 5, 12, 9, 12, 5, 14, 6, 8, 13, 6, 5, 15, 13, 11, 11,
)

# Раундовые константы: дробные части корней из 2, 3, 5, 7 (левая линия —
# квадратные, правая — кубические), крайние раунды идут без добавки.
_K_LEFT = (0x00000000, 0x5A827999, 0x6ED9EBA1, 0x8F1BBCDC, 0xA953FD4E)
_K_RIGHT = (0x50A28BE6, 0x5C4DD124, 0x6D703EF3, 0x7A6D76E9, 0x00000000)

_H0 = (0x67452301, 0xEFCDAB89, 0x98BADCFE, 0x10325476, 0xC3D2E1F0)


def _rotl(value: int, count: int) -> int:
    value &= _MASK32
    return ((value << count) | (value >> (32 - count))) & _MASK32


def _f(round_index: int, x: int, y: int, z: int) -> int:
    """Нелинейная функция раунда (своя на каждую пятёрку из 16 шагов)."""
    if round_index == 0:
        return x ^ y ^ z
    if round_index == 1:
        return (x & y) | (~x & _MASK32 & z)
    if round_index == 2:
        return (x | (~y & _MASK32)) ^ z
    if round_index == 3:
        return (x & z) | (y & ~z & _MASK32)
    return x ^ (y | (~z & _MASK32))


class Ripemd160:
    """Потоковый RIPEMD-160, совместимый по духу с hashlib."""

    block_size = 64
    digest_size = 20
    name = "ripemd160"

    def __init__(self, data: "bytes | str" = b""):
        self._h = list(_H0)
        self._buffer = bytearray()      # неполный «хвост» < block_size
        self._length = 0                # всего байт скормлено (для паддинга)
        if data:
            self.update(data)

    def update(self, data: "bytes | str") -> "Ripemd160":
        """Добавляет данные к сообщению (можно вызывать многократно)."""
        if isinstance(data, str):
            data = data.encode("utf-8")
        self._length += len(data)
        self._buffer += data
        full = len(self._buffer) - (len(self._buffer) % self.block_size)
        for base in range(0, full, self.block_size):
            self._compress(self._buffer[base:base + self.block_size])
        del self._buffer[:full]
        return self

    def _padded_tail(self) -> bytes:
        """Финальные блоки: 0x80, нули до 56 mod 64, длина в битах little-endian."""
        bit_len = (self._length * 8) & 0xFFFFFFFFFFFFFFFF
        pad = bytearray(self._buffer)
        pad.append(0x80)
        while len(pad) % self.block_size != 56:
            pad.append(0x00)
        pad += bit_len.to_bytes(8, "little")
        return bytes(pad)

    def _compress(self, block: bytes) -> None:
        # Слова блока — little-endian (у SHA-2 наоборот, big-endian).
        words = [int.from_bytes(block[i:i + 4], "little") for i in range(0, 64, 4)]

        a, b, c, d, e = self._h              # левая линия
        a2, b2, c2, d2, e2 = self._h         # правая линия
        for step in range(80):
            rnd = step // 16
            temp = (a + _f(rnd, b, c, d) + words[_ORDER_LEFT[step]]
                    + _K_LEFT[rnd]) & _MASK32
            temp = (_rotl(temp, _SHIFT_LEFT[step]) + e) & _MASK32
            a, e, d, c, b = e, d, _rotl(c, 10), b, temp

            # Правая линия идёт по тем же раундам в обратном порядке.
            temp = (a2 + _f(4 - rnd, b2, c2, d2) + words[_ORDER_RIGHT[step]]
                    + _K_RIGHT[rnd]) & _MASK32
            temp = (_rotl(temp, _SHIFT_RIGHT[step]) + e2) & _MASK32
            a2, e2, d2, c2, b2 = e2, d2, _rotl(c2, 10), b2, temp

        # Перемешивание линий: каждое слово состояния берёт вклад обеих.
        self._h = [
            (self._h[1] + c + d2) & _MASK32,
            (self._h[2] + d + e2) & _MASK32,
            (self._h[3] + e + a2) & _MASK32,
            (self._h[4] + a + b2) & _MASK32,
            (self._h[0] + b + c2) & _MASK32,
        ]

    def digest(self) -> bytes:
        """Дайджест текущего сообщения (не меняет состояние — можно продолжать)."""
        clone = self.copy()
        tail = clone._padded_tail()
        for base in range(0, len(tail), clone.block_size):
            clone._compress(tail[base:base + clone.block_size])
        return b"".join(word.to_bytes(4, "little") for word in clone._h)

    def hexdigest(self) -> str:
        return self.digest().hex()

    def copy(self) -> "Ripemd160":
        clone = Ripemd160.__new__(Ripemd160)
        clone._h = list(self._h)
        clone._buffer = bytearray(self._buffer)
        clone._length = self._length
        return clone


def ripemd160_bytes(message: "bytes | str") -> bytes:
    """RIPEMD-160 сообщения — 20 байт."""
    return Ripemd160(message).digest()


def ripemd160(message: "bytes | str") -> str:
    """RIPEMD-160 сообщения — hex-строка."""
    return Ripemd160(message).hexdigest()


if __name__ == "__main__":
    # Эталонные векторы из оригинальной спецификации RIPEMD-160.
    vectors = {
        "": "9c1185a5c5e9fc54612808977ee8f548b2258d31",
        "a": "0bdc9d2d256b3ee9daae347be6f4dc835a467ffe",
        "abc": "8eb208f7e05d987a9b044a8e98c6b087f15a0bfc",
        "message digest": "5d0689ef49d2fae572b881b123a85ffa21595f36",
    }
    for text, expected in vectors.items():
        got = ripemd160(text)
        print(f"{'OK ' if got == expected else 'FAIL'} ripemd160({text!r}) = {got}")
