"""TLS для REST API: сертификат, настоящий HTTPS и защита приватных ключей.

Здесь два разных предмета проверки.

Первый — САМ сертификат: он собирается из ASN.1/DER своими руками, и ошибку в
кодировании легко не заметить (структура «почти правильная» разбирается, но
клиент её отвергает). Поэтому сертификат и разбирается OpenSSL, и его подпись
проверяется нашим же кодом, и по нему проходит настоящее TLS-рукопожатие.

Второй — правило доступа к эндпоинтам, принимающим приватный ключ. Раньше оно
существовало только в документации («для СВОЕГО локального узла»), а
документация ничего не запрещает: по открытому HTTP ключ уходил в сеть
открытым текстом.
"""

import json
import os
import socket
import ssl
import subprocess
import sys
import threading
import urllib.error
import urllib.request

import pytest

from b_hydra import certgen
from b_hydra.api import BHydraAPI, make_server

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _free_port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@pytest.fixture(scope="module")
def certificate(tmp_path_factory):
    """Сертификат и ключ на весь модуль (генерация — это умножение точки)."""
    folder = tmp_path_factory.mktemp("tls")
    cert = str(folder / "cert.pem")
    key = str(folder / "cert.key")
    assert certgen.ensure_files(cert, key, hosts=("localhost", "127.0.0.1"))
    return cert, key


def _serve(state, certfile=None, keyfile=None, allow=None):
    """Поднимает сервер на свободном порту и возвращает (порт, сервер)."""
    port = _free_port()
    server = make_server("127.0.0.1", port, state, difficulty=2,
                         certfile=certfile, keyfile=keyfile,
                         allow_key_endpoints=allow)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return port, server


# --- Сертификат ---------------------------------------------------------------
def test_certificate_is_parsed_by_openssl(certificate):
    """OpenSSL разбирает наш DER: субъект, SAN и срок действия на месте."""
    cert, _key = certificate
    info = ssl._ssl._test_decode_cert(cert)
    assert ("commonName", "localhost") in info["subject"][-1]
    # Имя проверяется ТОЛЬКО по subjectAltName — CommonName для этого не
    # используется уже много лет, без SAN сертификат был бы бесполезен.
    assert ("DNS", "localhost") in info["subjectAltName"]
    assert ("IP Address", "127.0.0.1") in info["subjectAltName"]
    assert info["version"] == 3


def test_openssl_accepts_the_key_pair(certificate):
    """Ключ подходит к сертификату (иначе load_cert_chain бросит)."""
    cert, key = certificate
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)


def test_certificate_signature_verifies_with_our_own_code():
    """Подпись сертификата проверяется нашим же P-256 — не только «принял OpenSSL».

    Сертификат самоподписанный, поэтому подпись обязана сходиться с ключом,
    который лежит внутри него самого.
    """
    private, point = certgen.generate_key()
    payload = b"tbsCertificate"
    signature = certgen.sign_p256(private, payload)
    assert certgen.verify_p256(point, payload, signature) is True
    assert certgen.verify_p256(point, b"another payload", signature) is False
    other = certgen.generate_key()[1]
    assert certgen.verify_p256(other, payload, signature) is False


def test_der_integer_encodes_the_sign_bit():
    """У DER-числа со старшим битом обязан быть ведущий ноль.

    Без него положительное число прочиталось бы как отрицательное, и клиент
    отверг бы сертификат с невнятной ошибкой разбора.
    """
    assert certgen._integer(0x7F) == b"\x02\x01\x7f"
    assert certgen._integer(0x80) == b"\x02\x02\x00\x80"
    assert certgen._integer(1) == b"\x02\x01\x01"


def test_generated_keys_are_on_the_curve():
    for _ in range(3):
        private, point = certgen.generate_key()
        assert 1 <= private < certgen.P256_N
        x, y = point
        assert (y * y - x * x * x - certgen.P256_A * x - certgen.P256_B) \
            % certgen.P256_P == 0


def test_existing_files_are_not_overwritten(tmp_path):
    """Повторный запуск не должен молча менять сертификат: клиенты, которые
    его уже закрепили, увидели бы подмену сервера."""
    cert = str(tmp_path / "c.pem")
    key = str(tmp_path / "c.key")
    assert certgen.ensure_files(cert, key) is True
    first = open(cert, encoding="ascii").read()
    assert certgen.ensure_files(cert, key) is False
    assert open(cert, encoding="ascii").read() == first


# --- Настоящий HTTPS ----------------------------------------------------------
def test_https_request_works_end_to_end(certificate, tmp_path):
    cert, key = certificate
    port, server = _serve(str(tmp_path / "chain.json"), cert, key)
    try:
        context = ssl.create_default_context(cafile=cert)
        info = json.load(urllib.request.urlopen(
            f"https://localhost:{port}/api/info", context=context))
        assert info["network"] == "B-hydra"
        # И обозреватель отдаётся тоже (это же браузерная страница).
        page = urllib.request.urlopen(f"https://localhost:{port}/",
                                      context=context).read()
        assert b"<html" in page.lower()
    finally:
        server.shutdown()


def test_tls_version_is_1_2_at_least(certificate, tmp_path):
    """TLS 1.0/1.1 сломаны; оставленная поддержка старой версии — готовое
    понижение для активного атакующего."""
    cert, key = certificate
    port, server = _serve(str(tmp_path / "chain.json"), cert, key)
    try:
        context = ssl.create_default_context(cafile=cert)
        with context.wrap_socket(socket.create_connection(("localhost", port)),
                                 server_hostname="localhost") as sock:
            assert sock.version() in ("TLSv1.2", "TLSv1.3")
        weak = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        weak.check_hostname = False
        weak.verify_mode = ssl.CERT_NONE
        weak.maximum_version = ssl.TLSVersion.TLSv1_1
        with pytest.raises(ssl.SSLError):
            weak.wrap_socket(socket.create_connection(("localhost", port)),
                             server_hostname="localhost")
    finally:
        server.shutdown()


def test_unknown_certificate_authority_is_refused(certificate, tmp_path):
    """Самоподписанный сертификат без явного доверия приниматься не должен."""
    cert, key = certificate
    port, server = _serve(str(tmp_path / "chain.json"), cert, key)
    try:
        with pytest.raises(urllib.error.URLError):
            urllib.request.urlopen(f"https://localhost:{port}/api/info",
                                   context=ssl.create_default_context())
    finally:
        server.shutdown()


def test_ip_address_in_san_passes_hostname_check(certificate, tmp_path):
    """Подключение по IP тоже проходит проверку имени — SAN содержит iPAddress."""
    cert, key = certificate
    port, server = _serve(str(tmp_path / "chain.json"), cert, key)
    try:
        context = ssl.create_default_context(cafile=cert)
        json.load(urllib.request.urlopen(f"https://127.0.0.1:{port}/api/info",
                                         context=context))
    finally:
        server.shutdown()


# --- Приватный ключ по открытому каналу ---------------------------------------
def _post(port, path, body, scheme="http", context=None):
    request = urllib.request.Request(
        f"{scheme}://127.0.0.1:{port}{path}",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    try:
        return 200, json.load(urllib.request.urlopen(request, context=context))
    except urllib.error.HTTPError as error:
        return error.code, json.load(error)


KEY = "11" * 32


def test_key_endpoints_are_refused_without_tls(tmp_path):
    """Без TLS и не с локального адреса приватный ключ не принимают.

    allow_key_endpoints=False — ровно тот путь, по которому пойдёт удалённый
    клиент на открытом канале.
    """
    port, server = _serve(str(tmp_path / "chain.json"), allow=False)
    try:
        for path, body in (("/api/wallet", {"private_key": KEY}),
                           ("/api/send", {"private_key": KEY, "to": "BHYx",
                                          "amount": 1}),
                           ("/api/contract/escrow", {"private_key": KEY})):
            code, answer = _post(port, path, body)
            assert code == 403, path
            assert "приватный ключ" in answer["error"]
    finally:
        server.shutdown()


def test_reading_endpoints_still_work_without_tls(tmp_path):
    """Запрет касается ТОЛЬКО ключей: обозреватель и чтение работают как были."""
    port, server = _serve(str(tmp_path / "chain.json"), allow=False)
    try:
        info = json.load(urllib.request.urlopen(
            f"http://127.0.0.1:{port}/api/info"))
        assert info["network"] == "B-hydra"
        # Майнинг без ключа — тоже не про ключи.
        code, answer = _post(port, "/api/mine",
                             {"miner": "BHY" + "1" * 34})
        assert code in (200, 400) and "приватный ключ по открытому" not in \
            str(answer.get("error", ""))
    finally:
        server.shutdown()


def test_signed_transaction_is_always_accepted(tmp_path):
    """Правильный путь остаётся открытым: подпись на устройстве и
    POST /api/transaction ключа не содержат вовсе."""
    from b_hydra.wallet import generate_wallet

    port, server = _serve(str(tmp_path / "chain.json"), allow=False)
    try:
        miner = generate_wallet()
        BHydraAPI.node.mine_pending(miner.address)
        tx = BHydraAPI.node.create_transaction(
            miner, generate_wallet().address, 5, fee=0.1)
        code, answer = _post(port, "/api/transaction", tx.to_dict())
        assert code == 200 and answer["accepted"] is True
    finally:
        server.shutdown()


def test_key_endpoints_work_over_tls(certificate, tmp_path):
    cert, key = certificate
    port, server = _serve(str(tmp_path / "chain.json"), cert, key)
    try:
        context = ssl.create_default_context(cafile=cert)
        code, answer = _post(port, "/api/wallet", {"private_key": KEY},
                             scheme="https", context=context)
        assert code == 200 and answer["address"].startswith("BHY")
    finally:
        server.shutdown()


def test_local_client_is_allowed_without_tls(tmp_path):
    """С локального адреса ключ принимают и без TLS: перехватывать нечего,
    и это единственный сценарий, ради которого эти эндпоинты существуют."""
    port, server = _serve(str(tmp_path / "chain.json"))
    try:
        code, answer = _post(port, "/api/wallet", {"private_key": KEY})
        assert code == 200 and answer["address"].startswith("BHY")
    finally:
        server.shutdown()


def test_keys_allowed_decision_table():
    """Таблица решений целиком, без сети."""
    class Fake(BHydraAPI):
        def __init__(self, tls, address, allow=None):
            self.tls = tls
            self.client_address = (address, 1234)
            self.allow_key_endpoints = allow

    assert Fake(False, "127.0.0.1")._keys_allowed() is True      # свой узел
    assert Fake(False, "::1")._keys_allowed() is True
    assert Fake(False, "203.0.113.7")._keys_allowed() is False   # чужой, открыто
    assert Fake(True, "203.0.113.7")._keys_allowed() is True     # чужой, по TLS
    assert Fake(False, "203.0.113.7", allow=True)._keys_allowed() is True
    assert Fake(True, "127.0.0.1", allow=False)._keys_allowed() is False


# --- Регрессия: stdlib не должен быть перекрыт --------------------------------
def test_stdlib_ssl_is_not_shadowed_by_a_file_in_the_repo():
    """В корне репозитория лежала копия stdlib `ssl.py`, и она перекрывала
    настоящий модуль: `import ssl` из каталога проекта падал с ImportError.

    Это ломало не только TLS, а всё, что ходит по HTTPS. Тест не даёт файлу
    вернуться.
    """
    assert not os.path.exists(os.path.join(ROOT, "ssl.py"))
    result = subprocess.run(
        [sys.executable, "-c", "import ssl; print(ssl.OPENSSL_VERSION)"],
        cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    assert "OpenSSL" in result.stdout
