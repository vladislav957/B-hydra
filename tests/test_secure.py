"""Тесты шифрования транспорта: рукопожатие ECDH, кадры, подмена и повторы."""

import hashlib
import socket
import threading

import pytest

from b_hydra import secure
from b_hydra.secure import (DecryptError, HandshakeError, MAGIC, Session,
                            client_handshake, server_handshake)
from b_hydra.tcp import recv_message, send_message
from b_hydra.wallet import generate_wallet


def _handshake_pair(identity=None, expect_key=None):
    """Рукопожатие через socketpair: возвращает (сессия клиента, сессия сервера)."""
    identity = identity or generate_wallet()
    left, right = socket.socketpair()
    result = {}

    def serve():
        try:
            result["server"] = server_handshake(right, recv_message(right), identity)
        except OSError as exc:            # HandshakeError — тоже OSError
            result["error"] = exc

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        client = client_handshake(left, expect_key=expect_key)
    finally:
        thread.join(timeout=15)
    if "error" in result:
        raise result["error"]
    return client, result["server"], identity, (left, right)


# --- Рукопожатие -------------------------------------------------------------
def test_handshake_agrees_on_the_same_keys():
    client, server, identity, socks = _handshake_pair()
    try:
        assert client.peer_key == identity.public_key_hex
        message = b"get_height"
        assert server.decrypt(client.encrypt(message)) == message
        assert client.decrypt(server.encrypt(message)) == message
    finally:
        for sock in socks:
            sock.close()


def test_each_connection_gets_fresh_session_keys():
    """Ключи сессии одноразовые — это и есть прямая секретность.

    Долговременный ключ узла тот же, но записанный вчера трафик им не
    расшифровать: ключи выведены из ЭФЕМЕРНЫХ пар, которых уже нет.
    """
    identity = generate_wallet()
    first, _s1, _i, socks1 = _handshake_pair(identity)
    second, _s2, _i, socks2 = _handshake_pair(identity)
    try:
        assert first.peer_key == second.peer_key      # узел тот же
        assert first._out != second._out              # а ключи сессии разные
        assert first._in != second._in
    finally:
        for sock in socks1 + socks2:
            sock.close()


def test_directions_use_different_keys():
    """Клиент→сервер и сервер→клиент шифруются РАЗНЫМИ ключами.

    С одним ключом на оба направления кадр можно было бы отразить узлу обратно,
    и он принял бы собственный трафик за ответ пира.
    """
    client, server, _identity, socks = _handshake_pair()
    try:
        assert client.encrypt(b"x" * 32)[8:] != server.encrypt(b"x" * 32)[8:]
    finally:
        for sock in socks:
            sock.close()


def test_pinned_key_mismatch_is_refused():
    """Ключ не совпал с запомненным — соединения не будет (подмена узла)."""
    with pytest.raises(HandshakeError):
        _handshake_pair(expect_key=generate_wallet().public_key_hex)


def test_pinned_key_match_is_accepted():
    identity = generate_wallet()
    client, _server, _i, socks = _handshake_pair(
        identity, expect_key=identity.public_key_hex)
    try:
        assert client.peer_key == identity.public_key_hex
    finally:
        for sock in socks:
            sock.close()


def test_signature_binds_the_ephemeral_keys():
    """Посредник не может подставить свой эфемерный ключ к чужой подписи.

    Стенограмма (метка + оба эфемерных ключа + ключ узла) подписана целиком,
    поэтому подмена любого её байта ломает проверку подписи.
    """
    identity = generate_wallet()
    left, right = socket.socketpair()
    try:
        def serve():
            first = recv_message(right)
            peer_eph = first[len(MAGIC):]
            _priv, eph_pub = secure._ephemeral()
            id_pub = identity.public_key_bytes
            honest = MAGIC + peer_eph + eph_pub + id_pub
            signature = bytes.fromhex(identity.sign(honest))
            # Подпись настоящая, но эфемерный ключ подменён на чужой.
            _priv2, forged_eph = secure._ephemeral()
            send_message(right, forged_eph + id_pub + signature)

        threading.Thread(target=serve, daemon=True).start()
        with pytest.raises(HandshakeError):
            client_handshake(left)
    finally:
        left.close()
        right.close()


def test_public_key_off_the_curve_is_refused():
    """Точка не на кривой отвергается (invalid-curve атака).

    Умножение на подсунутой точке малого порядка выдало бы общий секрет по
    частям.
    """
    bogus = b"\x04" + (1).to_bytes(32, "big") + (1).to_bytes(32, "big")
    with pytest.raises(HandshakeError):
        secure._shared_secret(12345, bogus)
    with pytest.raises(HandshakeError):
        secure._shared_secret(12345, b"\x02" + b"\x11" * 64)   # не 0x04
    with pytest.raises(HandshakeError):
        secure._shared_secret(12345, b"\x04" + b"\x11" * 10)   # короткий


def test_plaintext_first_frame_is_not_a_handshake():
    assert secure.is_handshake(b'{"type": "ping"}') is False
    assert secure.is_handshake(b"") is False
    assert secure.is_handshake(MAGIC + b"\x04" + b"\x00" * 64) is True


def test_server_refuses_a_truncated_handshake():
    left, right = socket.socketpair()
    try:
        with pytest.raises(HandshakeError):
            server_handshake(right, MAGIC + b"\x04\x01\x02", generate_wallet())
    finally:
        left.close()
        right.close()


# --- Кадры: тайна, целостность, порядок --------------------------------------
def _session_pair():
    keys = secure._derive(b"\x11" * 32, "стенограмма".encode())
    return Session(keys, is_client=True), Session(keys, is_client=False)


def test_frame_hides_the_payload():
    client, server = _session_pair()
    secret = b"BHYDdbQfB7EfdKZi3fuX3A2bkwPmq7XsBaGFP"
    frame = client.encrypt(secret)
    assert secret not in frame                     # открытого текста в кадре нет
    assert server.decrypt(frame) == secret


def test_tampered_ciphertext_is_rejected():
    client, server = _session_pair()
    frame = bytearray(client.encrypt("перевод 10 монет".encode()))
    frame[10] ^= 0x01                              # один бит в шифртексте
    with pytest.raises(DecryptError):
        server.decrypt(bytes(frame))


def test_tampered_tag_is_rejected():
    client, server = _session_pair()
    frame = bytearray(client.encrypt(b"ping"))
    frame[-1] ^= 0xff
    with pytest.raises(DecryptError):
        server.decrypt(bytes(frame))


def test_replayed_frame_is_rejected():
    """Повтор того же кадра не принимается — у кадров порядковые номера."""
    client, server = _session_pair()
    frame = client.encrypt(b"get_height")
    assert server.decrypt(frame) == b"get_height"
    with pytest.raises(DecryptError):
        server.decrypt(frame)


def test_dropped_frame_is_rejected():
    """Выброшенный из потока кадр — это тоже подмена, а не потеря.

    Проверки «номер не меньше прошлого» было бы мало: она разрешает тихо
    потерять кадр, например анонс блока.
    """
    client, server = _session_pair()
    client.encrypt("первый".encode())             # этот кадр «не доехал»
    with pytest.raises(DecryptError):
        server.decrypt(client.encrypt("второй".encode()))


def test_reordered_frames_are_rejected():
    client, server = _session_pair()
    one, two = client.encrypt(b"one"), client.encrypt(b"two")
    with pytest.raises(DecryptError):
        server.decrypt(two)                        # пришёл раньше первого
    assert server.decrypt(one) == b"one"


def test_short_frame_is_rejected():
    _client, server = _session_pair()
    with pytest.raises(DecryptError):
        server.decrypt(b"\x00" * 8)


def test_empty_payload_roundtrip():
    client, server = _session_pair()
    assert server.decrypt(client.encrypt(b"")) == b""


def test_large_frame_roundtrip():
    """Кадр в мегабайт: путь XOR через большое целое."""
    client, server = _session_pair()
    payload = bytes(range(256)) * 4096             # 1 МиБ
    assert server.decrypt(client.encrypt(payload)) == payload


def test_frame_overhead_is_seq_plus_tag():
    client, _server = _session_pair()
    frame = client.encrypt(b"x" * 100)
    assert len(frame) == 100 + secure.SEQ_LEN + secure.TAG_LEN


# --- Примитивы ---------------------------------------------------------------
def test_keystream_depends_on_key_and_sequence():
    a = secure._keystream(b"\x01" * 32, 0, 64)
    assert a != secure._keystream(b"\x02" * 32, 0, 64)   # другой ключ
    assert a != secure._keystream(b"\x01" * 32, 1, 64)   # другой номер
    assert a == secure._keystream(b"\x01" * 32, 0, 64)   # воспроизводим
    assert len(secure._keystream(b"\x01" * 32, 0, 1000)) == 1000


def test_xor_matches_the_byte_by_byte_version():
    """Быстрый XOR через целое обязан совпадать с наивным побайтным."""
    data, stream = bytes(range(256)), bytes(range(255, -1, -1))
    naive = bytes(a ^ b for a, b in zip(data, stream))
    assert secure._xor(data, stream) == naive
    assert secure._xor(b"", b"") == b""
    assert secure._xor(b"\x00\x01", b"\x00\x00") == b"\x00\x01"   # ведущий ноль


def test_shake256_matches_the_reference_vector():
    """SHAKE-256 сверен с вектором NIST — битая сборка hashlib не пройдёт.

    Тот же вектор проверяет `secure._selftest()` при импорте: «шифрование»,
    которое не шифрует, должно падать сразу, а не тихо работать.
    """
    assert hashlib.shake_256(b"").hexdigest(32) == (
        "46b9dd2b0ba88d13233b3feb743eeb243fcd52ea62b81b82b50c27646ed5762f")
    secure._selftest()                             # не должен бросать


def test_our_hmac_matches_hashlib():
    """HMAC на нашем SHA-512 совпадает с hashlib байт-в-байт."""
    import hmac as stdlib_hmac
    for key, message in ((b"key", b"message"), (b"k" * 200, b""), (b"", b"x" * 500)):
        assert secure._hmac_ours(key, message) == stdlib_hmac.new(
            key, message, hashlib.sha512).digest()


def test_derived_keys_differ_by_role_and_purpose():
    keys = secure._derive(b"\x07" * 32, b"transcript")
    assert len({keys["c2s"], keys["s2c"], keys["mac_c2s"], keys["mac_s2c"]}) == 4
    other = secure._derive(b"\x07" * 32, b"transcript-2")
    assert other["c2s"] != keys["c2s"]             # стенограмма входит в вывод


def test_sequence_counter_has_a_ceiling():
    """Счётчик кадров не переполняется молча: повтор номера повторил бы keystream."""
    client, _server = _session_pair()
    client._out_seq = secure.MAX_SEQ
    with pytest.raises(DecryptError):
        client.encrypt(b"x")
