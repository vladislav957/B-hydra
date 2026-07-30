"""
secure.py — шифрование транспорта P2P: ECDH на secp256k1 + потоковый шифр.

Трафик узлов ходил открытым текстом. Кто видел канал — видел всё: какие адреса
у кого в кошельке, какие транзакции узел отправляет ПЕРВЫМ (а значит, чьи они),
кто с кем в соседях. Провайдер, владелец WiFi-точки или сосед по локальной сети
читали это, не прилагая усилий.

Здесь — шифрование канала на собственной криптографии проекта, без зависимостей:

  * обмен ключами: эфемерный ECDH на secp256k1 (та же кривая, что у подписей,
    `wallet._scalar_mult`). Ключи одноразовые, на соединение → прямая
    секретность (perfect forward secrecy): утечка долговременного ключа НЕ
    расшифровывает записанные раньше сессии;
  * подлинность узла: сервер подписывает стенограмму рукопожатия своим
    долговременным ключом (`Wallet.sign`, RFC 6979). Клиент запоминает этот
    ключ для пары (host, port) при первом успешном соединении и требует его же
    впоследствии — доверие при первом контакте (TOFU, как в SSH);
  * шифр: потоковый, keystream = SHAKE-256, XOR с данными; ключи РАЗНЫЕ по
    направлениям (иначе трафик можно было бы отражать узлу обратно);
  * целостность: HMAC-SHA512 (Encrypt-then-MAC), тег проверяется ДО расшифровки;
  * порядок и повторы: у каждого кадра порядковый номер, и он обязан идти строго
    подряд. Это закрывает и повтор кадра, и его выбрасывание, и перестановку.

⚠️ ЧТО ЭТО НЕ ДАЁТ. Активный посредник при ПЕРВОМ соединении с новым пиром
подменит ключ и останется незамеченным — как и в SSH, доверие берётся из первого
контакта. Со второго раза подмена видна (ключ не совпал с запомненным). Клиент
себя не аутентифицирует вовсе: у входящего соединения всё равно виден только IP,
а репутация и баны считаются по нему.

⚠️ Понижения версии (downgrade) НЕТ: если рукопожатие не удалось, соединение
обрывается, а не продолжается открытым текстом. Молчаливый откат — ровно то,
чего добивается активный атакующий: испортил рукопожатие и читает дальше.
Разговор с открытым узлом — только явным `encrypt=False`.

⚠️ Почему здесь hashlib, а не наш чистый SHA-512. Замер: keystream на нашем
Python-SHA — 0,18 МиБ/с, то есть блок в 4 МиБ шифровался бы 22 секунды.
На hashlib-SHA-512 — 26 МиБ/с, на SHAKE-256 — 256 МиБ/с. Это не выбор между
«чисто» и «быстро», а между «работает» и «не работает». Правило «своё, а
hashlib только ускоритель» держится там, где важна ВОСПРОИЗВОДИМОСТЬ снаружи:
адреса, txid, корень Меркла. Транспортный шифр никто не пересчитывает офлайн —
он живёт одну сессию. Байтовое соответствие SHAKE-256 проверяется на
контрольных векторах при импорте (`_selftest`), чтобы битая сборка не осталась
незамеченной.
"""

from __future__ import annotations

import hashlib
import hmac as _hmac
import os
import secrets

if __name__ == "__main__" and __package__ in (None, ""):
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    __package__ = "b_hydra"

from . import hashing
from .tcp import recv_message, send_message
from .wallet import Wallet, _G, _N, _P, _is_on_curve, _scalar_mult

# Метка зашифрованного рукопожатия. Сервер отличает по ней шифрованного клиента
# от открытого: обычное сообщение — это JSON, он начинается с "{".
MAGIC = b"BHYE1"
PUBLIC_KEY_LEN = 65             # несжатая точка 0x04||X||Y
SIGNATURE_LEN = 64              # r||s по 32 байта
KEY_LEN = 32                    # ключи шифра и MAC
TAG_LEN = 32                    # усечённый HMAC-SHA512
SEQ_LEN = 8                     # порядковый номер кадра
HANDSHAKE_TIMEOUT = 10.0        # на рукопожатие; ECDH на чистом Python не мгновенный

# Максимальный номер кадра. Переполнение счётчика повторило бы keystream для
# того же номера — соединение обрывается задолго до этого.
MAX_SEQ = 2 ** 63


class HandshakeError(OSError):
    """Рукопожатие не удалось: пир не шифрует, подменён или прислал мусор.

    Наследуется от OSError намеренно: для вызывающего это отказ соединения, и
    весь существующий разбор ошибок сети (`_fanout`, повтор в `send`) работает
    без изменений. Иначе одно неудачное рукопожатие ломало бы всю рассылку.
    """


class DecryptError(OSError):
    """Кадр не прошёл проверку: подделан, повторён или переставлен."""


def _selftest() -> None:
    """Сверяет SHAKE-256 и HMAC-SHA512 с контрольными векторами.

    Без этого битая или урезанная сборка hashlib дала бы «шифрование», которое
    на самом деле не шифрует, — и мы бы этого не заметили.
    """
    # SHAKE-256 от пустой строки (NIST FIPS 202, первые 32 байта).
    expected = ("46b9dd2b0ba88d13233b3feb743eeb243fcd52ea62b81b82"
                "b50c27646ed5762f")
    if hashlib.shake_256(b"").hexdigest(32) != expected:
        raise RuntimeError("hashlib.shake_256 даёт неверный результат")
    # HMAC-SHA512 сверяем с нашей собственной реализацией на нашем SHA.
    key, message = b"key", b"The quick brown fox"
    if _hmac.new(key, message, hashlib.sha512).digest() != _hmac_ours(key, message):
        raise RuntimeError("HMAC-SHA512 из hashlib расходится с нашим SHA-512")


def _hmac_ours(key: bytes, message: bytes) -> bytes:
    """HMAC-SHA512 на НАШЕМ SHA-512 (RFC 2104) — для мелких входов.

    Вывод ключей — единицы вызовов на соединение, поэтому здесь можно позволить
    себе свою реализацию; для потока байтов она слишком медленная.
    """
    block = 128                                  # размер блока SHA-512
    if len(key) > block:
        key = hashing.sha512_bytes(key)
    key = key.ljust(block, b"\x00")
    inner = hashing.sha512_bytes(bytes(b ^ 0x36 for b in key) + message)
    return hashing.sha512_bytes(bytes(b ^ 0x5c for b in key) + inner)


def _mac(key: bytes, message: bytes) -> bytes:
    """Тег целостности кадра: HMAC-SHA512 (hashlib — счёт идёт по мегабайтам)."""
    return _hmac.new(key, message, hashlib.sha512).digest()[:TAG_LEN]


def _keystream(key: bytes, seq: int, length: int) -> bytes:
    """Псевдослучайный поток: SHAKE-256(key || seq) нужной длины.

    XOF отдаёт сколько угодно байтов ОДНИМ вызовом — не нужен цикл по блокам,
    который на счётчике из SHA-512 стоил бы в десять раз дороже.
    """
    return hashlib.shake_256(key + seq.to_bytes(SEQ_LEN, "big")).digest(length)


def _xor(data: bytes, stream: bytes) -> bytes:
    """XOR двух равных по длине блоков — через одно большое целое.

    Побайтно (zip) выходит 15 МиБ/с, через int — 100 МиБ/с: XOR мегабайтных
    кадров идёт в C, а не в интерпретаторе.
    """
    if not data:
        return b""
    return (int.from_bytes(data, "big")
            ^ int.from_bytes(stream, "big")).to_bytes(len(data), "big")


def _ephemeral():
    """Одноразовая пара ключей для ECDH: (приватный int, публичные 65 байт)."""
    priv = secrets.randbelow(_N - 2) + 1
    x, y = _scalar_mult(priv, _G)
    return priv, b"\x04" + x.to_bytes(32, "big") + y.to_bytes(32, "big")


def _shared_secret(priv: int, peer_public: bytes) -> bytes:
    """ECDH: общий секрет как X-координата priv × PeerPub.

    Точка пира ОБЯЗАНА лежать на кривой: иначе умножение на подсунутой «кривой»
    точке малого порядка выдаёт секрет по частям (invalid-curve атака).
    """
    if len(peer_public) != PUBLIC_KEY_LEN or peer_public[0] != 0x04:
        raise HandshakeError("некорректный публичный ключ пира")
    x = int.from_bytes(peer_public[1:33], "big")
    y = int.from_bytes(peer_public[33:65], "big")
    if not (0 < x < _P and 0 < y < _P) or not _is_on_curve((x, y)):
        raise HandshakeError("публичный ключ пира не на кривой")
    point = _scalar_mult(priv, (x, y))
    if point is None:
        raise HandshakeError("вырожденный общий секрет")
    return point[0].to_bytes(32, "big")


def _derive(shared: bytes, transcript: bytes):
    """Ключи сессии из общего секрета и стенограммы рукопожатия.

    Стенограмма (метка + оба эфемерных ключа + ключ узла) входит в вывод, поэтому
    ключи привязаны к КОНКРЕТНОМУ рукопожатию: подменить в нём хоть байт — и
    стороны получат разные ключи, а первый же кадр не пройдёт проверку тега.
    """
    prk = _hmac_ours(transcript, shared)
    return {
        "c2s": _hmac_ours(prk, b"BHY c2s cipher")[:KEY_LEN],
        "s2c": _hmac_ours(prk, b"BHY s2c cipher")[:KEY_LEN],
        "mac_c2s": _hmac_ours(prk, b"BHY c2s mac")[:KEY_LEN],
        "mac_s2c": _hmac_ours(prk, b"BHY s2c mac")[:KEY_LEN],
    }


class Session:
    """Зашифрованный канал: шифрует исходящие кадры и расшифровывает входящие."""

    def __init__(self, keys, is_client: bool, peer_key: str = None):
        self.peer_key = peer_key            # долговременный ключ пира (hex) или None
        self._out = keys["c2s"] if is_client else keys["s2c"]
        self._in = keys["s2c"] if is_client else keys["c2s"]
        self._out_mac = keys["mac_c2s"] if is_client else keys["mac_s2c"]
        self._in_mac = keys["mac_s2c"] if is_client else keys["mac_c2s"]
        self._out_seq = 0
        self._in_seq = 0

    def encrypt(self, payload: bytes) -> bytes:
        """Кадр: номер || шифртекст || тег."""
        if self._out_seq >= MAX_SEQ:
            raise DecryptError("счётчик кадров исчерпан")
        seq = self._out_seq
        self._out_seq += 1
        cipher = _xor(payload, _keystream(self._out, seq, len(payload)))
        head = seq.to_bytes(SEQ_LEN, "big")
        return head + cipher + _mac(self._out_mac, head + cipher)

    def decrypt(self, frame: bytes) -> bytes:
        """Проверяет тег и порядок, затем расшифровывает."""
        if len(frame) < SEQ_LEN + TAG_LEN:
            raise DecryptError("кадр короче заголовка")
        head, cipher, tag = (frame[:SEQ_LEN], frame[SEQ_LEN:-TAG_LEN],
                             frame[-TAG_LEN:])
        # Тег — ДО расшифровки (Encrypt-then-MAC) и сравнение постоянного
        # времени: побайтная проверка с ранним выходом подсказывает атакующему,
        # сколько байтов тега он угадал.
        if not _hmac.compare_digest(tag, _mac(self._in_mac, head + cipher)):
            raise DecryptError("тег кадра не совпал — подделка или порча")
        seq = int.from_bytes(head, "big")
        # Номер обязан идти строго подряд. Проверки «не меньше прошлого» мало:
        # она разрешает ВЫБРОСИТЬ кадр из потока, а это тоже подмена — например,
        # тихо потерянный анонс блока.
        if seq != self._in_seq:
            raise DecryptError(f"кадр вне очереди: ждали {self._in_seq}, пришёл {seq}")
        self._in_seq += 1
        return _xor(cipher, _keystream(self._in, seq, len(cipher)))


def client_handshake(sock, identity: Wallet = None, expect_key: str = None):
    """Клиентская половина рукопожатия. Возвращает Session.

    `expect_key` — запомненный ранее ключ этого пира: если он задан и не совпал,
    соединение обрывается (защита от подмены со второго контакта).
    """
    previous = sock.gettimeout()
    try:
        sock.settimeout(HANDSHAKE_TIMEOUT)
        eph_priv, eph_pub = _ephemeral()
        send_message(sock, MAGIC + eph_pub)
        reply = recv_message(sock)
        if not reply or len(reply) != PUBLIC_KEY_LEN * 2 + SIGNATURE_LEN:
            raise HandshakeError("пир не ответил на рукопожатие")
        peer_eph = reply[:PUBLIC_KEY_LEN]
        peer_id = reply[PUBLIC_KEY_LEN:PUBLIC_KEY_LEN * 2]
        signature = reply[PUBLIC_KEY_LEN * 2:]
        transcript = MAGIC + eph_pub + peer_eph + peer_id
        peer_key = peer_id.hex()
        if not Wallet.verify(peer_key, transcript, signature.hex()):
            raise HandshakeError("подпись рукопожатия не сошлась")
        # Ключ пира сверяем ПОСЛЕ подписи: иначе о совпадении ключа судили бы по
        # неподтверждённому полю, которое кто угодно может скопировать.
        if expect_key is not None and peer_key != expect_key:
            raise HandshakeError("ключ пира не совпал с запомненным")
        keys = _derive(_shared_secret(eph_priv, peer_eph), transcript)
        return Session(keys, is_client=True, peer_key=peer_key)
    except (OSError, ValueError) as err:
        raise HandshakeError(f"рукопожатие не удалось: {err}") from err
    finally:
        try:
            sock.settimeout(previous)
        except OSError:
            pass


def server_handshake(sock, first_frame: bytes, identity: Wallet):
    """Серверная половина. `first_frame` — уже прочитанный первый кадр.

    Первый кадр читает вызывающий (ему нужно решить, шифрованный клиент или
    открытый), поэтому он передаётся сюда готовым.
    """
    if not is_handshake(first_frame):
        raise HandshakeError("это не зашифрованное рукопожатие")
    if len(first_frame) != len(MAGIC) + PUBLIC_KEY_LEN:
        raise HandshakeError("некорректная длина рукопожатия")
    try:
        peer_eph = first_frame[len(MAGIC):]
        eph_priv, eph_pub = _ephemeral()
        id_pub = identity.public_key_bytes
        transcript = MAGIC + peer_eph + eph_pub + id_pub
        signature = bytes.fromhex(identity.sign(transcript))
        send_message(sock, eph_pub + id_pub + signature)
        keys = _derive(_shared_secret(eph_priv, peer_eph), transcript)
        return Session(keys, is_client=False)
    except (OSError, ValueError) as err:
        raise HandshakeError(f"рукопожатие не удалось: {err}") from err


def is_handshake(frame: bytes) -> bool:
    """Похож ли первый кадр на зашифрованное рукопожатие."""
    return bool(frame) and frame.startswith(MAGIC)


_selftest()


if __name__ == "__main__":
    import socket
    import threading
    import time

    server_identity = Wallet()
    ready = threading.Event()
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    listener.listen(1)

    def serve():
        ready.set()
        conn, _ = listener.accept()
        with conn:
            session = server_handshake(conn, recv_message(conn), server_identity)
            while True:
                frame = recv_message(conn)
                if not frame:
                    break
                message = session.decrypt(frame)
                send_message(conn, session.encrypt("эхо: ".encode() + message))

    threading.Thread(target=serve, daemon=True).start()
    ready.wait()
    client = socket.socket()
    client.connect(("127.0.0.1", port))
    started = time.time()
    session = client_handshake(client)
    print(f"рукопожатие: {(time.time() - started) * 1000:.0f} мс")
    print(f"ключ узла   : {session.peer_key[:32]}…")
    for text in ("привет".encode(), b"secp256k1 + SHAKE-256"):
        send_message(client, session.encrypt(text))
        print(" ", session.decrypt(recv_message(client)).decode("utf-8"))
    client.close()
    listener.close()
