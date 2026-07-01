"""
qrcode_gen.py — генератор QR-кодов B-hydra с нуля (без сторонних библиотек).

В духе проекта (свой SHA, своя ECDSA) — свой QR-кодировщик. Поддерживает
байтовый режим (mode 0100), версии 1–10, уровень коррекции M. Этого с запасом
хватает на адрес B-hydra (Base58, ~34–37 символов).

Алгоритм по стандарту ISO/IEC 18004:
  1. кодирование данных (режим + счётчик + байты + терминатор + паддинг);
  2. коррекция ошибок Рида — Соломона над GF(256);
  3. размещение в матрице (поисковые узоры, тайминг, выравнивание, данные);
  4. восемь масок, выбор по штрафу, информация о формате (BCH).

Использование:
    from b_hydra.qrcode_gen import qr_matrix
    rows = qr_matrix("BHYD…")          # список строк из 0/1
"""

if __name__ == "__main__" and __package__ in (None, ""):
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    __package__ = "b_hydra"

# --- Галуа GF(256), порождающий многочлен x^8+x^4+x^3+x^2+1 (0x11d) ---------
_EXP = [0] * 512
_LOG = [0] * 256
_x = 1
for _i in range(255):
    _EXP[_i] = _x
    _LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11d
for _i in range(255, 512):
    _EXP[_i] = _EXP[_i - 255]


def _gf_mul(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return _EXP[_LOG[a] + _LOG[b]]


def _rs_generator(n: int) -> list:
    """Порождающий многочлен RS степени n."""
    g = [1]
    for i in range(n):
        ng = [0] * (len(g) + 1)
        for j, c in enumerate(g):
            ng[j] ^= c
            ng[j + 1] ^= _gf_mul(c, _EXP[i])
        g = ng
    return g


def _rs_encode(data: list, n_ec: int) -> list:
    """Байты коррекции ошибок для блока данных."""
    gen = _rs_generator(n_ec)
    res = [0] * n_ec
    for byte in data:
        factor = byte ^ res[0]
        res = res[1:] + [0]
        for i in range(n_ec):
            res[i] ^= _gf_mul(gen[i + 1], factor)
    return res


# --- Параметры версий для уровня M (data codewords, ec/блок, число блоков) --
# (всего кодовых слов данных, EC-слов на блок, групп: [(блоков, слов данных), ...])
_VERSIONS_M = {
    1: (16, 10, [(1, 16)]),
    2: (28, 16, [(1, 28)]),
    3: (44, 26, [(1, 44)]),
    4: (64, 18, [(2, 32)]),
    5: (86, 24, [(2, 43)]),
    6: (108, 16, [(4, 27)]),
    7: (124, 18, [(4, 31)]),
    8: (154, 22, [(2, 38), (2, 39)]),
    9: (182, 22, [(3, 36), (2, 37)]),
    10: (216, 26, [(4, 43), (1, 44)]),
}

# Позиции центров узоров выравнивания по версиям (2–10).
_ALIGN = {
    1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30],
    6: [6, 34], 7: [6, 22, 38], 8: [6, 24, 42], 9: [6, 26, 46],
    10: [6, 28, 50],
}


def _choose_version(n_bytes: int) -> int:
    for v in range(1, 11):
        data_cw = _VERSIONS_M[v][0]
        # 1 байт под режим+счётчик заголовка (4 бита режима + 8/16 бит счётчика).
        cc_bits = 16 if v >= 10 else 8
        header_bits = 4 + cc_bits
        capacity = data_cw * 8 - header_bits
        if n_bytes * 8 <= capacity:
            return v
    raise ValueError("данные слишком длинные для QR версии ≤10")


def _encode_data(text: str, version: int) -> list:
    """Кодирует строку в кодовые слова данных (байтовый режим)."""
    raw = text.encode("utf-8")
    data_cw, _, _ = _VERSIONS_M[version]
    cc_bits = 16 if version >= 10 else 8

    bits = []

    def put(value, length):
        for i in range(length - 1, -1, -1):
            bits.append((value >> i) & 1)

    put(0b0100, 4)               # режим: байтовый
    put(len(raw), cc_bits)       # счётчик символов
    for byte in raw:
        put(byte, 8)
    # Терминатор (до 4 нулей).
    cap = data_cw * 8
    put(0, min(4, cap - len(bits)))
    # Дополнить до целого байта.
    while len(bits) % 8:
        bits.append(0)
    # Байты-заполнители 0xEC / 0x11.
    codewords = [int("".join(str(b) for b in bits[i:i + 8]), 2)
                 for i in range(0, len(bits), 8)]
    pad = [0xEC, 0x11]
    k = 0
    while len(codewords) < data_cw:
        codewords.append(pad[k % 2])
        k += 1
    return codewords


def _interleave(codewords: list, version: int) -> list:
    """Разбить на блоки, посчитать EC и чередовать слова данных и EC."""
    _, ec_per_block, groups = _VERSIONS_M[version]
    blocks = []
    pos = 0
    for count, dwords in groups:
        for _ in range(count):
            data = codewords[pos:pos + dwords]
            pos += dwords
            blocks.append((data, _rs_encode(data, ec_per_block)))

    result = []
    max_data = max(len(d) for d, _ in blocks)
    for i in range(max_data):
        for data, _ in blocks:
            if i < len(data):
                result.append(data[i])
    for i in range(ec_per_block):
        for _, ec in blocks:
            result.append(ec[i])
    return result


# --- Построение матрицы -----------------------------------------------------
def _new_matrix(size: int):
    return [[None] * size for _ in range(size)]


def _place_finder(m, r, c):
    for dr in range(-1, 8):
        for dc in range(-1, 8):
            rr, cc = r + dr, c + dc
            if 0 <= rr < len(m) and 0 <= cc < len(m):
                # Узор 7x7 с рамкой-разделителем.
                if dr in (0, 6) and 0 <= dc <= 6 or dc in (0, 6) and 0 <= dr <= 6:
                    m[rr][cc] = 1
                elif 2 <= dr <= 4 and 2 <= dc <= 4:
                    m[rr][cc] = 1
                else:
                    m[rr][cc] = 0


def _place_alignment(m, version):
    centers = _ALIGN[version]
    size = len(m)
    for r in centers:
        for c in centers:
            # Не накладывать на поисковые узоры.
            if (r < 8 and c < 8) or (r < 8 and c > size - 9) or (r > size - 9 and c < 8):
                continue
            for dr in range(-2, 3):
                for dc in range(-2, 3):
                    edge = max(abs(dr), abs(dc))
                    m[r + dr][c + dc] = 1 if edge in (0, 2) else 0


def _place_timing(m):
    size = len(m)
    for i in range(8, size - 8):
        bit = 1 - (i % 2)
        if m[6][i] is None:
            m[6][i] = bit
        if m[i][6] is None:
            m[i][6] = bit


def _reserve_format(m):
    """Зарезервировать модули формата/версии (помечаем как занятые = 0)."""
    size = len(m)
    for i in range(9):
        if m[8][i] is None:
            m[8][i] = 0
        if m[i][8] is None:
            m[i][8] = 0
    for i in range(8):
        if m[8][size - 1 - i] is None:
            m[8][size - 1 - i] = 0
        if m[size - 1 - i][8] is None:
            m[size - 1 - i][8] = 0
    m[size - 8][8] = 1  # тёмный модуль


def _place_data(m, bits):
    """Зигзаг снизу-справа, столбцами по два, обходя занятые модули."""
    size = len(m)
    idx = 0
    upward = True
    col = size - 1
    while col > 0:
        if col == 6:        # пропустить вертикальный тайминг
            col -= 1
        rows = range(size - 1, -1, -1) if upward else range(size)
        for r in rows:
            for c in (col, col - 1):
                if m[r][c] is None:
                    bit = bits[idx] if idx < len(bits) else 0
                    m[r][c] = bit
                    idx += 1
        upward = not upward
        col -= 2


_MASKS = [
    lambda r, c: (r + c) % 2 == 0,
    lambda r, c: r % 2 == 0,
    lambda r, c: c % 3 == 0,
    lambda r, c: (r + c) % 3 == 0,
    lambda r, c: (r // 2 + c // 3) % 2 == 0,
    lambda r, c: (r * c) % 2 + (r * c) % 3 == 0,
    lambda r, c: ((r * c) % 2 + (r * c) % 3) % 2 == 0,
    lambda r, c: ((r + c) % 2 + (r * c) % 3) % 2 == 0,
]


def _is_function(reserved, r, c):
    return reserved[r][c]


def _apply_mask(m, reserved, mask):
    size = len(m)
    out = [row[:] for row in m]
    fn = _MASKS[mask]
    for r in range(size):
        for c in range(size):
            if not reserved[r][c] and fn(r, c):
                out[r][c] ^= 1
    return out


def _penalty(m):
    size = len(m)
    score = 0
    # Правило 1: серии из 5+ одинаковых модулей.
    for line in (m, list(zip(*m))):
        for row in line:
            run, prev = 1, None
            for v in row:
                if v == prev:
                    run += 1
                else:
                    if run >= 5:
                        score += 3 + (run - 5)
                    run, prev = 1, v
            if run >= 5:
                score += 3 + (run - 5)
    # Правило 2: блоки 2x2.
    for r in range(size - 1):
        for c in range(size - 1):
            if m[r][c] == m[r][c + 1] == m[r + 1][c] == m[r + 1][c + 1]:
                score += 3
    # Правило 3: узор 1011101 с отступом.
    pat1 = [1, 0, 1, 1, 1, 0, 1, 0, 0, 0, 0]
    pat2 = [0, 0, 0, 0, 1, 0, 1, 1, 1, 0, 1]
    for line in (m, [list(col) for col in zip(*m)]):
        for row in line:
            for i in range(size - 10):
                seg = list(row[i:i + 11])
                if seg == pat1 or seg == pat2:
                    score += 40
    # Правило 4: отклонение доли тёмных модулей от 50 %.
    dark = sum(sum(row) for row in m)
    ratio = dark * 100 // (size * size)
    score += 10 * (abs(ratio - 50) // 5)
    return score


def _format_bits(mask):
    """15-битная информация о формате (уровень M=00) с BCH и маской XOR."""
    fmt = (0b00 << 3) | mask          # EC уровень M = 00
    val = fmt << 10
    gen = 0b10100110111
    rem = val
    for i in range(14, 9, -1):
        if rem & (1 << i):
            rem ^= gen << (i - 10)
    bits = ((fmt << 10) | rem) ^ 0b101010000010010
    return [(bits >> i) & 1 for i in range(14, -1, -1)]


def _place_format(m, mask):
    size = len(m)
    bits = _format_bits(mask)
    # Вокруг левого верхнего узла.
    coords1 = [(8, 0), (8, 1), (8, 2), (8, 3), (8, 4), (8, 5), (8, 7), (8, 8),
               (7, 8), (5, 8), (4, 8), (3, 8), (2, 8), (1, 8), (0, 8)]
    for bit, (r, c) in zip(bits, coords1):
        m[r][c] = bit
    # Вдоль правого верхнего и левого нижнего узлов.
    coords2 = [(size - 1, 8), (size - 2, 8), (size - 3, 8), (size - 4, 8),
               (size - 5, 8), (size - 6, 8), (size - 7, 8),
               (8, size - 8), (8, size - 7), (8, size - 6), (8, size - 5),
               (8, size - 4), (8, size - 3), (8, size - 2), (8, size - 1)]
    for bit, (r, c) in zip(bits, coords2):
        m[r][c] = bit


def qr_matrix(text: str) -> list:
    """Возвращает QR-код для строки как список строк из 0/1 (без рамки)."""
    version = _choose_version(len(text.encode("utf-8")))
    codewords = _encode_data(text, version)
    final = _interleave(codewords, version)
    bits = []
    for cw in final:
        for i in range(7, -1, -1):
            bits.append((cw >> i) & 1)

    size = 17 + version * 4
    m = _new_matrix(size)
    _place_finder(m, 0, 0)
    _place_finder(m, 0, size - 7)
    _place_finder(m, size - 7, 0)
    _place_alignment(m, version)
    _place_timing(m)
    _reserve_format(m)

    # Карта функциональных (зарезервированных) модулей — до размещения данных.
    reserved = [[m[r][c] is not None for c in range(size)] for r in range(size)]

    _place_data(m, bits)

    # Выбрать лучшую маску.
    best, best_score = None, None
    for mask in range(8):
        cand = _apply_mask(m, reserved, mask)
        _place_format(cand, mask)
        sc = _penalty(cand)
        if best_score is None or sc < best_score:
            best, best_score = cand, sc
    return ["".join(str(v) for v in row) for row in best]


def qr_text(text: str, quiet: int = 2) -> str:
    """ASCII-представление QR (для терминала), два модуля на символ."""
    rows = qr_matrix(text)
    size = len(rows)
    pad = "  " * (size + 2 * quiet)
    out = [pad] * quiet
    for row in rows:
        line = "  " * quiet
        line += "".join("██" if v == "1" else "  " for v in row)
        line += "  " * quiet
        out.append(line)
    out += [pad] * quiet
    return "\n".join(out)


if __name__ == "__main__":
    sample = "BHYDhAjTov9QXWR3nKovis28mX8cARWVUqtCn"
    print(f"QR для {sample}:\n")
    print(qr_text(sample))
    mat = qr_matrix(sample)
    print(f"\nверсия-размер: {len(mat)}×{len(mat)} модулей")
