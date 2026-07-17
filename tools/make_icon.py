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


def _head(d, center, angle, size, color, eye_color):
    """Змеиная голова в профиль: округлый череп, вытянутая морда и глаз.

    center — середина черепа, angle — куда смотрит морда (градусы),
    size — радиус черепа. Никаких стрелок: силуэт именно змеи."""
    a = math.radians(angle)
    ca, sa = math.cos(a), math.sin(a)

    def pt(dx, dy):  # локальные координаты (x — вперёд, к кончику морды)
        return (center[0] + dx * ca - dy * sa, center[1] + dx * sa + dy * ca)

    r = size
    # Череп — круг.
    d.ellipse([center[0] - r, center[1] - r,
               center[0] + r, center[1] + r], fill=color)
    # Морда — широкий клин вперёд (верхняя и нижняя челюсти единым силуэтом).
    snout = 1.9 * r
    d.polygon([pt(-0.15 * r, -0.95 * r), pt(snout, -0.55 * r),
               pt(snout, 0.55 * r), pt(-0.15 * r, 0.95 * r)], fill=color)
    # РАСКРЫТАЯ ПАСТЬ — клин цвета фона, врезанный спереди: сразу «змея».
    d.polygon([pt(snout + 0.05 * r, -0.5 * r), pt(0.25 * r, 0),
               pt(snout + 0.05 * r, 0.5 * r)], fill=eye_color)
    # Глаз — тёмная точка в верхней части черепа.
    ex, ey = pt(0.15 * r, -0.45 * r)
    er = 0.21 * r
    d.ellipse([ex - er, ey - er, ex + er, ey + er], fill=eye_color)


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

    # --- Гидра: свернувшееся тело и три ЗМЕИНЫЕ головы --------------------
    # Асимметричная композиция, головы смотрят в стороны (не трезубец!).
    eye = BG_TOP

    # Тело — кольцо (свернувшаяся змея) внизу, чуть смещено влево.
    bx, by = cx - 10 * SS, cy + 46 * SS
    ring_r, ring_w = 16 * SS, 14 * SS
    d.ellipse([bx - ring_r - ring_w, by - ring_r - ring_w,
               bx + ring_r + ring_w, by + ring_r + ring_w], fill=GREEN)
    d.ellipse([bx - ring_r + ring_w, by - ring_r + ring_w,
               bx + ring_r - ring_w, by + ring_r - ring_w], fill=BG_BOTTOM)
    # Кончик хвоста выглядывает из-под кольца вправо.
    _stroke_bezier(d, (bx + ring_r + ring_w * 0.4, by + 8 * SS),
                   (bx + ring_r + 16 * SS, by + 12 * SS),
                   (bx + ring_r + 24 * SS, by + 6 * SS),
                   (bx + ring_r + 30 * SS, by + 2 * SS),
                   10 * SS, 3 * SS, GREEN, GREEN)

    neck_w0, neck_w1 = 16 * SS, 11 * SS            # сужение к голове

    # Левая шея: выгибается влево, голова смотрит ВЛЕВО.
    _stroke_bezier(d, (bx - 16 * SS, by - 10 * SS),
                   (cx - 50 * SS, cy + 16 * SS), (cx - 58 * SS, cy - 12 * SS),
                   (cx - 42 * SS, cy - 24 * SS),
                   neck_w0, neck_w1, GREEN, MINT)
    _head(d, (cx - 46 * SS, cy - 28 * SS), 172, 12 * SS, MINT, eye)

    # Центральная шея: высокий S-изгиб, голова наверху смотрит ВПРАВО.
    _stroke_bezier(d, (bx + 4 * SS, by - 14 * SS),
                   (cx - 24 * SS, cy - 8 * SS), (cx + 14 * SS, cy - 28 * SS),
                   (cx - 2 * SS, cy - 48 * SS),
                   neck_w0, neck_w1, GREEN, CYAN)
    _head(d, (cx + 4 * SS, cy - 53 * SS), 4, 13 * SS, CYAN, eye)

    # Правая шея: изгибается вправо-вниз, голова смотрит ВПРАВО-ВНИЗ.
    _stroke_bezier(d, (bx + 18 * SS, by - 6 * SS),
                   (cx + 32 * SS, cy + 24 * SS), (cx + 52 * SS, cy + 4 * SS),
                   (cx + 44 * SS, cy - 12 * SS),
                   neck_w0, neck_w1, GREEN, MINT)
    _head(d, (cx + 49 * SS, cy - 17 * SS), 24, 12 * SS, MINT, eye)

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
