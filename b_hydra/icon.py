"""
icon.py — иконка приложения (PNG) с нуля, без зависимостей.

Кошелёк ставится на телефон как обычное приложение (PWA), а для этого нужны
иконки: 192×192 и 512×512 в манифесте. Рисовать их в редакторе и класть
картинки в репозиторий не хочется — в проекте нет ни одного бинарного ресурса,
и PNG пришлось бы обновлять руками при любой правке. Здесь иконка СЧИТАЕТСЯ:
знак задан геометрией, а PNG (zlib + CRC32 из стандартной библиотеки)
собирается сам в любом размере.

Знак: буква «B» из вертикальной стойки и двух полуколец, залитая градиентом
бирюза→фиолет (те же цвета, что у кошелька и обозревателя), на тёмном фоне.
Фон занимает весь квадрат — иконка годится и как maskable: значимая часть
умещается в центральные 80%, поэтому Android может обрезать её под свою форму,
ничего не отрезав.
"""

from __future__ import annotations

import os
import struct
import zlib

BACKGROUND = (10, 18, 34)          # --abyss2 из wallet.html
TEAL = (46, 230, 192)              # --teal
VIOLET = (139, 108, 255)           # --violet
SUPERSAMPLE = 3                    # сглаживание: 3×3 подпикселя


def _coverage(px: float, py: float) -> float:
    """Доля пикселя, занятая знаком, в координатах 0..1 по обеим осям.

    Считается аналитически, а не по растру: знак остаётся чётким в любом
    размере, от 48 до 1024 пикселей.
    """
    # Стойка буквы.
    stem_x0, stem_x1 = 0.30, 0.40
    if stem_x0 <= px <= stem_x1 and 0.24 <= py <= 0.76:
        return 1.0
    # Два полукольца справа (верхнее и нижнее).
    for center_y in (0.375, 0.625):
        radius, thickness = 0.175, 0.10
        dx, dy = px - 0.40, py - center_y
        distance = (dx * dx + dy * dy) ** 0.5
        if px >= 0.40 and abs(distance - radius) <= thickness / 2:
            return 1.0
    return 0.0


def _pixel(x: int, y: int, size: int):
    """Цвет пикселя со сглаживанием по подвыборке."""
    hits = 0
    for sy in range(SUPERSAMPLE):
        for sx in range(SUPERSAMPLE):
            px = (x + (sx + 0.5) / SUPERSAMPLE) / size
            py = (y + (sy + 0.5) / SUPERSAMPLE) / size
            hits += _coverage(px, py)
    alpha = hits / (SUPERSAMPLE * SUPERSAMPLE)
    if alpha <= 0:
        return BACKGROUND
    # Градиент слева направо: бирюза → фиолет.
    t = min(1.0, max(0.0, (x / size - 0.28) / 0.34))
    mark = tuple(int(TEAL[i] + (VIOLET[i] - TEAL[i]) * t) for i in range(3))
    return tuple(int(BACKGROUND[i] + (mark[i] - BACKGROUND[i]) * alpha)
                 for i in range(3))


def _chunk(tag: bytes, data: bytes) -> bytes:
    """Блок PNG: длина, тег, данные, CRC32 от тега с данными."""
    return (struct.pack(">I", len(data)) + tag + data +
            struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))


def png_bytes(size: int = 192) -> bytes:
    """PNG-иконка заданного размера (8 бит, RGB, без палитры)."""
    raw = bytearray()
    for y in range(size):
        raw.append(0)                       # тип фильтра строки: без фильтра
        for x in range(size):
            raw.extend(_pixel(x, y, size))
    header = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)   # 2 = RGB
    return (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", header) +
            _chunk(b"IDAT", zlib.compress(bytes(raw), 9)) +
            _chunk(b"IEND", b""))


def ensure_files(folder: str, sizes=(192, 512)) -> list:
    """Создаёт недостающие иконки в каталоге. Возвращает пути созданных."""
    made = []
    for size in sizes:
        path = os.path.join(folder, f"icon-{size}.png")
        if os.path.exists(path):
            continue
        try:
            with open(path, "wb") as handle:
                handle.write(png_bytes(size))
        except OSError:
            continue          # каталог только для чтения — не повод падать
        made.append(path)
    return made


if __name__ == "__main__":
    for size in (192, 512):
        data = png_bytes(size)
        name = f"icon-{size}.png"
        with open(name, "wb") as handle:
            handle.write(data)
        print(f"{name}: {len(data)} байт")
