"""Генератор логотипа B-hydra (assets/bhydra.png и assets/bhydra.ico).

Рисует эмблему: тёмный фон с мягким градиентом, гексагон (символ блокчейна)
и ТРЁХГЛАВУЮ ГИДРУ — три изгибающиеся шеи со стреловидными головами растут
из одного основания (три головы = кошелёк · майнинг · сеть в одном клиенте).

Рисование в 4-кратном суперсэмплинге с последующим уменьшением — края
гладкие без специальных библиотек. Запуск:  python tools/make_icon.py
"""

import math
import os

from PIL import Image, ImageDraw

SIZE = 256              # итоговый размер
SS = 4                  # суперсэмплинг (рисуем в SIZE*SS, уменьшаем)
S = SIZE * SS

# Палитра бренда (как в веб-обозревателе и кошельке).
BG_TOP = (11, 18, 26, 255)      # тёмная ночь с холодным отливом
BG_BOTTOM = (6, 28, 22, 255)    # глубокая зелень внизу
HEX_DIM = (35, 78, 58, 255)     # приглушённый контур гексагона
GREEN = (63, 185, 80, 255)      # фирменный зелёный (#3fb950)
MINT = (86, 211, 100, 255)      # светлее — к головам
CYAN = (45, 212, 191, 255)      # бирюзовый кончик центральной головы


def _lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(4))


def _hexagon(center, radius):
    cx, cy = center
    return [
        (cx + radius * math.cos(math.radians(60 * i - 90)),
         cy + radius * math.sin(math.radians(60 * i - 90)))
        for i in range(6)
    ]


def _bezier(p0, p1, p2, p3, t):
    """Точка кубической кривой Безье в параметре t."""
    u = 1 - t
    return (u**3 * p0[0] + 3 * u**2 * t * p1[0] + 3 * u * t**2 * p2[0] + t**3 * p3[0],
            u**3 * p0[1] + 3 * u**2 * t * p1[1] + 3 * u * t**2 * p2[1] + t**3 * p3[1])


def _stroke_bezier(d, p0, p1, p2, p3, width0, width1, color0, color1, steps=120):
    """Толстая сглаженная кривая: круги вдоль Безье с плавным сужением
    и градиентом цвета от основания к голове."""
    for i in range(steps + 1):
        t = i / steps
        x, y = _bezier(p0, p1, p2, p3, t)
        r = (width0 + (width1 - width0) * t) / 2
        c = _lerp(color0, color1, t)
        d.ellipse([x - r, y - r, x + r, y + r], fill=c)


def _head(d, tip, angle, size, color):
    """Стреловидная голова гидры: наконечник копья, повёрнутый по ходу шеи."""
    a = math.radians(angle)
    ca, sa = math.cos(a), math.sin(a)

    def pt(dx, dy):  # локальные координаты (x — вперёд к кончику)
        return (tip[0] + dx * ca - dy * sa, tip[1] + dx * sa + dy * ca)

    d.polygon([pt(size, 0),                 # кончик
               pt(-size * 0.55, size * 0.62),
               pt(-size * 0.18, 0),          # выемка сзади (хищный силуэт)
               pt(-size * 0.55, -size * 0.62)],
              fill=color)


def make() -> None:
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # --- Фон: скруглённый квадрат с вертикальным градиентом --------------
    grad = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grad)
    for y in range(S):
        gd.line([(0, y), (S, y)], fill=_lerp(BG_TOP, BG_BOTTOM, y / S))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [8 * SS, 8 * SS, S - 8 * SS, S - 8 * SS], radius=48 * SS, fill=255)
    img.paste(grad, (0, 0), mask)

    # --- Гексагон (блокчейн): внешний приглушённый + внутренний яркий ----
    cx, cy = S / 2, S / 2
    d.polygon(_hexagon((cx, cy), 100 * SS), outline=HEX_DIM, width=3 * SS)
    d.polygon(_hexagon((cx, cy), 88 * SS), outline=GREEN, width=6 * SS)

    # --- Гидра: три шеи из одного основания -------------------------------
    base = (cx, cy + 60 * SS)                      # общее основание (туловище)
    r0 = 13 * SS                                   # толщина у основания
    d.ellipse([base[0] - r0, base[1] - r0 * 0.8,
               base[0] + r0, base[1] + r0 * 0.8], fill=GREEN)

    neck_w0, neck_w1 = 14 * SS, 8 * SS             # сужение к голове

    # Центральная шея — лёгкий S-изгиб, голова смотрит строго вверх.
    _stroke_bezier(d, base,
                   (cx - 8 * SS, cy + 18 * SS), (cx + 8 * SS, cy - 16 * SS),
                   (cx, cy - 42 * SS),
                   neck_w0, neck_w1, GREEN, CYAN)
    _head(d, (cx, cy - 58 * SS), -90, 19 * SS, CYAN)

    # Боковые шеи — плавный изгиб наружу, головы смотрят вверх-в-стороны.
    for sgn in (-1, 1):
        _stroke_bezier(d, base,
                       (cx + sgn * 30 * SS, cy + 26 * SS),
                       (cx + sgn * 50 * SS, cy - 2 * SS),
                       (cx + sgn * 46 * SS, cy - 26 * SS),
                       neck_w0, neck_w1, GREEN, MINT)
        _head(d, (cx + sgn * 53 * SS, cy - 41 * SS),
              -90 + sgn * 27, 17 * SS, MINT)

    # --- Уменьшение (сглаживание) и сохранение ---------------------------
    img = img.resize((SIZE, SIZE), Image.LANCZOS)
    os.makedirs("assets", exist_ok=True)
    img.save("assets/bhydra.png")
    # .ico с несколькими размерами (для Windows).
    img.save("assets/bhydra.ico",
             sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("Логотип создан: assets/bhydra.png, assets/bhydra.ico")


if __name__ == "__main__":
    make()
