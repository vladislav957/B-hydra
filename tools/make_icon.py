"""Генератор логотипа B-hydra Core (assets/bhydra.png, .ico, bhydra_small.png).

Рисует «крипто-монету»: тёмный фон, металлически-зелёная монета с ободком,
насечками по краю (как гурт настоящей монеты), рельефной буквой «B», лёгким
шестиугольником-водяным знаком (блокчейн) и бликом. Рисуем в 4× размере и
уменьшаем — получаем гладкие края (антиалиасинг).

Запуск:  python tools/make_icon.py
"""

import math
import os

from PIL import Image, ImageDraw, ImageFilter, ImageFont

SIZE = 256                     # итоговый размер
SS = 4                         # супер-сэмплинг (рисуем крупнее, потом уменьшаем)
S = SIZE * SS

# Палитра B-hydra (тёмная тема + зелёный акцент).
BG_TOP = (19, 27, 38)          # фон сверху
BG_BOT = (9, 13, 19)           # фон снизу
RIM_LIGHT = (126, 231, 135)    # светлый край ободка (#7ee787)
RIM_DARK = (35, 134, 54)       # тёмный край ободка  (#238636)
DISC_TOP = (16, 45, 28)        # диск монеты, верх
DISC_BOT = (8, 24, 15)         # диск монеты, низ
LETTER = (222, 255, 227)       # буква «B»
LETTER_SHADOW = (5, 20, 10)    # тень буквы (рельеф)
HEX_WM = (126, 231, 135, 42)   # водяной знак-шестиугольник (полупрозрачный)


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


def _lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _v_gradient(size, top, bottom):
    """Вертикальный градиент top→bottom как RGBA-картинка."""
    img = Image.new("RGBA", (size, size))
    d = ImageDraw.Draw(img)
    for y in range(size):
        d.line([(0, y), (size, y)], fill=_lerp(top, bottom, y / size) + (255,))
    return img


def _diag_gradient(size, c1, c2):
    """Диагональный градиент (свет сверху-слева, тень снизу-справа)."""
    img = Image.new("RGBA", (size, size))
    d = ImageDraw.Draw(img)
    for y in range(size):
        for step in range(0, size, 4):          # полосами по 4 пикселя (быстрее)
            t = (step + y) / (2 * size)
            d.line([(step, y), (min(step + 4, size), y)],
                   fill=_lerp(c1, c2, t) + (255,))
    return img


def _circle_mask(size, radius, center=None, width=None):
    """Маска круга (или кольца, если задана width)."""
    cx, cy = center or (size / 2, size / 2)
    m = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(m)
    d.ellipse([cx - radius, cy - radius, cx + radius, cy + radius], fill=255)
    if width:
        d.ellipse([cx - radius + width, cy - radius + width,
                   cx + radius - width, cy + radius - width], fill=0)
    return m


def _hexagon(center, radius):
    cx, cy = center
    return [
        (cx + radius * math.cos(math.radians(60 * i - 90)),
         cy + radius * math.sin(math.radians(60 * i - 90)))
        for i in range(6)
    ]


def make() -> None:
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))

    # 1. Тёмный фон со скруглёнными углами и мягким градиентом.
    bg = _v_gradient(S, BG_TOP, BG_BOT)
    bg_mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(bg_mask).rounded_rectangle(
        [S * 0.03, S * 0.03, S * 0.97, S * 0.97], radius=S * 0.19, fill=255)
    img.paste(bg, (0, 0), bg_mask)

    cx = cy = S / 2
    R = S * 0.37                    # радиус монеты

    # 2. Мягкое зелёное свечение позади монеты.
    glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse(
        [cx - R * 1.22, cy - R * 1.22, cx + R * 1.22, cy + R * 1.22],
        fill=RIM_DARK + (90,))
    glow = glow.filter(ImageFilter.GaussianBlur(S * 0.045))
    glow_clipped = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    glow_clipped.paste(glow, (0, 0), bg_mask)   # не выходить за скруглённый фон
    img = Image.alpha_composite(img, glow_clipped)

    # 3. Ободок монеты: металлический диагональный градиент.
    rim_grad = _diag_gradient(S, RIM_LIGHT, RIM_DARK)
    rim_mask = _circle_mask(S, R, (cx, cy))
    img.paste(rim_grad, (0, 0), rim_mask)

    # 4. Насечки по краю (гурт), как у настоящей монеты.
    d = ImageDraw.Draw(img)
    for deg in range(0, 360, 6):
        a = math.radians(deg)
        x1 = cx + (R - S * 0.012) * math.cos(a)
        y1 = cy + (R - S * 0.012) * math.sin(a)
        x2 = cx + R * math.cos(a)
        y2 = cy + R * math.sin(a)
        d.line([(x1, y1), (x2, y2)], fill=(10, 30, 18, 255), width=int(S * 0.006))

    # 5. Внутренний диск (чуть темнее) + тонкое светлое кольцо.
    R2 = R * 0.86
    disc = _v_gradient(S, DISC_TOP, DISC_BOT)
    img.paste(disc, (0, 0), _circle_mask(S, R2, (cx, cy)))
    ring = _circle_mask(S, R2, (cx, cy), width=int(S * 0.008))
    ring_layer = Image.new("RGBA", (S, S), RIM_LIGHT + (170,))
    img.paste(ring_layer, (0, 0), ring)

    # 6. Водяной знак — шестиугольник (символ блокчейна) внутри диска.
    wm = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(wm).polygon(_hexagon((cx, cy), R2 * 0.82),
                               outline=HEX_WM, width=int(S * 0.012))
    img = Image.alpha_composite(img, wm)

    # 7. Буква «B» с рельефом: тень вниз-вправо, свет вверх-влево.
    d = ImageDraw.Draw(img)
    font = _font(int(S * 0.42))
    off = S * 0.008
    d.text((cx + off, cy - S * 0.02 + off), "B", font=font,
           fill=LETTER_SHADOW + (230,), anchor="mm")          # тень (рельеф)
    d.text((cx - off * 0.6, cy - S * 0.02 - off * 0.6), "B", font=font,
           fill=RIM_LIGHT + (120,), anchor="mm")               # светлая грань
    d.text((cx, cy - S * 0.02), "B", font=font,
           fill=LETTER + (255,), anchor="mm")                  # основная буква

    # 8. Блик сверху-слева (стекло/металл) — подрезан по кругу монеты.
    shine = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(shine).ellipse(
        [cx - R * 0.9, cy - R * 1.0, cx + R * 0.5, cy - R * 0.2],
        fill=(255, 255, 255, 46))
    shine = shine.filter(ImageFilter.GaussianBlur(S * 0.03))
    coin_mask = _circle_mask(S, R, (cx, cy))
    clipped = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    clipped.paste(shine, (0, 0), coin_mask)
    img = Image.alpha_composite(img, clipped)

    # 9. Уменьшаем с антиалиасингом и сохраняем.
    final = img.resize((SIZE, SIZE), Image.LANCZOS)
    os.makedirs("assets", exist_ok=True)
    final.save("assets/bhydra.png")
    final.save("assets/bhydra.ico",
               sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    final.resize((48, 48), Image.LANCZOS).save("assets/bhydra_small.png")
    print("Логотип создан: assets/bhydra.png, assets/bhydra.ico, assets/bhydra_small.png")


if __name__ == "__main__":
    make()
