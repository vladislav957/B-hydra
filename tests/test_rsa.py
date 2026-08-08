"""RSA с нуля: OAEP, PSS, PKCS#1 v1.5 — и сверка с OpenSSL.

⚠️ В консенсус это не входит: подписи транзакций делает ECDSA. RSA нужен там,
где с нами разговаривают чужие инструменты, — поэтому главная проверка здесь
не «работает ли само с собой», а **совместимость с настоящей реализацией**.
Тесты с openssl пропускаются, если его нет.

Отдельно проверяется то, чем «RSA из учебника» отличается от рабочего: набивка
обязана быть вероятностной, подделка — отвергаться, а разбор PKCS#1 v1.5 не
должен принимать мусор между набивкой и хешем (ошибка Блейхенбахера 2006 года,
из-за которой подпись подделывали вообще без ключа).
"""

import os
import shutil
import subprocess

import pytest

from b_hydra import rsa

OPENSSL = shutil.which("openssl")

# Генерация 2048-битного ключа — около секунды, поэтому один на весь модуль.
@pytest.fixture(scope="module")
def key():
    return rsa.generate(2048)


@pytest.fixture(scope="module")
def public(key):
    return key.public()


# --- Ключи --------------------------------------------------------------------
def test_key_is_consistent(key):
    """Модуль и параметры CRT обязаны сходиться между собой."""
    assert key.p * key.q == key.n
    assert key.bits == 2048
    assert key.e == rsa.DEFAULT_EXPONENT
    assert key.dp == key.d % (key.p - 1)
    assert key.dq == key.d % (key.q - 1)
    assert key.qinv * key.q % key.p == 1
    assert key.p > key.q                      # для qinv = q^-1 mod p


def test_primes_are_really_prime(key):
    assert rsa._is_probable_prime(key.p)
    assert rsa._is_probable_prime(key.q)


def test_primes_are_far_apart(key):
    """Близкие p и q раскладывает метод Ферма за секунды, каким бы длинным
    ни был ключ."""
    assert abs(key.p - key.q).bit_length() > 900


def test_short_keys_are_refused():
    """1024 бита запрещены стандартами с 2010-х — молча выпускать их нельзя."""
    with pytest.raises(rsa.RSAError):
        rsa.generate(1024)


def test_even_exponent_is_refused():
    with pytest.raises(rsa.RSAError):
        rsa.generate(2048, exponent=4)


def test_primality_agrees_with_a_known_list():
    """Тест простоты проверяется на числах, про которые ответ известен."""
    known = [2, 3, 5, 7, 97, 3559, 65537, 2 ** 61 - 1]
    for value in known:
        assert rsa._is_probable_prime(value), value
    for value in [0, 1, 4, 9, 561, 1105, 65536, 2 ** 61 - 3]:
        assert not rsa._is_probable_prime(value), value


def test_carmichael_numbers_are_not_fooled():
    """Числа Кармайкла обманывают тест Ферма, но не Миллера–Рабина.

    Именно на них ломались наивные реализации: 561, 1105, 1729 проходят
    проверку a^(n-1) ≡ 1 для всех оснований, взаимно простых с n.
    """
    for carmichael in (561, 1105, 1729, 2465, 2821, 6601, 8911):
        assert not rsa._is_probable_prime(carmichael), carmichael


# --- Примитивы ----------------------------------------------------------------
def test_i2osp_and_os2ip_are_inverse():
    for value in (0, 1, 255, 256, 1 << 100):
        length = max(1, (value.bit_length() + 7) // 8)
        assert rsa.os2ip(rsa.i2osp(value, length)) == value


def test_i2osp_refuses_to_truncate():
    """Молча обрезать число нельзя: получится другое сообщение."""
    with pytest.raises(rsa.RSAError):
        rsa.i2osp(256, 1)


def test_blinding_does_not_change_the_result(key):
    """Ослепление обязано быть невидимым: тот же ответ при разной случайности."""
    value = 12345678901234567890
    first = rsa._private_op(key, value)
    second = rsa._private_op(key, value)
    assert first == second
    assert pow(first, key.e, key.n) == value


def test_mgf1_is_deterministic_and_long_enough():
    first = rsa.mgf1(b"seed", 100)
    assert first == rsa.mgf1(b"seed", 100)
    assert len(first) == 100
    assert first != rsa.mgf1(b"other", 100)
    assert rsa.mgf1(b"seed", 100)[:32] == rsa.mgf1(b"seed", 32)


# --- OAEP ---------------------------------------------------------------------
def test_oaep_roundtrip(key, public):
    for message in (b"", b"x", "перевод 10 BHY".encode("utf-8"), b"a" * 190):
        box = rsa.encrypt_oaep(public, message)
        assert len(box) == public.size
        assert rsa.decrypt_oaep(key, box) == message


def test_oaep_is_probabilistic(public):
    """Два шифрования одного текста обязаны различаться.

    Детерминированный шифр выдаёт повторы: одинаковая сумма перевода даёт
    одинаковый шифротекст, и содержимое подбирается перебором коротких
    вариантов.
    """
    message = b"same message"
    assert rsa.encrypt_oaep(public, message) != rsa.encrypt_oaep(public, message)


def test_oaep_refuses_too_long_a_message(public):
    limit = public.size - 2 * 32 - 2
    with pytest.raises(rsa.RSAError):
        rsa.encrypt_oaep(public, b"a" * (limit + 1))


def test_oaep_rejects_tampered_ciphertext(key, public):
    box = bytearray(rsa.encrypt_oaep(public, b"secret"))
    box[10] ^= 1
    with pytest.raises(rsa.RSAError):
        rsa.decrypt_oaep(key, bytes(box))


def test_oaep_label_must_match(key, public):
    label = "метка".encode("utf-8")
    box = rsa.encrypt_oaep(public, b"secret", label=label)
    assert rsa.decrypt_oaep(key, box, label=label) == b"secret"
    with pytest.raises(rsa.RSAError):
        rsa.decrypt_oaep(key, box, label="другая".encode("utf-8"))


def test_oaep_failures_look_identical(key, public):
    """Все неудачи дают ОДНУ ошибку без подробностей.

    Разные сообщения об ошибке — это оракул: по тому, как именно отвергнут
    шифротекст, его читают целиком (атака Мангера).

    ⚠️ Шифротекст c ≥ n добавлен ЯВНО, а не в надежде, что порча старшего байта
    туда попадёт. Изначально попадала по удаче — примерно на одном ключе из
    нескольких, — и утечка («шифротекст вне диапазона модуля» вместо общего
    отказа) всплыла случайным падением, а не на первом же прогоне.
    """
    reasons = set()
    box = bytearray(rsa.encrypt_oaep(public, b"secret"))
    broken_boxes = []
    for position in (0, 5, 100, len(box) - 1):
        copy = bytearray(box)
        copy[position] ^= 0xFF
        broken_boxes.append(bytes(copy))
    # Заведомо больше модуля и заведомо меньше — обе стороны диапазона.
    broken_boxes.append(b"\xff" * key.size)
    broken_boxes.append(b"\x00" * key.size)

    for candidate in broken_boxes:
        try:
            rsa.decrypt_oaep(key, candidate)
        except rsa.RSAError as error:
            reasons.add(str(error))
    assert len(reasons) == 1, reasons
    assert reasons == {"расшифровка не удалась"}


def test_a_ciphertext_above_the_modulus_is_not_distinguishable(key):
    """Отдельно: c ≥ n обязан отвергаться теми же словами, что и мусор.

    Это самая заметная утечка из всех: границу модуля атакующий нащупывает
    первой, и по ней восстанавливается старший байт открытого текста.
    """
    with pytest.raises(rsa.RSAError, match="расшифровка не удалась"):
        rsa.decrypt_oaep(key, key.n.to_bytes(key.size, "big"))


def test_oaep_refuses_wrong_length(key):
    with pytest.raises(rsa.RSAError):
        rsa.decrypt_oaep(key, b"\x00" * 10)


# --- PSS ----------------------------------------------------------------------
def test_pss_roundtrip(key, public):
    for message in (b"", b"b-hydra", "перевод".encode("utf-8"), b"z" * 1000):
        signature = rsa.sign_pss(key, message)
        assert len(signature) == public.size
        assert rsa.verify_pss(public, message, signature) is True


def test_pss_is_probabilistic(key):
    message = b"same message"
    assert rsa.sign_pss(key, message) != rsa.sign_pss(key, message)


def test_pss_rejects_tampering(key, public):
    signature = rsa.sign_pss(key, b"original")
    assert rsa.verify_pss(public, b"tampered", signature) is False
    broken = bytearray(signature)
    broken[0] ^= 1
    assert rsa.verify_pss(public, b"original", bytes(broken)) is False


def test_pss_rejects_a_signature_of_the_wrong_length(public):
    assert rsa.verify_pss(public, b"x", b"\x00" * 10) is False


def test_pss_rejects_another_key(key, public):
    other = rsa.generate(2048)
    signature = rsa.sign_pss(other, b"message")
    assert rsa.verify_pss(public, b"message", signature) is False


# --- PKCS#1 v1.5 --------------------------------------------------------------
def test_pkcs1v15_roundtrip(key, public):
    signature = rsa.sign_pkcs1v15(key, b"b-hydra")
    assert rsa.verify_pkcs1v15(public, b"b-hydra", signature) is True
    assert rsa.verify_pkcs1v15(public, b"other", signature) is False


def test_pkcs1v15_is_deterministic(key):
    """В отличие от PSS, эта схема без случайности — так в стандарте."""
    assert rsa.sign_pkcs1v15(key, b"x") == rsa.sign_pkcs1v15(key, b"x")


def test_pkcs1v15_checks_the_whole_padding(key, public):
    """Проверяется ВСЯ строка набивки, а не «нашёлся ли хеш в конце».

    Разбор по частям — это ошибка Блейхенбахера 2006 года: реализации,
    принимавшие мусор между набивкой и хешем, позволяли подделать подпись
    вообще без ключа при e = 3.
    """
    signature = rsa.sign_pkcs1v15(key, b"message")
    encoded = bytearray(rsa.i2osp(rsa._public_op(public, rsa.os2ip(signature)),
                                  public.size))
    # Портим один байт набивки, оставляя хеш на месте.
    encoded[5] = 0xFE
    forged = rsa.i2osp(rsa._private_op(key, rsa.os2ip(bytes(encoded))),
                       public.size)
    assert rsa.verify_pkcs1v15(public, b"message", forged) is False


def test_digest_info_prefixes_match_the_standard():
    """Префиксы DigestInfo — фиксированные байты из RFC 8017 (приложение B.1)."""
    assert rsa._DIGEST_INFO["sha256"].hex() == \
        "3031300d060960864801650304020105000420"
    assert rsa._DIGEST_INFO["sha512"].hex() == \
        "3051300d060960864801650304020305000440"


# --- Сериализация -------------------------------------------------------------
def test_der_roundtrip(key, public):
    assert rsa.private_from_der(rsa.private_to_der(key)).n == key.n
    assert rsa.private_from_der(rsa.private_to_der(key)).d == key.d
    assert rsa.public_from_der(rsa.public_to_der(public)).n == public.n


def test_pkcs8_and_spki_roundtrip(key, public):
    """Современные форматы («BEGIN PRIVATE KEY» / «BEGIN PUBLIC KEY»)."""
    assert rsa.private_from_der(rsa.private_to_pkcs8(key)).n == key.n
    assert rsa.public_from_der(rsa.public_to_spki(public)).e == public.e


def test_pem_roundtrip(key, public):
    assert rsa.private_from_pem(rsa.private_to_pem(key)).n == key.n
    assert rsa.private_from_pem(rsa.private_to_pem(key, pkcs8=True)).n == key.n
    assert rsa.public_from_pem(rsa.public_to_pem(public)).n == public.n
    assert rsa.public_from_pem(rsa.public_to_pem(public, spki=False)).n == public.n


def test_pem_labels_are_the_expected_ones(key, public):
    assert "BEGIN RSA PRIVATE KEY" in rsa.private_to_pem(key)
    assert "BEGIN PRIVATE KEY" in rsa.private_to_pem(key, pkcs8=True)
    assert "BEGIN PUBLIC KEY" in rsa.public_to_pem(public)


def test_garbage_der_is_refused():
    with pytest.raises(rsa.RSAError):
        rsa.private_from_der(b"\x30\x03\x02\x01\x00")


# --- Совместимость с OpenSSL (главное) -----------------------------------------
def _run(*args, **kwargs):
    return subprocess.run(args, capture_output=True, timeout=120, **kwargs)


@pytest.mark.skipif(OPENSSL is None, reason="нет openssl")
def test_openssl_reads_our_key(key, tmp_path):
    """Наш ключ обязан быть настоящим ключом, а не «похожим на него»."""
    path = tmp_path / "ours.pem"
    path.write_text(rsa.private_to_pem(key), encoding="ascii")
    result = _run(OPENSSL, "rsa", "-in", str(path), "-check", "-noout")
    assert result.returncode == 0, result.stderr
    assert b"RSA key ok" in result.stdout


@pytest.mark.skipif(OPENSSL is None, reason="нет openssl")
@pytest.mark.parametrize("scheme", ["pss", "pkcs1"])
def test_openssl_verifies_our_signature(key, tmp_path, scheme):
    """НАША подпись проверяется ЧУЖИМ кодом — то, ради чего всё это нужно."""
    private = tmp_path / "ours.pem"
    private.write_text(rsa.private_to_pem(key), encoding="ascii")
    public = tmp_path / "ours.pub"
    public.write_text(rsa.public_to_pem(key.public()), encoding="ascii")
    message = tmp_path / "msg"
    message.write_bytes("перевод 10 BHY".encode("utf-8"))

    signature = tmp_path / "sig"
    if scheme == "pss":
        signature.write_bytes(rsa.sign_pss(key, message.read_bytes()))
        extra = ["-sigopt", "rsa_padding_mode:pss",
                 "-sigopt", "rsa_pss_saltlen:32"]
    else:
        signature.write_bytes(rsa.sign_pkcs1v15(key, message.read_bytes()))
        extra = []

    result = _run(OPENSSL, "dgst", "-sha256", "-verify", str(public), *extra,
                  "-signature", str(signature), str(message))
    assert result.returncode == 0, result.stdout + result.stderr
    assert b"Verified OK" in result.stdout


@pytest.mark.skipif(OPENSSL is None, reason="нет openssl")
def test_we_read_a_key_made_by_openssl(tmp_path):
    """Ключ из обычной команды openssl (PKCS#8) обязан читаться."""
    path = tmp_path / "their.pem"
    assert _run(OPENSSL, "genrsa", "-out", str(path), "2048").returncode == 0
    key = rsa.private_from_pem(path.read_text())
    assert key.p * key.q == key.n
    assert key.bits == 2048


@pytest.mark.skipif(OPENSSL is None, reason="нет openssl")
@pytest.mark.parametrize("scheme", ["pss", "pkcs1"])
def test_we_verify_a_signature_made_by_openssl(tmp_path, scheme):
    """И обратно: подпись ЧУЖОГО кода проверяется нашим."""
    private = tmp_path / "their.pem"
    _run(OPENSSL, "genrsa", "-out", str(private), "2048")
    message = tmp_path / "msg"
    message.write_bytes("сообщение от openssl".encode("utf-8"))
    signature = tmp_path / "sig"

    extra = ["-sigopt", "rsa_padding_mode:pss", "-sigopt",
             "rsa_pss_saltlen:32"] if scheme == "pss" else []
    assert _run(OPENSSL, "dgst", "-sha256", "-sign", str(private), *extra,
                "-out", str(signature), str(message)).returncode == 0

    key = rsa.private_from_pem(private.read_text()).public()
    raw = signature.read_bytes()
    verify = rsa.verify_pss if scheme == "pss" else rsa.verify_pkcs1v15
    assert verify(key, message.read_bytes(), raw) is True
    assert verify(key, b"tampered", raw) is False


@pytest.mark.skipif(OPENSSL is None, reason="нет openssl")
def test_we_decrypt_what_openssl_encrypted(tmp_path):
    """OAEP от openssl расшифровывается нашим кодом."""
    private = tmp_path / "their.pem"
    _run(OPENSSL, "genrsa", "-out", str(private), "2048")
    public = tmp_path / "their.pub"
    _run(OPENSSL, "rsa", "-in", str(private), "-pubout", "-out", str(public))

    plain = tmp_path / "plain"
    plain.write_bytes("секрет для нас".encode("utf-8"))
    box = tmp_path / "box"
    assert _run(OPENSSL, "pkeyutl", "-encrypt", "-pubin", "-inkey", str(public),
                "-pkeyopt", "rsa_padding_mode:oaep",
                "-pkeyopt", "rsa_oaep_md:sha256",
                "-in", str(plain), "-out", str(box)).returncode == 0

    key = rsa.private_from_pem(private.read_text())
    assert rsa.decrypt_oaep(key, box.read_bytes()) == plain.read_bytes()


@pytest.mark.skipif(OPENSSL is None, reason="нет openssl")
def test_openssl_decrypts_what_we_encrypted(tmp_path):
    """И обратно: наш OAEP читается openssl."""
    private = tmp_path / "their.pem"
    _run(OPENSSL, "genrsa", "-out", str(private), "2048")
    key = rsa.private_from_pem(private.read_text())

    secret = "наш секрет".encode("utf-8")
    box = tmp_path / "box"
    box.write_bytes(rsa.encrypt_oaep(key.public(), secret))
    result = _run(OPENSSL, "pkeyutl", "-decrypt", "-inkey", str(private),
                  "-pkeyopt", "rsa_padding_mode:oaep",
                  "-pkeyopt", "rsa_oaep_md:sha256", "-in", str(box))
    assert result.returncode == 0, result.stderr
    assert result.stdout == secret
