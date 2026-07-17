"""Подготовка иконок B-hydra из фирменного арта (assets/bhydra_master.png).

Официальный логотип — киберпанк-эмблема «B» (магента/циан неон) от автора
проекта. Скрипт вырезает из мастер-арта квадрат с эмблемой, скругляет углы
и сохраняет:
    assets/bhydra.png — 256×256 (иконка окна и шапка кошелька в GUI)
    assets/bhydra.ico — многоразмерная иконка Windows (для .exe)

Запуск:  python tools/make_icon.py
"""

import os

from PIL import Image, ImageDraw, ImageOps

MASTER = "assets/bhydra_master.png"
SIZE = 256
# Кроп эмблемы «B» с кольцом (координаты в мастер-арте 1254×1254):
# по вертикали берём чуть выше/ниже кольца, без текста «B-HYDRA» снизу.
CROP = (188, 15, 1068, 895)           # квадрат 880×880 вокруг эмблемы
CORNER_RADIUS = 48                     # скругление углов итоговой иконки


def make() -> None:
    img = Image.open(MASTER).convert("RGBA")
    img = img.crop(CROP)
    img = ImageOps.fit(img, (SIZE, SIZE), Image.LANCZOS)

    # Скруглённые углы (как у иконок приложений).
    mask = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, SIZE - 1, SIZE - 1], radius=CORNER_RADIUS, fill=255)
    img.putalpha(mask)

    os.makedirs("assets", exist_ok=True)
    img.save("assets/bhydra.png")
    # .ico с несколькими размерами (для Windows .exe).
    img.save("assets/bhydra.ico",
             sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("Иконки готовы: assets/bhydra.png, assets/bhydra.ico "
          f"(из {MASTER})")


if __name__ == "__main__":
    make()
