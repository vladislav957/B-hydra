"""Сверка нативного транспорта (cpp/) с Python — байт-в-байт.

Одного «оба конца договорились» мало: договориться можно и на неверно
выведенных ключах, если ошибка одинаковая с двух сторон, — а на подпись это
уже не распространяется, там неверный нонс раскрывает приватный ключ. Поэтому
здесь сравниваются САМИ БАЙТЫ: хеши, общий секрет ECDH, подписи (они
воспроизводимы благодаря RFC 6979), выведенные ключи сессии и целые кадры.

Плюс живое рукопожатие через настоящий сокет в обе стороны и запрос к
настоящему узлу P2PNode: это проверяет уже не арифметику, а протокол —
порядок сообщений, стенограмму, подпись и закрепление ключа.

Тесты пропускаются, если в системе нет компилятора C++.
"""

import json
import os
import socket
import subprocess
import threading
import time

import pytest

from b_hydra import secure
from b_hydra.node import BHydraNode
from b_hydra.p2p import P2PNode
from b_hydra.tcp import recv_message, send_message
from b_hydra.wallet import Wallet, generate_wallet

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(ROOT, "cpp", "bhydra_bridge.cpp")
COMPILER = None
for candidate in ("g++", "clang++"):
    import shutil

    if shutil.which(candidate):
        COMPILER = candidate
        break

pytestmark = pytest.mark.skipif(COMPILER is None,
                                reason="нет компилятора C++ (g++/clang++)")


@pytest.fixture(scope="module")
def bridge(tmp_path_factory):
    """Собирает мост один раз на весь модуль."""
    binary = str(tmp_path_factory.mktemp("cpp") / "bhydra_bridge")
    result = subprocess.run(
        [COMPILER, "-O2", "-std=c++17", "-pthread",
         "-I", os.path.join(ROOT, "cpp"), "-o", binary, SOURCE],
        capture_output=True, text=True)
    if result.returncode != 0:
        pytest.skip(f"мост не собрался: {result.stderr[:400]}")
    return binary


def run(bridge, *args, timeout=120):
    result = subprocess.run([bridge] + [str(a) for a in args],
                            capture_output=True, text=True, timeout=timeout)
    assert result.returncode == 0, f"{args[0]}: {result.stderr}"
    return result.stdout.strip()


def _free_port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


# --- Хеши ---------------------------------------------------------------------
def test_sha512_matches_python(bridge):
    from b_hydra import hashing
    for payload in (b"", b"abc", b"x" * 200, bytes(range(256))):
        assert run(bridge, "sha512", payload.hex()) == \
            hashing.sha512_bytes(payload).hex()


def test_shake256_matches_python(bridge):
    import hashlib
    for payload, size in ((b"", 32), (b"abc", 64), (b"\x00" * 300, 500)):
        assert run(bridge, "shake256", payload.hex(), size) == \
            hashlib.shake_256(payload).hexdigest(size)


def test_hmac_sha512_matches_python(bridge):
    import hashlib
    import hmac
    for key, message in ((b"key", b"abc"), (b"k" * 200, b""), (b"", b"y" * 500)):
        assert run(bridge, "hmac", key.hex(), message.hex()) == \
            hmac.new(key, message, hashlib.sha512).hexdigest()
        # И с нашей собственной реализацией на нашем SHA — тоже.
        assert run(bridge, "hmac", key.hex(), message.hex()) == \
            secure._hmac_ours(key, message).hex()


# --- Кривая и подписи ---------------------------------------------------------
PRIVATE_KEYS = [
    "0000000000000000000000000000000000000000000000000000000000000001",
    "0000000000000000000000000000000000000000000000000000000000000002",
    "f109bffc35c74e113cfcfeadba9d0e8db647b290abb1b5744240153ca7436c34",
    "7f7f7f7f7f7f7f7f7f7f7f7f7f7f7f7f7f7f7f7f7f7f7f7f7f7f7f7f7f7f7f7f",
    # n - 1: край диапазона, где легко ошибиться с редукцией.
    "fffffffffffffffffffffffffffffffebaaedce6af48a03bbfd25e8cd0364140",
]


def test_public_keys_match_python(bridge):
    for private in PRIVATE_KEYS:
        assert run(bridge, "pub", private) == \
            Wallet.from_private_hex(private).public_key_hex


def test_signatures_match_python_byte_for_byte(bridge):
    """Подпись воспроизводима (RFC 6979), поэтому сверяем сами байты.

    Проверки «подпись валидна» было бы мало: она прошла бы и при неверно
    выведенном нонсе, а повтор или предсказуемость нонса раскрывают ключ.
    """
    payloads = [b"", b"abc", b"deadbeef", b"i-3ru", bytes(range(64)), b"z" * 300]
    for private in PRIVATE_KEYS:
        wallet = Wallet.from_private_hex(private)
        for payload in payloads:
            assert run(bridge, "sign", private, payload.hex()) == \
                wallet.sign(payload), (private, payload)


def test_python_verifies_cpp_signature(bridge):
    private = PRIVATE_KEYS[2]
    wallet = Wallet.from_private_hex(private)
    payload = b"handshake transcript"
    signature = run(bridge, "sign", private, payload.hex())
    assert Wallet.verify(wallet.public_key_hex, payload, signature) is True


def test_cpp_verifies_python_signature(bridge):
    wallet = generate_wallet()
    payload = b"another transcript"
    signature = wallet.sign(payload)
    assert run(bridge, "verify", wallet.public_key_hex, payload.hex(),
               signature) == "ok"
    # Испорченная подпись обязана отвергаться.
    broken = bytearray(bytes.fromhex(signature))
    broken[5] ^= 0x01
    assert run(bridge, "verify", wallet.public_key_hex, payload.hex(),
               bytes(broken).hex()) == "bad"


def test_cpp_refuses_a_point_off_the_curve(bridge):
    """Точка не на кривой отвергается и в нативной реализации."""
    bogus = ("04" + "01" * 32 + "01" * 32)
    result = subprocess.run([bridge, "ecdh", PRIVATE_KEYS[2], bogus],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode != 0
    assert "кривой" in result.stderr


# --- ECDH и ключи сессии ------------------------------------------------------
def test_ecdh_agrees_with_python(bridge):
    alice, bob = generate_wallet(), generate_wallet()
    shared = secure._shared_secret(int(alice.private_key_hex, 16),
                                   bob.public_key_bytes)
    assert run(bridge, "ecdh", alice.private_key_hex,
               bob.public_key_bytes.hex()) == shared.hex()
    # Симметричность: у обеих сторон один и тот же секрет.
    assert run(bridge, "ecdh", bob.private_key_hex,
               alice.public_key_bytes.hex()) == shared.hex()


def test_session_keys_match_python(bridge):
    shared = bytes(range(32))
    transcript = secure.MAGIC + b"\x04" + b"\xab" * 64
    keys = secure._derive(shared, transcript)
    expected = " ".join([keys["c2s"].hex(), keys["s2c"].hex(),
                         keys["mac_c2s"].hex(), keys["mac_s2c"].hex()])
    assert run(bridge, "derive", shared.hex(), transcript.hex()) == expected


def test_keystream_matches_python(bridge):
    for key, seq, size in ((b"\x11" * 32, 0, 100), (b"\xab" * 32, 7777, 200),
                           (b"\x00" * 32, 2 ** 40, 64)):
        assert run(bridge, "keystream", key.hex(), seq, size) == \
            secure._keystream(key, seq, size).hex()


# --- Кадры --------------------------------------------------------------------
CIPHER_KEY = bytes(range(32))
MAC_KEY = bytes(range(32, 64))


def _python_session(is_client=True):
    keys = {"c2s": CIPHER_KEY, "s2c": CIPHER_KEY,
            "mac_c2s": MAC_KEY, "mac_s2c": MAC_KEY}
    return secure.Session(keys, is_client=is_client)


@pytest.mark.parametrize("seq,payload", [
    (0, b"get_height"),
    (1, b""),
    (5, bytes(range(256)) * 3),
    (2 ** 32, b"large sequence number"),
])
def test_frames_match_python(bridge, seq, payload):
    session = _python_session()
    session._out_seq = seq
    expected = session.encrypt(payload)
    assert run(bridge, "frame", CIPHER_KEY.hex(), MAC_KEY.hex(), seq,
               payload.hex()) == expected.hex()


def test_python_decrypts_a_cpp_frame(bridge):
    frame = bytes.fromhex(run(bridge, "frame", CIPHER_KEY.hex(), MAC_KEY.hex(),
                              0, b"secp256k1".hex()))
    assert _python_session(is_client=False).decrypt(frame) == b"secp256k1"


def test_cpp_decrypts_a_python_frame(bridge):
    frame = _python_session().encrypt(b"i-3ru")
    assert run(bridge, "unframe", CIPHER_KEY.hex(), MAC_KEY.hex(), 0,
               frame.hex()) == b"i-3ru".hex()


def test_cpp_rejects_a_tampered_frame(bridge):
    frame = bytearray(_python_session().encrypt(b"transfer 10"))
    frame[10] ^= 0x01
    result = subprocess.run([bridge, "unframe", CIPHER_KEY.hex(), MAC_KEY.hex(),
                             "0", bytes(frame).hex()],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode != 0
    assert "тег" in result.stderr


# --- Живое рукопожатие --------------------------------------------------------
def test_python_client_talks_to_a_cpp_server(bridge):
    """Рукопожатие Python → C++ через настоящий сокет."""
    port = _free_port()
    identity = generate_wallet()
    server = subprocess.Popen(
        [bridge, "serve", str(port), identity.private_key_hex, "3"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        line = server.stdout.readline().split()
        assert line and line[0] == "ready"
        assert line[1] == identity.public_key_hex   # ключ узла тот же
        sock = socket.create_connection(("127.0.0.1", port), timeout=30)
        try:
            session = secure.client_handshake(
                sock, expect_key=identity.public_key_hex)
            assert session.peer_key == identity.public_key_hex
            for payload in (b"i-3ru", b'{"type": "ping"}', bytes(range(256))):
                send_message(sock, session.encrypt(payload))
                assert session.decrypt(recv_message(sock)) == b"cpp:" + payload
        finally:
            sock.close()
    finally:
        server.kill()
        server.wait(timeout=30)


def test_cpp_client_talks_to_a_python_server(bridge):
    """Рукопожатие C++ → Python: нативный клиент проверяет подпись Python."""
    identity = generate_wallet()
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    listener.listen(2)
    received = []

    def serve():
        conn, _ = listener.accept()
        session = secure.server_handshake(conn, recv_message(conn), identity)
        while True:
            frame = recv_message(conn)
            if not frame:
                break
            message = session.decrypt(frame)
            received.append(message)
            send_message(conn, session.encrypt(b"py:" + message))
        conn.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    time.sleep(0.2)
    try:
        out = run(bridge, "connect", "127.0.0.1", port,
                  identity.public_key_hex, "hello", "secp256k1").split("\n")
        assert out[0] == "peer " + identity.public_key_hex
        assert out[1] == "py:hello" and out[2] == "py:secp256k1"
        assert received == [b"hello", b"secp256k1"]
    finally:
        listener.close()


def test_cpp_client_queries_a_real_node(bridge):
    """Нативный клиент — полноценный участник сети: узел отвечает ему по делу."""
    node = P2PNode("127.0.0.1", _free_port(), BHydraNode(difficulty=1))
    node.start()
    time.sleep(0.3)
    try:
        node.node.mine_pending(generate_wallet().address)
        out = run(bridge, "connect", node.host, node.port, node.node_key,
                  '{"type": "ping"}', '{"type": "get_height"}').split("\n")
        assert out[0] == "peer " + node.node_key
        assert json.loads(out[1])["type"] == "pong"
        assert json.loads(out[2])["height"] == node.node.height
    finally:
        node.stop()


def test_cpp_client_refuses_a_wrong_pinned_key(bridge):
    """Закрепление работает и в нативном клиенте: чужой ключ — отказ."""
    node = P2PNode("127.0.0.1", _free_port(), BHydraNode(difficulty=1))
    node.start()
    time.sleep(0.3)
    try:
        result = subprocess.run(
            [bridge, "connect", node.host, str(node.port),
             generate_wallet().public_key_hex, '{"type": "ping"}'],
            capture_output=True, text=True, timeout=120)
        assert result.returncode != 0
        assert "не совпал" in result.stderr
    finally:
        node.stop()


def test_cpp_selftest_passes(bridge):
    """Свои контрольные векторы: битая сборка не должна «работать»."""
    assert run(bridge, "selftest") == "ok"


def test_odd_length_hex_is_refused(bridge):
    """Hex нечётной длины — ошибка, а не «последний символ отбросим».

    Молчаливое усечение дало бы нулевой ключ вместо заявленного, и код
    продолжил бы работать, «шифруя» на нём.
    """
    result = subprocess.run([bridge, "sha512", "abc"],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode != 0
