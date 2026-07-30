"""
certgen.py — самоподписанный сертификат X.509 для HTTPS, без зависимостей.

REST API отдаёт кошелёк и обозреватель в БРАУЗЕР, а `/api/send` принимает
приватный ключ. По открытому HTTP это читает любой, кто видит канал. Нужен TLS.

⚠️ Свой TLS мы НЕ пишем. Для канала узел↔узел собственный протокол (`secure.py`)
уместен: он учебный, обе стороны наши, и вторая реализация его перепроверяет.
Для браузера так нельзя — там нужен настоящий TLS 1.2/1.3 с проверенным
стеком, и он уже есть в стандартной библиотеке (`ssl`, поверх OpenSSL).
Самодельный TLS был бы ровно тем, от чего предостерегает любой учебник.

А вот чего в стандартной библиотеке НЕТ — это выпуска сертификата: обычно зовут
`openssl req` или ставят `cryptography`. Здесь он собирается сам: ASN.1/DER,
ключ ECDSA P-256, подпись SHA-256 с детерминированным нонсом (RFC 6979,
`wallet._rfc6979_nonces` — тот же код, что проверен на официальных векторах).
Так `python -m b_hydra.api --tls` работает сразу, без openssl в системе и без
pip install.

⚠️ Самоподписанный сертификат годится для СВОЕЙ машины и локальной сети:
браузер честно предупредит, что удостоверяющего центра нет. Для публичного
узла берите настоящий сертификат (Let's Encrypt) и передавайте `--cert/--key`.
"""

from __future__ import annotations

import base64
import datetime
import ipaddress
import os
import secrets

if __name__ == "__main__" and __package__ in (None, ""):
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    __package__ = "b_hydra"

from . import hashing
from .wallet import _hmac_sha512, _inverse_mod, _rfc6979_nonces

# --- Кривая P-256 (secp256r1) ------------------------------------------------
# Не secp256k1: TLS её не принимает — современные стеки её из умолчаний убрали.
# Отличие только в коэффициенте a (здесь a = p-3, у нас в кошельке a = 0),
# поэтому удвоение точки считается по чуть другой формуле.
P256_P = 0xffffffff00000001000000000000000000000000ffffffffffffffffffffffff
P256_A = P256_P - 3
P256_B = 0x5ac635d8aa3a93e7b3ebbd55769886bc651d06b0cc53b0f63bce3c3e27d2604b
P256_GX = 0x6b17d1f2e12c4247f8bce6e563a440f277037d812deb33a0f4a13945d898c296
P256_GY = 0x4fe342e2fe1a7f9b8ee7eb4a7c0f9e162bce33576b315ececbb6406837bf51f5
P256_N = 0xffffffff00000000ffffffffffffffffbce6faada7179e84f3b9cac2fc632551
P256_G = (P256_GX, P256_GY)


def _point_add(first, second):
    if first is None:
        return second
    if second is None:
        return first
    x1, y1 = first
    x2, y2 = second
    if x1 == x2 and (y1 + y2) % P256_P == 0:
        return None                       # P + (−P) = бесконечность
    if first == second:
        slope = (3 * x1 * x1 + P256_A) * _inverse_mod(2 * y1, P256_P) % P256_P
    else:
        slope = (y2 - y1) * _inverse_mod(x2 - x1, P256_P) % P256_P
    x3 = (slope * slope - x1 - x2) % P256_P
    return (x3, (slope * (x1 - x3) - y1) % P256_P)


def _scalar_mult(k, point):
    result, addend = None, point
    while k:
        if k & 1:
            result = _point_add(result, addend)
        addend = _point_add(addend, addend)
        k >>= 1
    return result


def _sha256(data: bytes) -> bytes:
    return hashing.sha256_bytes(data)


def _hmac_sha256(key: bytes, message: bytes) -> bytes:
    """HMAC-SHA256 на нашем SHA (RFC 2104) — для нонса RFC 6979 под P-256."""
    block = 64                                    # размер блока SHA-256
    if len(key) > block:
        key = _sha256(key)
    key = key.ljust(block, b"\x00")
    inner = _sha256(bytes(b ^ 0x36 for b in key) + message)
    return _sha256(bytes(b ^ 0x5c for b in key) + inner)


def generate_key():
    """Пара ключей P-256: (приватное число, публичная точка)."""
    private = secrets.randbelow(P256_N - 1) + 1
    return private, _scalar_mult(private, P256_G)


def verify_p256(point, payload: bytes, signature) -> bool:
    """Проверка подписи P-256 — чтобы сертификат можно было сверить своим же
    кодом, не полагаясь только на то, что его «принял OpenSSL»."""
    r, s = signature
    if not (1 <= r < P256_N and 1 <= s < P256_N):
        return False
    z = int.from_bytes(_sha256(payload), "big")
    w = _inverse_mod(s, P256_N)
    first = _scalar_mult(z * w % P256_N, P256_G)
    second = _scalar_mult(r * w % P256_N, point)
    total = _point_add(first, second)
    return total is not None and total[0] % P256_N == r


def sign_p256(private: int, payload: bytes):
    """ECDSA P-256 + SHA-256, нонс детерминированный (RFC 6979).

    Тот же HMAC-DRBG, что и у транзакций, только на SHA-256 и с порядком
    P-256 — он параметризован именно для этого и сверен с официальными
    векторами RFC 6979 (приложение A.2.5).
    """
    digest = _sha256(payload)
    z = int.from_bytes(digest, "big")             # len(digest)*8 == qlen
    for k in _rfc6979_nonces(private, z, order=P256_N,
                             hmac_fn=_hmac_sha256, hlen=32):
        point = _scalar_mult(k, P256_G)
        r = point[0] % P256_N
        if r == 0:
            continue
        s = _inverse_mod(k, P256_N) * (z + r * private) % P256_N
        if s == 0:
            continue
        return r, s


# --- ASN.1 / DER --------------------------------------------------------------
def _length(size: int) -> bytes:
    if size < 0x80:
        return bytes([size])
    raw = size.to_bytes((size.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(raw)]) + raw


def _tlv(tag: int, value: bytes) -> bytes:
    return bytes([tag]) + _length(len(value)) + value


def _integer(value: int) -> bytes:
    raw = value.to_bytes(max(1, (value.bit_length() + 7) // 8), "big")
    # DER-число знаковое: если старший бит стоит, нужен ведущий ноль, иначе
    # положительное число прочитается как отрицательное.
    if raw[0] & 0x80:
        raw = b"\x00" + raw
    return _tlv(0x02, raw)


def _bit_string(data: bytes) -> bytes:
    return _tlv(0x03, b"\x00" + data)             # 0 неиспользованных битов


def _octet_string(data: bytes) -> bytes:
    return _tlv(0x04, data)


def _sequence(*parts: bytes) -> bytes:
    return _tlv(0x30, b"".join(parts))


def _set(*parts: bytes) -> bytes:
    return _tlv(0x31, b"".join(parts))


def _oid(text: str) -> bytes:
    parts = [int(p) for p in text.split(".")]
    body = bytes([parts[0] * 40 + parts[1]])
    for number in parts[2:]:
        chunk = bytearray([number & 0x7F])
        number >>= 7
        while number:
            chunk.append((number & 0x7F) | 0x80)
            number >>= 7
        body += bytes(reversed(chunk))
    return _tlv(0x06, body)


def _utf8(text: str) -> bytes:
    return _tlv(0x0C, text.encode("utf-8"))


def _utc_time(moment: datetime.datetime) -> bytes:
    return _tlv(0x17, moment.strftime("%y%m%d%H%M%SZ").encode("ascii"))


def _explicit(number: int, value: bytes) -> bytes:
    return _tlv(0xA0 | number, value)


OID_EC_PUBLIC_KEY = "1.2.840.10045.2.1"
OID_PRIME256V1 = "1.2.840.10045.3.1.7"
OID_ECDSA_SHA256 = "1.2.840.10045.4.3.2"
OID_COMMON_NAME = "2.5.4.3"
OID_ORGANISATION = "2.5.4.10"
OID_BASIC_CONSTRAINTS = "2.5.29.19"
OID_KEY_USAGE = "2.5.29.15"
OID_EXT_KEY_USAGE = "2.5.29.37"
OID_SERVER_AUTH = "1.3.6.1.5.5.7.3.1"
OID_SUBJECT_ALT_NAME = "2.5.29.17"


def _name(common_name: str, organisation: str) -> bytes:
    return _sequence(
        _set(_sequence(_oid(OID_ORGANISATION), _utf8(organisation))),
        _set(_sequence(_oid(OID_COMMON_NAME), _utf8(common_name))),
    )


def _public_key_info(point) -> bytes:
    raw = b"\x04" + point[0].to_bytes(32, "big") + point[1].to_bytes(32, "big")
    return _sequence(
        _sequence(_oid(OID_EC_PUBLIC_KEY), _oid(OID_PRIME256V1)),
        _bit_string(raw),
    )


def _alt_names(hosts) -> bytes:
    """subjectAltName. Современные браузеры и Python смотрят ТОЛЬКО сюда:
    CommonName для проверки имени не используется уже много лет."""
    entries = []
    for host in hosts:
        try:
            address = ipaddress.ip_address(host)
            entries.append(_tlv(0x87, address.packed))        # iPAddress
        except ValueError:
            entries.append(_tlv(0x82, host.encode("ascii")))  # dNSName
    return _sequence(*entries)


def _extension(oid: str, value: bytes, critical: bool = False) -> bytes:
    parts = [_oid(oid)]
    if critical:
        parts.append(_tlv(0x01, b"\xff"))          # BOOLEAN TRUE
    parts.append(_octet_string(value))
    return _sequence(*parts)


def _pem(label: str, der: bytes) -> str:
    body = base64.b64encode(der).decode("ascii")
    lines = [body[i:i + 64] for i in range(0, len(body), 64)]
    return (f"-----BEGIN {label}-----\n" + "\n".join(lines) +
            f"\n-----END {label}-----\n")


def private_key_pem(private: int, point) -> str:
    """Приватный ключ в формате SEC1 (RFC 5915) — его понимает ssl."""
    raw = b"\x04" + point[0].to_bytes(32, "big") + point[1].to_bytes(32, "big")
    der = _sequence(
        _integer(1),
        _octet_string(private.to_bytes(32, "big")),
        _explicit(0, _oid(OID_PRIME256V1)),
        _explicit(1, _bit_string(raw)),
    )
    return _pem("EC PRIVATE KEY", der)


def self_signed(hosts=("localhost", "127.0.0.1"), days=825,
                organisation="B-hydra", common_name=None):
    """Самоподписанный сертификат. Возвращает (PEM сертификата, PEM ключа).

    825 дней — верхний предел, который признают браузеры за сертификатом без
    удостоверяющего центра; больше ставить бессмысленно.
    """
    hosts = list(hosts) or ["localhost"]
    common_name = common_name or hosts[0]
    private, point = generate_key()
    now = datetime.datetime.now(datetime.timezone.utc)
    # Сдвиг назад на час: часы у клиента могут немного отставать, и свежий
    # сертификат оказался бы «ещё не действителен».
    not_before = now - datetime.timedelta(hours=1)
    not_after = now + datetime.timedelta(days=days)
    name = _name(common_name, organisation)

    extensions = _sequence(
        # cA = FALSE кодируется ПУСТОЙ последовательностью: в DER значение,
        # равное умолчанию, не пишется.
        _extension(OID_BASIC_CONSTRAINTS, _sequence(), critical=True),
        _extension(OID_KEY_USAGE, _tlv(0x03, b"\x05\xa0"), critical=True),
        _extension(OID_EXT_KEY_USAGE, _sequence(_oid(OID_SERVER_AUTH))),
        _extension(OID_SUBJECT_ALT_NAME, _alt_names(hosts)),
    )
    tbs = _sequence(
        _explicit(0, _integer(2)),                 # версия v3
        _integer(secrets.randbits(64) | 1),        # серийный номер, положительный
        _sequence(_oid(OID_ECDSA_SHA256)),
        name,                                      # издатель = субъект
        _sequence(_utc_time(not_before), _utc_time(not_after)),
        name,
        _public_key_info(point),
        _explicit(3, extensions),
    )
    r, s = sign_p256(private, tbs)
    certificate = _sequence(
        tbs,
        _sequence(_oid(OID_ECDSA_SHA256)),
        _bit_string(_sequence(_integer(r), _integer(s))),
    )
    return _pem("CERTIFICATE", certificate), private_key_pem(private, point)


def ensure_files(cert_path, key_path, hosts=("localhost", "127.0.0.1")) -> bool:
    """Создаёт сертификат и ключ, если их ещё нет. True — если создали.

    Ключ пишется с правами 0600: кто его получил, тот может выдать себя за наш
    сервер. Это НЕ ключ кошелька — монеты им не тронуть.
    """
    if os.path.exists(cert_path) and os.path.exists(key_path):
        return False
    certificate, key = self_signed(hosts=hosts)
    with open(cert_path, "w", encoding="ascii") as handle:
        handle.write(certificate)
    with open(key_path, "w", encoding="ascii") as handle:
        handle.write(key)
    try:
        os.chmod(key_path, 0o600)
    except OSError:
        pass
    return True


if __name__ == "__main__":
    import ssl
    import tempfile

    certificate, key = self_signed()
    print(certificate.strip()[:64], "…")
    with tempfile.TemporaryDirectory() as folder:
        cert_path = os.path.join(folder, "cert.pem")
        key_path = os.path.join(folder, "key.pem")
        with open(cert_path, "w") as handle:
            handle.write(certificate)
        with open(key_path, "w") as handle:
            handle.write(key)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert_path, key_path)
        print("OpenSSL принял сертификат и ключ")
        info = ssl._ssl._test_decode_cert(cert_path)
        print("субъект :", info["subject"])
        print("SAN     :", info["subjectAltName"])
        print("действителен до:", info["notAfter"])
