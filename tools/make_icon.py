"""Генератор иконки B-hydra (assets/bhydra.png и assets/bhydra.ico).

Рисует логотип: тёмный фон, зелёный гексагон (символ блокчейна) и букву «B».
Запуск:  python tools/make_icon.py
"""

import math
import os

from PIL import Image, ImageDraw, ImageFont

SIZE = 256
BG = (13, 17, 23, 255)        # тёмный фон (#0d1117)
ACCENT = (63, 185, 80, 255)   # зелёный (#3fb950)
LIGHT = (230, 237, 243, 255)  # светлый текст


def _font(size: int) -> ImageFont.FreeTypeFont:
    for path in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/Library/Fonts/Arial Bold.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
    ):
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _hexagon(center, radius):
    cx, cy = center
    return [
        (cx + radius * math.cos(math.radians(60 * i - 90)),
         cy + radius * math.sin(math.radians(60 * i - 90)))
        for i in range(6)
    ]


def make() -> None:
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # Скруглённый тёмный фон.
    d.rounded_rectangle([8, 8, SIZE - 8, SIZE - 8], radius=48, fill=BG)

    # Гексагон (блокчейн).
    hexg = _hexagon((SIZE / 2, SIZE / 2), 86)
    d.polygon(hexg, outline=ACCENT, width=10)

    # Буква «B».
    font = _font(132)
    d.text((SIZE / 2, SIZE / 2 - 6), "B", font=font, fill=LIGHT, anchor="mm")

    os.makedirs("assets", exist_ok=True)
    img.save("assets/bhydra.png")
    # .ico с несколькими размерами (для Windows).
    img.save("assets/bhydra.ico",
             sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("Иконка создана: assets/bhydra.png, assets/bhydra.ico")


if __name__ == "__main__":
    make()
