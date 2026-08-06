"""Единый движок эллиптических кривых (`b_hydra/ec.py`).

Арифметика точек была написана в проекте ДВАЖДЫ: своя в `wallet.py` для
secp256k1 и своя в `certgen.py` для P-256. Теперь реализация одна, а кривая —
параметр, и точки живут в якобиевых координатах (без деления в цикле).

⚠️ ГЛАВНОЕ ЗДЕСЬ — НЕ СКОРОСТЬ, А ТОЖДЕСТВЕННОСТЬ РЕЗУЛЬТАТА. Умножение на
скаляр считает подписи транзакций, то есть правило сети. Разойдись новый ответ
со старым хоть в одном бите — часть существующей цепочки стала бы невалидной.
Поэтому эталон здесь СВОЙ, независимый: наивная аффинная арифметика прямо в
тесте (`_ref_*`), написанная отдельно от `ec.py`. Сверка с самим собой ничего
не доказывает.

Второй эталон — ЧУЖОЙ: openssl умеет все три кривые, и его публичный ключ по
приватному числу обязан совпасть с нашим.
"""

import random
import re
import shutil
import subprocess

import pytest

from b_hydra import certgen, ec, wallet
from b_hydra.wallet import Wallet

ALL_CURVES = [ec.SECP256K1, ec.SECP256R1, ec.BRAINPOOLP512R1]
IDS = [curve.name for curve in ALL_CURVES]

OPENSSL = shutil.which("openssl")
#: Имена наших кривых в терминах openssl (P-256 он зовёт prime256v1).
OPENSSL_NAMES = {"secp256k1": "secp256k1", "secp256r1": "prime256v1",
                 "brainpoolP512r1": "brainpoolP512r1"}


# --- Независимый эталон: аффинная арифметика «в лоб» ---------------------------
def _ref_add(curve, first, second):
    """P + Q по школьным формулам, с делением по модулю на каждом шаге.

    Медленно и прямолинейно — ровно так, как было до `ec.py`. Ценность
    эталона в том, что он написан ОТДЕЛЬНО и не разделяет с проверяемым кодом
    ни строчки.
    """
    p = curve.p
    if first is None:
        return second
    if second is None:
        return first
    x1, y1 = first
    x2, y2 = second
    if x1 == x2 and (y1 + y2) % p == 0:
        return None                                   # P + (−P) = бесконечность
    if first == second:
        slope = (3 * x1 * x1 + curve.a) * pow(2 * y1, -1, p) % p
    else:
        slope = (y2 - y1) * pow(x2 - x1, -1, p) % p
    x3 = (slope * slope - x1 - x2) % p
    return (x3, (slope * (x1 - x3) - y1) % p)


def _ref_mul(curve, k, point):
    """k·P двоичным удвоением в аффинных координатах."""
    result, addend = None, point
    while k:
        if k & 1:
            result = _ref_add(curve, result, addend)
        addend = _ref_add(curve, addend, addend)
        k >>= 1
    return result


def _rng(seed):
    return random.Random(seed)


# --- Параметры кривых ----------------------------------------------------------
@pytest.mark.parametrize("curve", ALL_CURVES, ids=IDS)
def test_generator_lies_on_the_curve(curve):
    """Генератор обязан удовлетворять уравнению кривой.

    Опечатка в одной цифре параметра дала бы точку не на кривой, и вся
    арифметика поверх неё считала бы что-то другое, не жалуясь.
    """
    assert curve.is_on_curve(curve.g)


@pytest.mark.parametrize("curve", ALL_CURVES, ids=IDS)
def test_generator_has_the_declared_order(curve):
    """n·G = бесконечность, а (n−1)·G — ещё нет.

    Это проверяет ИМЕННО заявленный порядок: сойдись он меньшим, подписи
    считались бы в подгруппе, где стойкость кривой не та, что обещана.
    """
    last = curve.multiply(curve.n - 1, curve.g)
    assert last is not None
    assert _ref_add(curve, last, curve.g) is None


@pytest.mark.parametrize("curve", ALL_CURVES, ids=IDS)
def test_curve_parameters_are_prime_field(curve):
    assert curve.p > 3 and curve.p % 2 == 1
    assert curve.size == (curve.p.bit_length() + 7) // 8


def test_a_broken_curve_is_refused_at_construction():
    """Генератор не на кривой — ошибка сразу, а не молчаливый счёт."""
    with pytest.raises(ValueError):
        ec.Curve("битая", p=23, a=1, b=1, gx=1, gy=1, n=7)


def test_secp512k1_does_not_exist():
    """Напоминание в коде: кривой «secp512k1» нет ни в одном стандарте.

    Семейство SEC «k» (Коблица) кончается на secp256k1; дальше идут только
    «r» — secp384r1 и secp521r1. Настоящая стандартная кривая на 512 бит —
    brainpoolP512r1 из RFC 5639, она и лежит в таблице.
    """
    assert "secp512k1" not in ec.CURVES
    assert "brainpoolP512r1" in ec.CURVES
    assert ec.BRAINPOOLP512R1.p.bit_length() == 512


# --- Сверка с независимым эталоном (главное) ----------------------------------
@pytest.mark.parametrize("curve", ALL_CURVES, ids=IDS)
def test_scalar_multiplication_matches_the_reference(curve):
    """Якобиевы координаты обязаны давать ТУ ЖЕ точку, что аффинные."""
    rnd = _rng(f"mul-{curve.name}")
    for _ in range(10):
        k = rnd.randrange(1, curve.n)
        assert curve.multiply(k, curve.g) == _ref_mul(curve, k, curve.g)


@pytest.mark.parametrize("curve", ALL_CURVES, ids=IDS)
def test_base_point_table_matches_the_reference(curve):
    """Предвычисленная таблица генератора не должна менять ответ.

    Таблица — чистая оптимизация k·G: ошибка в ней дала бы неверный публичный
    ключ из приватного, то есть адрес, с которого нельзя потратить.
    """
    rnd = _rng(f"base-{curve.name}")
    for _ in range(10):
        k = rnd.randrange(1, curve.n)
        assert curve.multiply_base(k) == _ref_mul(curve, k, curve.g)


@pytest.mark.parametrize("curve", ALL_CURVES, ids=IDS)
def test_shamir_trick_matches_two_separate_multiplications(curve):
    """u1·G + u2·Q одним проходом == два умножения и сложение.

    Именно эту форму считает проверка подписи, поэтому расхождение здесь —
    это расхождение в том, какая транзакция валидна.
    """
    rnd = _rng(f"shamir-{curve.name}")
    for _ in range(8):
        k1 = rnd.randrange(1, curve.n)
        k2 = rnd.randrange(1, curve.n)
        q = _ref_mul(curve, rnd.randrange(1, curve.n), curve.g)
        expected = _ref_add(curve, _ref_mul(curve, k1, curve.g),
                            _ref_mul(curve, k2, q))
        assert curve.multiply_add(k1, curve.g, k2, q) == expected


@pytest.mark.parametrize("curve", ALL_CURVES, ids=IDS)
def test_addition_matches_the_reference(curve):
    rnd = _rng(f"add-{curve.name}")
    for _ in range(8):
        first = _ref_mul(curve, rnd.randrange(1, curve.n), curve.g)
        second = _ref_mul(curve, rnd.randrange(1, curve.n), curve.g)
        assert curve.add(first, second) == _ref_add(curve, first, second)


# --- Краевые случаи ------------------------------------------------------------
@pytest.mark.parametrize("curve", ALL_CURVES, ids=IDS)
def test_point_plus_its_negation_is_infinity(curve):
    """P + (−P) — бесконечность, а не «случайная точка».

    В якобиевых координатах это отдельная ветка: X совпадают, а Y — нет.
    Спутай её с удвоением, и проверка подписи начнёт принимать мусор.
    """
    x, y = curve.g
    assert curve.add(curve.g, (x, (-y) % curve.p)) is None


@pytest.mark.parametrize("curve", ALL_CURVES, ids=IDS)
def test_infinity_is_the_neutral_element(curve):
    assert curve.add(curve.g, None) == curve.g
    assert curve.add(None, curve.g) == curve.g
    assert curve.add(None, None) is None


@pytest.mark.parametrize("curve", ALL_CURVES, ids=IDS)
def test_trivial_scalars(curve):
    assert curve.multiply(0, curve.g) is None
    assert curve.multiply(curve.n, curve.g) is None       # n·G = бесконечность
    assert curve.multiply(1, curve.g) == curve.g
    assert curve.multiply_base(1) == curve.g
    assert curve.multiply(2, curve.g) == curve.add(curve.g, curve.g)
    assert curve.multiply(3, curve.g) == curve.add(curve.multiply(2, curve.g),
                                                   curve.g)
    assert curve.multiply(5, None) is None


@pytest.mark.parametrize("curve", ALL_CURVES, ids=IDS)
def test_scalar_wraps_around_the_order(curve):
    """(k + n)·P == k·P: порядок группы ровно n."""
    k = 12345
    assert curve.multiply(k + curve.n, curve.g) == curve.multiply(k, curve.g)


@pytest.mark.parametrize("curve", ALL_CURVES, ids=IDS)
def test_points_outside_the_curve_are_refused(curve):
    x, y = curve.g
    assert curve.is_on_curve((x, (y + 1) % curve.p)) is False
    assert curve.is_on_curve(None) is False
    assert curve.is_on_curve((x, y + curve.p)) is False   # координата вне поля
    assert curve.is_on_curve((-1, y)) is False


@pytest.mark.parametrize("curve", ALL_CURVES, ids=IDS)
def test_scalar_multiplication_is_distributive(curve):
    """(a + b)·G == a·G + b·G — независимая проверка алгеброй, без эталона."""
    a, b = 987654321, 123456789
    assert curve.multiply(a + b, curve.g) == curve.add(curve.multiply(a, curve.g),
                                                       curve.multiply(b, curve.g))


# --- ECDSA поверх любой кривой -------------------------------------------------
def _fixed_nonces(*values):
    return iter(values)


@pytest.mark.parametrize("curve", ALL_CURVES, ids=IDS)
def test_sign_and_verify_roundtrip(curve):
    rnd = _rng(f"ecdsa-{curve.name}")
    private = rnd.randrange(1, curve.n)
    public = curve.public_key(private)
    z = rnd.randrange(1, curve.n)
    r, s = curve.sign(private, z, _fixed_nonces(rnd.randrange(1, curve.n)))
    assert curve.verify(public, z, r, s) is True
    assert curve.verify(public, (z + 1) % curve.n, r, s) is False


@pytest.mark.parametrize("curve", ALL_CURVES, ids=IDS)
def test_verify_refuses_out_of_range_values(curve):
    """r и s обязаны лежать в (0, n) — иначе подпись не подпись."""
    rnd = _rng(f"range-{curve.name}")
    private = rnd.randrange(1, curve.n)
    public = curve.public_key(private)
    z = rnd.randrange(1, curve.n)
    r, s = curve.sign(private, z, _fixed_nonces(rnd.randrange(1, curve.n)))
    assert curve.verify(public, z, 0, s) is False
    assert curve.verify(public, z, r, 0) is False
    assert curve.verify(public, z, curve.n, s) is False
    assert curve.verify(public, z, r, curve.n) is False


@pytest.mark.parametrize("curve", ALL_CURVES, ids=IDS)
def test_signature_of_another_key_is_refused(curve):
    rnd = _rng(f"other-{curve.name}")
    z = rnd.randrange(1, curve.n)
    mine = rnd.randrange(1, curve.n)
    theirs = rnd.randrange(1, curve.n)
    r, s = curve.sign(theirs, z, _fixed_nonces(rnd.randrange(1, curve.n)))
    assert curve.verify(curve.public_key(mine), z, r, s) is False


def test_low_s_normalisation_is_optional():
    """Кошелёк нормализует s (ковкость подписи), сертификат — нет.

    X.509 нормализации не требует, и подпись сертификата обязана быть такой,
    какой её ждёт чужой стек. Поэтому это параметр, а не жёсткое правило.
    """
    curve = ec.SECP256K1
    rnd = _rng("low-s")
    private = rnd.randrange(1, curve.n)
    z = rnd.randrange(1, curve.n)
    k = rnd.randrange(1, curve.n)
    _, plain = curve.sign(private, z, _fixed_nonces(k))
    _, low = curve.sign(private, z, _fixed_nonces(k), low_s=True)
    assert low <= curve.n // 2
    assert low in (plain, curve.n - plain)


def test_public_key_refuses_a_scalar_out_of_range():
    with pytest.raises(ValueError):
        ec.SECP256K1.public_key(0)
    with pytest.raises(ValueError):
        ec.SECP256K1.public_key(ec.SECP256K1.n)


# --- Кошелёк не изменился (правило сети) ---------------------------------------
#: Подписи, снятые с реализации ДО перехода на якобиевы координаты. Приколочены
#: намеренно: любой будущий «безобидный» рефактор арифметики, который их
#: сдвинет, сломает совместимость со всей уже существующей цепочкой.
PINNED = [
    ("0000000000000000000000000000000000000000000000000000000000000001",
     "b-hydra",
     "BHYDZjuqcYg3kauAkAUSN1UPkMHP1tZ7vRqH1",
     "7a9877a9e351706d320308877cebf32e7fcd8deb7409106c9ae61ee1ae6caa68"
     "74be43e4e7892bc0ee66a6b1a70951b85fead7dbeb03b97c710cd3af8b9f00e4"),
    ("1111111111111111111111111111111111111111111111111111111111111111",
     "перевод 10 BHY",
     "BHYDVbNYFjaTuJjXXMikPAtviFU5nvfnfY8Ao",
     "55043b4c54dc010eb4a3f36b941f9b9c447c7bdec99eac1604ebe52b14200c69"
     "512362b974c8d3928eceece7a73763cb46860bcd32f55139ae3d5233ec2c1c52"),
    ("7fffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff",
     "x",
     "BHYDfCQeZuAKMQR2eiujqUC1pxeQQ4c7f4foB",
     "8576b5aaf34cf85f05813f31220ae2043e55a6c0d5e448c283f11a5a4d5a8999"
     "636b37c5427a824174d0ca3ad411be7dfabdecd0b36555a113a210b613c97273"),
]


@pytest.mark.parametrize("private_hex,message,address,expected", PINNED,
                         ids=["k=1", "k=0x11…", "k=2²⁵⁵−1"])
def test_signatures_are_unchanged_by_the_new_arithmetic(private_hex, message,
                                                        address, expected):
    """Известные подписи обязаны воспроизводиться байт-в-байт.

    Нонс детерминированный (RFC 6979), поэтому подпись — функция от ключа и
    сообщения, и её можно приколотить как эталон. Это самый прямой способ
    поймать смену правила сети: значения сняты с реализации ДО перехода на
    якобиевы координаты.
    """
    w = Wallet.from_private_hex(private_hex)
    payload = message.encode("utf-8")
    assert w.address == address, "адрес выводится из ключа — он тоже приколочен"
    assert w.sign(payload) == expected
    assert Wallet.verify(w.public_key_hex, payload, expected)


def test_wallet_still_exposes_the_curve_internals():
    """`secure.py` берёт отсюда _G/_P/_N и умножение — имена обязаны остаться."""
    assert wallet._P == ec.SECP256K1.p
    assert wallet._N == ec.SECP256K1.n
    assert wallet._G == ec.SECP256K1.g
    assert wallet._is_on_curve(wallet._G) is True
    assert wallet._scalar_mult(2, wallet._G) == ec.SECP256K1.multiply(2, wallet._G)


def test_ecdh_still_agrees_in_both_directions():
    """Общий секрет ECDH: a·(b·G) == b·(a·G). На нём держится шифрование канала."""
    rnd = _rng("ecdh")
    a = rnd.randrange(1, wallet._N)
    b = rnd.randrange(1, wallet._N)
    assert wallet._scalar_mult(a, wallet._scalar_mult(b, wallet._G)) == \
        wallet._scalar_mult(b, wallet._scalar_mult(a, wallet._G))


def test_certgen_uses_the_shared_engine():
    """У P-256 в certgen больше нет своей копии арифметики."""
    assert certgen.P256_P == ec.SECP256R1.p
    assert certgen.P256_N == ec.SECP256R1.n
    assert certgen.P256_G == ec.SECP256R1.g
    assert not hasattr(certgen, "_scalar_mult")


def test_certgen_signs_and_verifies_p256():
    private, public = certgen.generate_key()
    payload = "сертификат".encode("utf-8")
    signature = certgen.sign_p256(private, payload)
    assert certgen.verify_p256(public, payload, signature) is True
    assert certgen.verify_p256(public, b"other", signature) is False


# --- Чужой эталон: openssl -----------------------------------------------------
def _openssl_keypair(curve):
    """Ключ, сгенерированный openssl: (приватное число, публичная точка)."""
    name = OPENSSL_NAMES[curve.name]
    pem = subprocess.run([OPENSSL, "ecparam", "-name", name, "-genkey", "-noout"],
                         capture_output=True, timeout=120)
    if pem.returncode != 0:
        pytest.skip(f"openssl не знает кривую {name}")
    text = subprocess.run([OPENSSL, "ec", "-text", "-noout"], input=pem.stdout,
                          capture_output=True, timeout=120).stdout.decode()

    def block(label):
        match = re.search(label + r":\s*\n((?:\s+[0-9a-f:]+\n)+)", text)
        return bytes.fromhex(re.sub(r"[^0-9a-f]", "", match.group(1)))

    private = int.from_bytes(block("priv"), "big")
    point = block("pub")
    assert point[0] == 0x04, "ожидался несжатый публичный ключ"
    half = (len(point) - 1) // 2
    return private, (int.from_bytes(point[1:1 + half], "big"),
                     int.from_bytes(point[1 + half:], "big"))


@pytest.mark.skipif(not OPENSSL, reason="нет openssl")
@pytest.mark.parametrize("curve", ALL_CURVES, ids=IDS)
def test_openssl_agrees_about_the_public_key(curve):
    """Наше k·G обязано совпасть с публичным ключом от openssl.

    Это проверка ЧУЖИМ инструментом, и она сильнее всех предыдущих: сойдись
    наши параметры кривой или формулы неверно, openssl бы разошёлся. Заодно
    подтверждает, что константы всех трёх кривых записаны без опечаток.
    """
    private, expected = _openssl_keypair(curve)
    assert curve.is_on_curve(expected), "openssl выдал точку не на нашей кривой"
    assert curve.public_key(private) == expected
    assert curve.multiply(private, curve.g) == expected


@pytest.mark.skipif(not OPENSSL, reason="нет openssl")
@pytest.mark.parametrize("curve", ALL_CURVES, ids=IDS)
def test_openssl_key_survives_a_roundtrip_through_our_arithmetic(curve):
    """Точка openssl, умноженная на скаляр, остаётся на кривой и обратима."""
    private, point = _openssl_keypair(curve)
    doubled = curve.add(point, point)
    assert curve.is_on_curve(doubled)
    assert curve.multiply(2, point) == doubled
    assert curve.add(doubled, (doubled[0], (-doubled[1]) % curve.p)) is None
    assert private  # ключ прочитан, а не пустой
