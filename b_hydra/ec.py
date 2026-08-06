"""
ec.py — арифметика эллиптических кривых и ECDSA, параметризованные кривой.

ЗАЧЕМ ОТДЕЛЬНЫЙ МОДУЛЬ. Арифметика точек в проекте была написана ДВАЖДЫ:
`wallet.py` считал secp256k1, `certgen.py` — свою копию для P-256. Формулы там
почти одинаковые, и расхождение в одной из них не поймал бы ни один тест
другой. Теперь реализация одна, а кривая — параметр.

Побочный, но главный по величине эффект — СКОРОСТЬ. Прежний `_scalar_mult`
работал в аффинных координатах, а там каждое сложение точек требует деления по
модулю, то есть `pow(k, -1, p)`. Замер на secp256k1: одно умножение на скаляр —
385 инверсий, 5,5 мс из 7,0, то есть 79% всего времени уходило на них.

Здесь точки живут в ЯКОБИЕВЫХ координатах: (X, Y, Z) означает аффинную точку
(X/Z², Y/Z³). Деления при этом не нужны вовсе — знаменатель просто копится в Z,
и инверсия делается ОДИН раз в самом конце, при переводе обратно в аффинные.

⚠️ Это НЕ смена правила сети. Якобиевы координаты — другая запись той же самой
группы точек: результат совпадает с аффинным до последнего бита, меняется
только способ счёта. Множество принимаемых подписей то же, старые подписи и вся
существующая цепочка валидны как раньше. Сверка с независимой аффинной
реализацией — в `tests/test_ec.py`, и это там главный тест.

Второй приём — ПРИЁМ ШАМИРА в проверке подписи. Проверка ECDSA считает
u1·G + u2·Q, то есть два умножения на скаляр. Но их можно вести ОДНИМ циклом:
удвоение общее, а на каждом шаге прибавляется одна из четырёх заранее
посчитанных точек (O, G, Q, G+Q) по паре битов. Удвоений становится вдвое
меньше — а удвоение и есть основная работа.
"""

# --- Точка на бесконечности ---------------------------------------------------
# В якобиевых координатах это Z == 0. Наружу она отдаётся как None — так её
# обозначал прежний аффинный код, и вызывающие проверяют именно `is None`.
_INFINITY = (0, 0, 0)


class Curve:
    """Эллиптическая кривая y² = x³ + a·x + b над полем F_p.

    Кривая описывается пятёркой (p, a, b, G, n) — этого достаточно и для
    арифметики, и для ECDSA. Добавить новую кривую значит добавить строчку
    с параметрами, а не написать ещё одну реализацию.
    """

    __slots__ = ("name", "p", "a", "b", "g", "n", "h", "size", "_base_window")

    def __init__(self, name, p, a, b, gx, gy, n, h=1):
        self.name = name
        self.p = p
        self.a = a % p
        self.b = b % p
        self.g = (gx, gy)
        self.n = n
        self.h = h
        #: Длина координаты в байтах — для сериализации ключей и подписей.
        self.size = (p.bit_length() + 7) // 8
        self._base_window = None
        if not self.is_on_curve(self.g):
            raise ValueError(f"{name}: генератор не лежит на кривой")

    def __repr__(self):
        return f"<Curve {self.name}, {self.p.bit_length()} бит>"

    # --- Проверка принадлежности ---------------------------------------------
    def is_on_curve(self, point) -> bool:
        """Лежит ли точка на кривой. Бесконечность (None) точкой не считается.

        ⚠️ Это защита, а не формальность: подпись, проверенная на ЧУЖОЙ кривой,
        может сойтись для ключа, которым её не делали (invalid-curve атака).
        Поэтому чужой публичный ключ проверяется здесь до всякой арифметики.
        """
        if point is None:
            return False
        x, y = point
        if not (0 <= x < self.p and 0 <= y < self.p):
            return False
        return (y * y - (x * x * x + self.a * x + self.b)) % self.p == 0

    # --- Якобиевы координаты --------------------------------------------------
    def _double(self, point):
        """Удвоение точки в якобиевых координатах — ни одного деления."""
        x, y, z = point
        if z == 0 or y == 0:
            return _INFINITY
        p = self.p
        yy = y * y % p
        s = 4 * x * yy % p
        if self.a == 0:
            # secp256k1: слагаемое a·Z⁴ обращается в ноль, и два умножения
            # уходят целиком. Наша основная кривая идёт именно этой веткой.
            m = 3 * x * x % p
        else:
            zz = z * z % p
            m = (3 * x * x + self.a * zz % p * zz) % p
        x3 = (m * m - 2 * s) % p
        y3 = (m * (s - x3) - 8 * yy * yy) % p
        z3 = 2 * y * z % p
        return (x3, y3, z3)

    def _add(self, first, second):
        """Сложение двух точек в якобиевых координатах."""
        if first[2] == 0:
            return second
        if second[2] == 0:
            return first
        p = self.p
        x1, y1, z1 = first
        x2, y2, z2 = second
        z1z1 = z1 * z1 % p
        z2z2 = z2 * z2 % p
        u1 = x1 * z2z2 % p
        u2 = x2 * z1z1 % p
        s1 = y1 * z2 % p * z2z2 % p
        s2 = y2 * z1 % p * z1z1 % p
        if u1 == u2:
            # Одна и та же точка по X: либо это удвоение, либо P + (−P).
            return self._double(first) if s1 == s2 else _INFINITY
        h = (u2 - u1) % p
        r = (s2 - s1) % p
        hh = h * h % p
        hhh = hh * h % p
        u1hh = u1 * hh % p
        x3 = (r * r - hhh - 2 * u1hh) % p
        y3 = (r * (u1hh - x3) - s1 * hhh) % p
        z3 = z1 * z2 % p * h % p
        return (x3, y3, z3)

    def _to_affine(self, point):
        """Якобиевы → аффинные. Единственная инверсия за весь расчёт."""
        x, y, z = point
        if z == 0:
            return None
        zinv = pow(z, -1, self.p)
        zinv2 = zinv * zinv % self.p
        return (x * zinv2 % self.p, y * zinv2 % self.p * zinv % self.p)

    def _batch_to_affine(self, points):
        """Пачка точек в аффинные — ОДНОЙ инверсией на всю пачку (Монтгомери).

        Инверсия по модулю дороже умножения примерно в сотню раз, поэтому
        вместо m инверсий выгоднее посчитать префиксные произведения Z,
        обратить одно общее произведение и раскрутить обратно умножениями.
        Ради этого приёма и существует метод: таблица генератора состоит из
        сотен точек, и построение её «в лоб» стоило бы сотни инверсий.
        """
        p = self.p
        prefix = []
        acc = 1
        for _, _, z in points:
            prefix.append(acc)
            acc = acc * z % p
        inverse = pow(acc, -1, p)
        result = [None] * len(points)
        for index in range(len(points) - 1, -1, -1):
            x, y, z = points[index]
            zinv = inverse * prefix[index] % p
            inverse = inverse * z % p
            zinv2 = zinv * zinv % p
            result[index] = (x * zinv2 % p, y * zinv2 % p * zinv % p)
        return result

    @staticmethod
    def _to_jacobian(point):
        return _INFINITY if point is None else (point[0], point[1], 1)

    # --- Умножение на скаляр --------------------------------------------------
    def multiply(self, k: int, point):
        """k·P. Точка и результат аффинные, None — бесконечность."""
        if point is None:
            return None
        k %= self.n
        if k == 0:
            return None
        return self._to_affine(self._multiply_jacobian(k, self._to_jacobian(point)))

    def _multiply_jacobian(self, k: int, point):
        """Оконный метод, ширина 4: 15 предвычисленных кратных на точку.

        Двоичный метод тратил бы на каждый единичный бит по сложению — в
        среднем половину длины скаляра. Окно из четырёх битов даёт одно
        сложение на четыре бита ценой пятнадцати сложений в начале.
        """
        table = [_INFINITY, point]
        for _ in range(2, 16):
            table.append(self._add(table[-1], point))

        bits = k.bit_length()
        top = bits - bits % 4 if bits % 4 else bits - 4   # кратно 4, старшее окно
        result = _INFINITY
        for shift in range(top, -4, -4):
            if result[2]:                     # пока ничего не накоплено — не удваиваем
                result = self._double(self._double(
                    self._double(self._double(result))))
            digit = (k >> shift) & 0xF
            if digit:
                result = self._add(result, table[digit])
        return result

    def multiply_base(self, k: int):
        """k·G для генератора — с таблицей, посчитанной один раз на кривую.

        Генератор у кривой один и не меняется, поэтому предвычисление окупается
        сразу: подпись и вывод публичного ключа из приватного — это ровно k·G.
        Замер на secp256k1: 6,18 → 0,31 мс за вызов.

        ⚠️ Таблица строится ЛЕНИВО и стоит 12 мс с 185 КБ памяти (у 512-битной
        кривой — 62 мс и 499 КБ). Окупается с третьего вызова, поэтому процессу,
        которому нужна ровно одна подпись, она обходится в лишние ~6 мс. Для
        узла, кошелька и рукопожатий это выигрыш в разы, для однократного вызова
        CLI — потеря, теряющаяся на фоне загрузки цепочки с диска.
        """
        k %= self.n
        if k == 0:
            return None
        if self._base_window is None:
            self._base_window = self._build_base_window()
        result = _INFINITY
        table = self._base_window
        for index, chunk in enumerate(table):
            digit = (k >> (4 * index)) & 0xF
            if digit:
                result = self._add(result, chunk[digit])
        return self._to_affine(result)

    def _build_base_window(self):
        """Таблица кратных G: по 16 точек на каждые 4 бита скаляра.

        Так удвоений в цикле не остаётся вовсе — только сложения, по одному на
        каждые четыре бита. Память: (длина/4)×16 точек, для 256 бит это 1024
        тройки чисел, то есть считанные сотни килобайт.
        """
        rows = []
        current = self._to_jacobian(self.g)
        for _ in range((self.n.bit_length() + 3) // 4):
            row = [current]
            for _ in range(2, 16):
                row.append(self._add(row[-1], current))
            rows.append(row)
            for _ in range(4):
                current = self._double(current)

        # Приводим всю таблицу к аффинному виду ОДНОЙ инверсией на все точки
        # сразу: с Z = 1 каждое обращение к таблице дешевле, а платим за это
        # один раз. Замер построения для secp256k1: 24 → 12 мс (960 инверсий
        # превратились в одну; остаток — сами сложения точек).
        flat = self._batch_to_affine([point for row in rows for point in row])
        table = []
        for index in range(len(rows)):
            chunk = flat[index * 15:(index + 1) * 15]
            table.append([_INFINITY] + [(x, y, 1) for x, y in chunk])
        return table

    def multiply_add(self, k1: int, point1, k2: int, point2):
        """k1·P1 + k2·P2 одним проходом (приём Шамира).

        Наивно это два независимых умножения на скаляр. Здесь удвоение делается
        один раз на шаг и обслуживает оба слагаемых, а прибавляется одна из
        четырёх точек по паре битов. Именно эта форма и нужна ECDSA.
        """
        p1 = self._to_jacobian(point1)
        p2 = self._to_jacobian(point2)
        table = (_INFINITY, p1, p2, self._add(p1, p2))
        result = _INFINITY
        for shift in range(max(k1.bit_length(), k2.bit_length()) - 1, -1, -1):
            if result[2]:
                result = self._double(result)
            digit = ((k1 >> shift) & 1) | (((k2 >> shift) & 1) << 1)
            if digit:
                result = self._add(result, table[digit])
        return self._to_affine(result)

    # --- Аффинное сложение (совместимость и наглядность) ----------------------
    def add(self, first, second):
        """P + Q в аффинных координатах. None — бесконечность."""
        if first is None:
            return second
        if second is None:
            return first
        return self._to_affine(self._add(self._to_jacobian(first),
                                         self._to_jacobian(second)))

    # --- ECDSA ----------------------------------------------------------------
    def sign(self, private: int, z: int, nonces, low_s: bool = False):
        """ECDSA-подпись (r, s). `nonces` — генератор кандидатов в k.

        Нонс сюда ПЕРЕДАЁТСЯ, а не берётся из ГСЧ: в проекте он выводится
        детерминированно по RFC 6979. Повтор k у двух разных сообщений
        раскрывает приватный ключ элементарной алгеброй, поэтому случайности
        здесь быть не должно вовсе.
        """
        n = self.n
        for k in nonces:
            point = self.multiply_base(k)
            if point is None:
                continue
            r = point[0] % n
            if r == 0:
                continue
            s = pow(k, -1, n) * (z + r * private) % n
            if s == 0:
                continue
            if low_s and s > n // 2:
                s = n - s          # защита от ковкости подписи
            return r, s
        return None

    def verify(self, point, z: int, r: int, s: int) -> bool:
        """Проверка ECDSA. Точка обязана быть проверена на принадлежность."""
        n = self.n
        if not (1 <= r < n and 1 <= s < n):
            return False
        w = pow(s, -1, n)
        total = self.multiply_add(z * w % n, self.g, r * w % n, point)
        return total is not None and total[0] % n == r

    def public_key(self, private: int):
        """Публичная точка по приватному числу."""
        if not 1 <= private < self.n:
            raise ValueError("приватный ключ вне диапазона [1, n-1]")
        return self.multiply_base(private)


# --- Кривые -------------------------------------------------------------------
#: secp256k1 — кривая B-hydra: подписи транзакций, ECDH в шифрованном канале.
#: Коблица (a = 0), поэтому удвоение считается на два умножения дешевле.
SECP256K1 = Curve(
    "secp256k1",
    p=0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFC2F,
    a=0,
    b=7,
    gx=0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798,
    gy=0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8,
    n=0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141,
)

#: P-256 (secp256r1) — для TLS-сертификата (`certgen.py`). Нашу secp256k1 TLS
#: не принимает: её убрали из умолчаний. Отличие только в коэффициенте a.
SECP256R1 = Curve(
    "secp256r1",
    p=0xFFFFFFFF00000001000000000000000000000000FFFFFFFFFFFFFFFFFFFFFFFF,
    a=0xFFFFFFFF00000001000000000000000000000000FFFFFFFFFFFFFFFFFFFFFFFC,
    b=0x5AC635D8AA3A93E7B3EBBD55769886BC651D06B0CC53B0F63BCE3C3E27D2604B,
    gx=0x6B17D1F2E12C4247F8BCE6E563A440F277037D812DEB33A0F4A13945D898C296,
    gy=0x4FE342E2FE1A7F9B8EE7EB4A7C0F9E162BCE33576B315ECECBB6406837BF51F5,
    n=0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551,
)

#: brainpoolP512r1 (RFC 5639) — настоящая 512-битная кривая, для опытов.
#:
#: ⚠️ ЕЁ ЗДЕСЬ НЕТ РАДИ БЕЗОПАСНОСТИ, и в консенсус она не идёт. secp256k1 даёт
#: 128 бит стойкости — это 2¹²⁸ операций Полларда, столько не переберут никогда.
#: А от КВАНТОВОГО противника размер кривой не спасает вообще: Шор ломает любую
#: кривую независимо от длины поля. Ответ кванту в проекте другой и правильный —
#: гибридные адреса 0x2f с XMSS (хеш-подписи Шор не берёт).
#: Замер цены: умножение на скаляр 256 бит 1,9 мс против 11,0 мс на 512 битах,
#: то есть проверка подписи 4 мс против 22 мс.
#:
#: ⚠️ И имя: кривой «secp512k1» НЕ СУЩЕСТВУЕТ. Семейство SEC «k» (Коблица)
#: кончается на secp256k1, дальше идут только «r»: secp384r1 и secp521r1 (521
#: бит, а не 512 — там простое Мерсенна 2⁵²¹−1). Настоящая стандартная кривая
#: на 512 бит — вот эта, из RFC 5639. Своя кривая тут была бы худшим выбором:
#: подбор параметров требует счёта числа точек (алгоритм SEA), доказательства
#: простоты порядка и проверки MOV-вложения — взять p и b наугад значит,
#: возможно, получить сломанную кривую и не узнать об этом.
BRAINPOOLP512R1 = Curve(
    "brainpoolP512r1",
    p=0xAADD9DB8DBE9C48B3FD4E6AE33C9FC07CB308DB3B3C9D20ED6639CCA703308717D4D9B009BC66842AECDA12AE6A380E62881FF2F2D82C68528AA6056583A48F3,
    a=0x7830A3318B603B89E2327145AC234CC594CBDD8D3DF91610A83441CAEA9863BC2DED5D5AA8253AA10A2EF1C98B9AC8B57F1117A72BF2C7B9E7C1AC4D77FC94CA,
    b=0x3DF91610A83441CAEA9863BC2DED5D5AA8253AA10A2EF1C98B9AC8B57F1117A72BF2C7B9E7C1AC4D77FC94CADC083E67984050B75EBAE5DD2809BD638016F723,
    gx=0x81AEE4BDD82ED9645A21322E9C4C6A9385ED9F70B5D916C1B43B62EEF4D0098EFF3B1F78E2D0D48D50D1687B93B97D5F7C6D5047406A5E688B352209BCB9F822,
    gy=0x7DDE385D566332ECC0EABFA9CF7822FDF209F70024A57B1AA000C55B881F8111B2DCDE494A5F485E5BCA4BD88A2763AED1CA2B2FA8F0540678CD1E0F3AD80892,
    n=0xAADD9DB8DBE9C48B3FD4E6AE33C9FC07CB308DB3B3C9D20ED6639CCA70330870553E5C414CA92619418661197FAC10471DB1D381085DDADDB58796829CA90069,
)

#: Кривые по имени — для тестов и будущих экспериментов.
CURVES = {curve.name: curve for curve in (SECP256K1, SECP256R1, BRAINPOOLP512R1)}
