"""
rsa.py — RSA с нуля: генерация ключей, OAEP, PSS, PKCS#1 v1.5.

⚠️ В КОНСЕНСУС B-hydra это не входит и входить не должно. Подписи транзакций
делает ECDSA на secp256k1 (`wallet.py`) — менять схему подписи значит менять
правило сети. RSA здесь самостоятельный модуль: он нужен там, где с нами
разговаривают чужие инструменты (подпись APK, сертификаты, обмен ключами со
старым софтом), и как честная реализация ещё одного формата — как свои SHA,
RIPEMD, QR, PNG и X.509 в этом же проекте.

ЧТО ДЕЛАЕТ ЭТУ РЕАЛИЗАЦИЮ НЕ УЧЕБНОЙ

«RSA» из учебника — `pow(m, e, n)` — СЛОМАН, и это не придирка:

  * без набивки шифр детерминированный: одинаковые сообщения дают одинаковый
    шифротекст, и короткое сообщение (например, сумму перевода) подбирают
    перебором;
  * малое сообщение при e=3 извлекается обычным кубическим корнем — модуль
    даже не участвует;
  * шифр малеабелен: c·s^e mod n превращает m в m·s, то есть подпись «переводу
    на 1 монету» можно превратить в подпись переводу на 1000;
  * подпись без набивки подделывается перемножением двух чужих подписей.

Поэтому здесь: **OAEP** для шифрования и **PSS** для подписи (RFC 8017), плюс
PKCS#1 v1.5 — он слабее, но без него не разговаривают почти все существующие
инструменты (тот же `jarsigner`).

Что ещё сделано по делу:

  * ключи через **CRT** — приватная операция вчетверо быстрее;
  * **ослепление** (blinding) приватной операции: без него время работы зависит
    от секретного показателя, и ключ вынимается замерами;
  * генерация простых с отсевом по малым простым и Миллером–Рабином, проверка
    |p − q| (близкие p и q факторизуются методом Ферма за секунды);
  * `d` считается по функции Кармайкла λ(n), а не по Эйлеру φ(n) — так требует
    современная редакция стандарта, показатель получается меньше и работа
    быстрее;
  * при неудачной расшифровке OAEP наружу уходит ОДНА ошибка без подробностей:
    подробное сообщение — это оракул, по которому шифротекст читают целиком
    (атака Мангера).

⚠️ ЧЕГО НЕ ДАЁТ. Арифметика больших чисел в Python не постоянная по времени, и
сделать её такой нельзя. Ослепление закрывает утечку через приватный показатель,
но от атакующего, который меряет время НА ТОЙ ЖЕ машине, чистый Python не
защитит никакая набивка. Для ключей на своём сервере это приемлемо, для смарт-
карты — нет.
"""

import secrets

if __name__ == "__main__" and __package__ in (None, ""):
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    __package__ = "b_hydra"

from . import hashing

#: Открытый показатель по умолчанию. 65537 = 2^16+1: простое, всего два
#: единичных бита (быстрое возведение) и достаточно большое, чтобы короткое
#: сообщение не извлекалось обычным корнем, как при e=3.
DEFAULT_EXPONENT = 65537

#: Минимальный размер ключа. 1024 бита факторизуются достижимыми ресурсами,
#: и стандарты запретили их ещё в 2010-х. Меньше 2048 не выпускаем.
MIN_BITS = 2048

#: Малые простые для быстрого отсева кандидатов: делимость на них отбраковывает
#: около 80% чисел ценой одного деления вместо возведения в степень.
_SMALL_PRIMES = [
    2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47, 53, 59, 61, 67, 71,
    73, 79, 83, 89, 97, 101, 103, 107, 109, 113, 127, 131, 137, 139, 149, 151,
    157, 163, 167, 173, 179, 181, 191, 193, 197, 199, 211, 223, 227, 229, 233,
    239, 241, 251, 257, 263, 269, 271, 277, 281, 283, 293, 307, 311, 313, 317,
    331, 337, 347, 349, 353, 359, 367, 373, 379, 383, 389, 397, 401, 409, 419,
    421, 431, 433, 439, 443, 449, 457, 461, 463, 467, 479, 487, 491, 499, 503,
    509, 521, 523, 541,
]


class RSAError(Exception):
    """Ошибка RSA. Намеренно ОДНА на все случаи — см. атаку Мангера."""


# --- Хеш-функции --------------------------------------------------------------
# Свои же (`hashing`), чтобы не тянуть чужую криптографию. Имя выбирается
# параметром: OAEP и PSS определены для любого хеша, а эталонные векторы и
# чужие инструменты чаще всего используют SHA-256.
_HASHES = {
    "sha256": (hashing.sha256_bytes, 32),
    "sha512": (hashing.sha512_bytes, 64),
}


def _hash(name):
    try:
        return _HASHES[name]
    except KeyError:
        raise RSAError(f"неизвестный хеш: {name}") from None


# --- Простые числа ------------------------------------------------------------
def _is_probable_prime(candidate: int, rounds: int = 64) -> bool:
    """Тест Миллера–Рабина со случайными основаниями.

    64 раунда — не перестраховка ради красивого числа: основания выбираются
    случайно, поэтому вероятность ошибки не более 4^-64, и это дешевле, чем
    доказательство простоты, при том же практическом результате.
    """
    if candidate < 2:
        return False
    for prime in _SMALL_PRIMES:
        if candidate == prime:
            return True
        if candidate % prime == 0:
            return False

    # candidate - 1 = 2^shift * odd
    odd = candidate - 1
    shift = 0
    while odd % 2 == 0:
        odd //= 2
        shift += 1

    for _ in range(rounds):
        base = secrets.randbelow(candidate - 3) + 2       # 2 ≤ base ≤ n-2
        value = pow(base, odd, candidate)
        if value in (1, candidate - 1):
            continue
        for _ in range(shift - 1):
            value = pow(value, 2, candidate)
            if value == candidate - 1:
                break
        else:
            return False
    return True


def _random_prime(bits: int, exponent: int) -> int:
    """Случайное простое заданной длины, пригодное как множитель модуля.

    Два старших бита выставлены: старший — чтобы длина была ровно `bits`,
    следующий — чтобы произведение двух таких простых имело ровно 2·bits бит
    (иначе модуль иногда получался бы на бит короче заказанного).

    `gcd(exponent, p-1) == 1` проверяется здесь же: иначе обратного к e не
    существует и ключ пришлось бы выбрасывать в самом конце.
    """
    if bits < 16:
        raise RSAError("слишком короткое простое")
    while True:
        candidate = secrets.randbits(bits) | (3 << (bits - 2)) | 1
        if _greatest_common_divisor(exponent, candidate - 1) != 1:
            continue
        if _is_probable_prime(candidate):
            return candidate


def _greatest_common_divisor(first: int, second: int) -> int:
    while second:
        first, second = second, first % second
    return first


def _least_common_multiple(first: int, second: int) -> int:
    return first // _greatest_common_divisor(first, second) * second


# --- Ключи --------------------------------------------------------------------
class PublicKey:
    """Открытый ключ RSA: модуль n и показатель e."""

    def __init__(self, n: int, e: int = DEFAULT_EXPONENT):
        if n < 3 or e < 3 or e % 2 == 0:
            raise RSAError("некорректный открытый ключ")
        self.n = int(n)
        self.e = int(e)

    @property
    def bits(self) -> int:
        return self.n.bit_length()

    @property
    def size(self) -> int:
        """Длина модуля в байтах — она же длина шифротекста и подписи."""
        return (self.n.bit_length() + 7) // 8

    def __repr__(self):
        return f"<RSA public {self.bits} бит, e={self.e}>"


class PrivateKey:
    """Закрытый ключ RSA вместе с параметрами CRT.

    CRT (китайская теорема об остатках) считает приватную операцию по двум
    половинам модуля: два возведения в степень вдвое меньших чисел вместо
    одного большого — примерно вчетверо быстрее.
    """

    def __init__(self, n, e, d, p, q, dp=None, dq=None, qinv=None):
        self.n, self.e, self.d = int(n), int(e), int(d)
        self.p, self.q = int(p), int(q)
        self.dp = int(dp) if dp is not None else self.d % (self.p - 1)
        self.dq = int(dq) if dq is not None else self.d % (self.q - 1)
        self.qinv = int(qinv) if qinv is not None else pow(self.q, -1, self.p)

    @property
    def bits(self) -> int:
        return self.n.bit_length()

    @property
    def size(self) -> int:
        return (self.n.bit_length() + 7) // 8

    def public(self) -> PublicKey:
        return PublicKey(self.n, self.e)

    def __repr__(self):
        return f"<RSA private {self.bits} бит>"


def generate(bits: int = MIN_BITS, exponent: int = DEFAULT_EXPONENT) -> PrivateKey:
    """Генерирует пару ключей RSA.

    ⚠️ `p` и `q` обязаны быть не только простыми, но и ДАЛЁКИМИ друг от друга.
    Близкие множители находит метод Ферма: он пробует n = a² − b² вокруг √n и
    при |p − q| порядка n^(1/4) справляется за секунды, каким бы длинным ни был
    ключ. Поэтому кандидат с близким соседом отбрасывается.
    """
    if bits < MIN_BITS:
        raise RSAError(f"ключ короче {MIN_BITS} бит небезопасен")
    if exponent < 3 or exponent % 2 == 0:
        raise RSAError("открытый показатель должен быть нечётным и ≥ 3")

    half = bits // 2
    p = _random_prime(half, exponent)
    while True:
        q = _random_prime(bits - half, exponent)
        if p == q:
            continue
        # |p − q| должно быть велико: иначе метод Ферма разложит модуль.
        if abs(p - q) >> (half - 100) == 0:
            continue
        n = p * q
        if n.bit_length() == bits:
            break

    # λ(n) вместо φ(n): показатель получается меньше, приватная операция
    # быстрее, а множество допустимых d включает эйлеровское.
    lam = _least_common_multiple(p - 1, q - 1)
    d = pow(exponent, -1, lam)
    if d < 1 << (bits // 2):
        # Слишком маленький d вскрывается атакой Винера — берём другую пару.
        return generate(bits, exponent)
    if p < q:
        # λ(n) симметрична, поэтому d от перестановки не меняется — меняется
        # только то, какой множитель обращается в qinv = q^-1 mod p.
        p, q = q, p
    return PrivateKey(n=p * q, e=exponent, d=d, p=p, q=q)


# --- Примитивы ----------------------------------------------------------------
def i2osp(value: int, length: int) -> bytes:
    """Целое → строка байтов фиксированной длины (RFC 8017)."""
    if value < 0 or value >= 1 << (8 * length):
        raise RSAError("число не помещается в отведённую длину")
    return value.to_bytes(length, "big")


def os2ip(data: bytes) -> int:
    """Строка байтов → целое (RFC 8017)."""
    return int.from_bytes(data, "big")


def _public_op(key: PublicKey, value: int) -> int:
    if not 0 <= value < key.n:
        raise RSAError("сообщение вне диапазона модуля")
    return pow(value, key.e, key.n)


def _private_op(key: PrivateKey, value: int) -> int:
    """Приватная операция: CRT + ОСЛЕПЛЕНИЕ.

    Ослепление обязательно. Без него время вычисления зависит от секретного
    показателя и от самого сообщения, и ключ восстанавливают, замеряя ответы
    сервера (классическая атака Кохера, и она работала на настоящих TLS).
    Умножая вход на случайное r^e, мы считаем то же самое, но с непредсказуемым
    для атакующего числом, а результат снимаем умножением на r^-1.
    """
    if not 0 <= value < key.n:
        raise RSAError("шифротекст вне диапазона модуля")

    while True:
        blind = secrets.randbelow(key.n - 2) + 2
        if _greatest_common_divisor(blind, key.n) == 1:
            break
    unblind = pow(blind, -1, key.n)
    blinded = value * pow(blind, key.e, key.n) % key.n

    # CRT: две степени по половинкам вместо одной большой.
    m1 = pow(blinded, key.dp, key.p)
    m2 = pow(blinded, key.dq, key.q)
    h = (key.qinv * (m1 - m2)) % key.p
    result = (m2 + h * key.q) % key.n

    result = result * unblind % key.n
    # Проверка результата: сбой в CRT (наведённая ошибка) раскрывает множители
    # модуля одним НОД. Дешевле проверить, чем потерять ключ.
    if pow(result, key.e, key.n) != value:
        raise RSAError("сбой приватной операции")
    return result


# --- MGF1 ---------------------------------------------------------------------
def mgf1(seed: bytes, length: int, hash_name: str = "sha256") -> bytes:
    """Функция генерации маски из RFC 8017 (приложение B.2)."""
    digest, _size = _hash(hash_name)
    out = bytearray()
    counter = 0
    while len(out) < length:
        out += digest(seed + i2osp(counter, 4))
        counter += 1
    return bytes(out[:length])


def _constant_time_equal(first: bytes, second: bytes) -> bool:
    """Сравнение без ранних выходов.

    Обычное `==` возвращается на первом различии, и по времени ответа
    подбирают правильное значение байт за байтом. Здесь время зависит только
    от ДЛИНЫ, а она и так известна.
    """
    if len(first) != len(second):
        return False
    difference = 0
    for left, right in zip(first, second):
        difference |= left ^ right
    return difference == 0


# --- OAEP (шифрование) --------------------------------------------------------
def encrypt_oaep(key: PublicKey, message: bytes, label: bytes = b"",
                 hash_name: str = "sha256") -> bytes:
    """Шифрование RSAES-OAEP (RFC 8017, §7.1.1).

    Набивка обязательна: без неё шифр детерминированный и малеабельный.
    Случайное зерно на каждое сообщение делает два шифрования одного и того же
    текста неразличимыми.
    """
    digest, hash_size = _hash(hash_name)
    size = key.size
    limit = size - 2 * hash_size - 2
    if limit < 0:
        raise RSAError("ключ слишком короткий для этого хеша")
    if len(message) > limit:
        raise RSAError(f"сообщение длиннее {limit} байт")

    label_hash = digest(label)
    padding = b"\x00" * (limit - len(message))
    data_block = label_hash + padding + b"\x01" + message
    seed = secrets.token_bytes(hash_size)

    masked_db = _xor(data_block, mgf1(seed, size - hash_size - 1, hash_name))
    masked_seed = _xor(seed, mgf1(masked_db, hash_size, hash_name))
    encoded = b"\x00" + masked_seed + masked_db
    return i2osp(_public_op(key, os2ip(encoded)), size)


def decrypt_oaep(key: PrivateKey, ciphertext: bytes, label: bytes = b"",
                 hash_name: str = "sha256") -> bytes:
    """Расшифровка RSAES-OAEP.

    ⚠️ Все неудачи дают ОДНУ И ТУ ЖЕ ошибку без подробностей, и проверки не
    прерываются на первой сработавшей. Иначе получается оракул: по тому, КАК
    именно сервер отверг шифротекст, его читают целиком (атака Мангера — это
    и есть OAEP-версия Bleichenbacher).
    """
    digest, hash_size = _hash(hash_name)
    size = key.size
    if len(ciphertext) != size or size < 2 * hash_size + 2:
        raise RSAError("расшифровка не удалась")

    encoded = i2osp(_private_op(key, os2ip(ciphertext)), size)
    leading = encoded[0]
    masked_seed = encoded[1:1 + hash_size]
    masked_db = encoded[1 + hash_size:]

    seed = _xor(masked_seed, mgf1(masked_db, hash_size, hash_name))
    data_block = _xor(masked_db, mgf1(seed, size - hash_size - 1, hash_name))

    label_ok = _constant_time_equal(data_block[:hash_size], digest(label))
    # Ищем разделитель 0x01, не прерываясь на нём: длина цикла не должна
    # зависеть от содержимого.
    separator = -1
    for index in range(hash_size, len(data_block)):
        byte = data_block[index]
        if byte == 0x01 and separator < 0:
            separator = index
        elif byte != 0x00 and separator < 0:
            separator = -2          # мусор до разделителя
    if leading != 0 or not label_ok or separator < 0:
        raise RSAError("расшифровка не удалась")
    return data_block[separator + 1:]


def _xor(first: bytes, second: bytes) -> bytes:
    return bytes(a ^ b for a, b in zip(first, second))


# --- PSS (подпись) ------------------------------------------------------------
def sign_pss(key: PrivateKey, message: bytes, salt_length: int = None,
             hash_name: str = "sha256") -> bytes:
    """Подпись RSASSA-PSS (RFC 8017, §8.1.1).

    PSS предпочтительнее PKCS#1 v1.5: у него есть доказательство стойкости,
    и подпись вероятностная — две подписи одного сообщения различаются.
    """
    digest, hash_size = _hash(hash_name)
    bits = key.n.bit_length() - 1
    size = (bits + 7) // 8
    if salt_length is None:
        salt_length = hash_size
    if size < hash_size + salt_length + 2:
        raise RSAError("ключ слишком короткий для такой соли")

    message_hash = digest(message)
    salt = secrets.token_bytes(salt_length)
    # Восемь нулевых байт впереди — так в стандарте: они отделяют хеш
    # сообщения от того, что подписывается, и мешают переносить подпись между
    # схемами.
    inner = digest(b"\x00" * 8 + message_hash + salt)

    padding = b"\x00" * (size - salt_length - hash_size - 2)
    data_block = padding + b"\x01" + salt
    masked_db = bytearray(_xor(data_block, mgf1(inner, size - hash_size - 1,
                                                hash_name)))
    # Лишние старшие биты обнуляются, иначе число превысит модуль.
    masked_db[0] &= 0xFF >> (8 * size - bits)
    encoded = bytes(masked_db) + inner + b"\xbc"
    return i2osp(_private_op(key, os2ip(encoded)), key.size)


def verify_pss(key: PublicKey, message: bytes, signature: bytes,
               salt_length: int = None, hash_name: str = "sha256") -> bool:
    """Проверка RSASSA-PSS. Любая неудача — просто False."""
    digest, hash_size = _hash(hash_name)
    if len(signature) != key.size:
        return False
    bits = key.n.bit_length() - 1
    size = (bits + 7) // 8
    if salt_length is None:
        salt_length = hash_size
    if size < hash_size + salt_length + 2:
        return False

    try:
        encoded = i2osp(_public_op(key, os2ip(signature)), size)
    except RSAError:
        return False
    if encoded[-1] != 0xBC:
        return False

    masked_db = encoded[:size - hash_size - 1]
    inner = encoded[size - hash_size - 1:-1]
    if masked_db[0] >> (8 - (8 * size - bits)) != 0:
        return False

    data_block = bytearray(_xor(masked_db, mgf1(inner, size - hash_size - 1,
                                                hash_name)))
    data_block[0] &= 0xFF >> (8 * size - bits)
    expected_padding = size - hash_size - salt_length - 2
    if any(data_block[:expected_padding]) or data_block[expected_padding] != 0x01:
        return False

    salt = bytes(data_block[expected_padding + 1:])
    recomputed = digest(b"\x00" * 8 + digest(message) + salt)
    return _constant_time_equal(recomputed, inner)


# --- PKCS#1 v1.5 (подпись) ----------------------------------------------------
# Схема старая и без доказательства стойкости, но её понимают все существующие
# инструменты (jarsigner, старые TLS-стеки), поэтому она нужна для совместимости.
# Префиксы DigestInfo — это DER-обёртка «идентификатор алгоритма + хеш».
_DIGEST_INFO = {
    "sha256": bytes.fromhex("3031300d060960864801650304020105000420"),
    "sha512": bytes.fromhex("3051300d060960864801650304020305000440"),
}


def sign_pkcs1v15(key: PrivateKey, message: bytes,
                  hash_name: str = "sha256") -> bytes:
    """Подпись RSASSA-PKCS1-v1_5 (RFC 8017, §8.2.1)."""
    digest, _size = _hash(hash_name)
    prefix = _DIGEST_INFO[hash_name]
    target = prefix + digest(message)
    if key.size < len(target) + 11:
        raise RSAError("ключ слишком короткий")
    padding = b"\xff" * (key.size - len(target) - 3)
    encoded = b"\x00\x01" + padding + b"\x00" + target
    return i2osp(_private_op(key, os2ip(encoded)), key.size)


def verify_pkcs1v15(key: PublicKey, message: bytes, signature: bytes,
                    hash_name: str = "sha256") -> bool:
    """Проверка PKCS#1 v1.5.

    ⚠️ Сравнивается ВСЯ строка набивки целиком, а не «нашёлся ли хеш в конце».
    Разбор по частям — это ошибка Блейхенбахера 2006 года: реализации,
    принимавшие мусор между набивкой и хешем, позволяли подделать подпись
    вообще без ключа, когда e = 3.
    """
    digest, _size = _hash(hash_name)
    if len(signature) != key.size:
        return False
    try:
        encoded = i2osp(_public_op(key, os2ip(signature)), key.size)
    except RSAError:
        return False
    prefix = _DIGEST_INFO.get(hash_name)
    if prefix is None:
        return False
    target = prefix + digest(message)
    padding = b"\xff" * (key.size - len(target) - 3)
    expected = b"\x00\x01" + padding + b"\x00" + target
    return _constant_time_equal(encoded, expected)


# --- Сериализация (PKCS#1 DER/PEM) --------------------------------------------
def _der_length(size: int) -> bytes:
    if size < 0x80:
        return bytes([size])
    raw = size.to_bytes((size.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(raw)]) + raw


def _der_integer(value: int) -> bytes:
    raw = value.to_bytes((value.bit_length() + 8) // 8 or 1, "big")
    return b"\x02" + _der_length(len(raw)) + raw


def _der_sequence(*parts: bytes) -> bytes:
    body = b"".join(parts)
    return b"\x30" + _der_length(len(body)) + body


def _der_read(data: bytes, offset: int):
    """Читает один TLV. Возвращает (тег, значение, следующий сдвиг)."""
    tag = data[offset]
    length = data[offset + 1]
    offset += 2
    if length & 0x80:
        count = length & 0x7F
        length = int.from_bytes(data[offset:offset + count], "big")
        offset += count
    return tag, data[offset:offset + length], offset + length


def _der_integers(data: bytes):
    tag, body, _end = _der_read(data, 0)
    if tag != 0x30:
        raise RSAError("это не последовательность DER")
    values, offset = [], 0
    while offset < len(body):
        tag, raw, offset = _der_read(body, offset)
        if tag != 0x02:
            raise RSAError("в ключе ожидались целые числа")
        values.append(int.from_bytes(raw, "big"))
    return values


def private_to_der(key: PrivateKey) -> bytes:
    """RSAPrivateKey из PKCS#1 — тот же формат, что читает openssl."""
    return _der_sequence(
        _der_integer(0), _der_integer(key.n), _der_integer(key.e),
        _der_integer(key.d), _der_integer(key.p), _der_integer(key.q),
        _der_integer(key.dp), _der_integer(key.dq), _der_integer(key.qinv))


def public_to_der(key: PublicKey) -> bytes:
    """RSAPublicKey из PKCS#1."""
    return _der_sequence(_der_integer(key.n), _der_integer(key.e))


# PKCS#8 и SubjectPublicKeyInfo — то, что современный openssl пишет ПО
# УМОЛЧАНИЮ («BEGIN PRIVATE KEY» и «BEGIN PUBLIC KEY»). Внутри лежит тот же
# PKCS#1, просто завёрнутый вместе с идентификатором алгоритма. Понимать надо
# оба формата: иначе ключ, выпущенный обычной командой openssl, не читается.
_OID_RSA = bytes.fromhex("06092a864886f70d010101")     # 1.2.840.113549.1.1.1
_ALGORITHM_ID = _der_sequence(_OID_RSA + b"\x05\x00")  # + NULL


def private_to_pkcs8(key: PrivateKey) -> bytes:
    """PKCS#8 PrivateKeyInfo — формат «BEGIN PRIVATE KEY»."""
    inner = private_to_der(key)
    return _der_sequence(_der_integer(0), _ALGORITHM_ID,
                         b"\x04" + _der_length(len(inner)) + inner)


def public_to_spki(key: PublicKey) -> bytes:
    """SubjectPublicKeyInfo — формат «BEGIN PUBLIC KEY»."""
    inner = public_to_der(key)
    bit_string = b"\x00" + inner        # 0 неиспользованных бит
    return _der_sequence(_ALGORITHM_ID,
                         b"\x03" + _der_length(len(bit_string)) + bit_string)


def _unwrap(data: bytes):
    """Достаёт PKCS#1 из PKCS#8 или SPKI. Возвращает None, если это не обёртка."""
    try:
        tag, body, _end = _der_read(data, 0)
        if tag != 0x30:
            return None
        offset = 0
        parts = []
        while offset < len(body):
            item_tag, item, offset = _der_read(body, offset)
            parts.append((item_tag, item))
    except (IndexError, ValueError):
        return None

    # PKCS#8: INTEGER 0, SEQUENCE(алгоритм), OCTET STRING(ключ)
    if len(parts) >= 3 and parts[0][0] == 0x02 and parts[1][0] == 0x30 \
            and parts[2][0] == 0x04:
        return parts[2][1]
    # SPKI: SEQUENCE(алгоритм), BIT STRING(ключ)
    if len(parts) == 2 and parts[0][0] == 0x30 and parts[1][0] == 0x03:
        return parts[1][1][1:]          # первый байт — число неиспользованных бит
    return None


def private_from_der(data: bytes) -> PrivateKey:
    """Читает RSAPrivateKey — как в PKCS#1, так и завёрнутый в PKCS#8."""
    inner = _unwrap(data)
    values = _der_integers(inner if inner is not None else data)
    if len(values) < 9 or values[0] != 0:
        raise RSAError("не похоже на RSAPrivateKey")
    _version, n, e, d, p, q, dp, dq, qinv = values[:9]
    return PrivateKey(n=n, e=e, d=d, p=p, q=q, dp=dp, dq=dq, qinv=qinv)


def public_from_der(data: bytes) -> PublicKey:
    """Читает RSAPublicKey — как в PKCS#1, так и в SubjectPublicKeyInfo."""
    inner = _unwrap(data)
    values = _der_integers(inner if inner is not None else data)
    if len(values) != 2:
        raise RSAError("не похоже на RSAPublicKey")
    return PublicKey(n=values[0], e=values[1])


def _pem(label: str, payload: bytes) -> str:
    import base64

    body = base64.b64encode(payload).decode("ascii")
    lines = [body[i:i + 64] for i in range(0, len(body), 64)]
    return (f"-----BEGIN {label}-----\n" + "\n".join(lines) +
            f"\n-----END {label}-----\n")


def _unpem(text: str) -> bytes:
    import base64

    body = "".join(line for line in text.splitlines()
                   if line and not line.startswith("-----"))
    return base64.b64decode(body)


def private_to_pem(key: PrivateKey, pkcs8: bool = False) -> str:
    """PEM закрытого ключа. `pkcs8=True` — современный «BEGIN PRIVATE KEY»."""
    if pkcs8:
        return _pem("PRIVATE KEY", private_to_pkcs8(key))
    return _pem("RSA PRIVATE KEY", private_to_der(key))


def public_to_pem(key: PublicKey, spki: bool = True) -> str:
    """PEM открытого ключа. По умолчанию «BEGIN PUBLIC KEY» — его ждут
    современные инструменты, в том числе `openssl dgst -verify`."""
    if spki:
        return _pem("PUBLIC KEY", public_to_spki(key))
    return _pem("RSA PUBLIC KEY", public_to_der(key))


def private_from_pem(text: str) -> PrivateKey:
    return private_from_der(_unpem(text))


def public_from_pem(text: str) -> PublicKey:
    return public_from_der(_unpem(text))


def _demo():
    import time

    print("RSA B-hydra — своя реализация\n")
    started = time.time()
    key = generate(2048)
    print(f"ключ 2048 бит: {time.time() - started:.2f} с")
    print(f"  {key}  →  {key.public()}")

    secret = "перевод 10 BHY".encode("utf-8")
    started = time.time()
    box = encrypt_oaep(key.public(), secret)
    opened = decrypt_oaep(key, box)
    print(f"\nOAEP: {len(box)} байт шифротекста, "
          f"расшифровано верно: {opened == secret} "
          f"({(time.time() - started) * 1000:.0f} мс)")
    print(f"  два шифрования одного текста различаются: "
          f"{encrypt_oaep(key.public(), secret) != box}")

    started = time.time()
    signature = sign_pss(key, secret)
    ok = verify_pss(key.public(), secret, signature)
    bad = verify_pss(key.public(), b"tampered", signature)
    print(f"\nPSS : подпись верна: {ok}, подделка принята: {bad} "
          f"({(time.time() - started) * 1000:.0f} мс)")

    signature = sign_pkcs1v15(key, secret)
    print(f"v1.5: подпись верна: "
          f"{verify_pkcs1v15(key.public(), secret, signature)}")
    print(f"\nPEM ключа — {len(private_to_pem(key))} символов, читается openssl")


if __name__ == "__main__":
    _demo()
