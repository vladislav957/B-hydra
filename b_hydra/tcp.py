"""
TCP.py — простые TCP-помощники B-hydra.

Тонкие обёртки для отправки и приёма сообщений с длиной-префиксом, чтобы
сообщения не «слипались» в потоке. Используются сетевым слоем P2P.py.
"""

import socket
import struct


def send_message(sock: socket.socket, data: bytes) -> None:
    """Отправляет сообщение с 4-байтовым префиксом длины."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    sock.sendall(struct.pack(">I", len(data)) + data)


def recv_exactly(sock: socket.socket, n: int) -> bytes:
    """Читает ровно n байт или возвращает b'' при разрыве соединения."""
    chunks = bytearray()
    while len(chunks) < n:
        chunk = sock.recv(n - len(chunks))
        if not chunk:
            return b""
        chunks.extend(chunk)
    return bytes(chunks)


# Анти-DoS: верхний предел размера одного сообщения (32 МБ).
MAX_MESSAGE_SIZE = 32 * 1024 * 1024


def recv_message(sock: socket.socket) -> bytes:
    """Читает одно сообщение с префиксом длины (с лимитом размера)."""
    header = recv_exactly(sock, 4)
    if not header:
        return b""
    (length,) = struct.unpack(">I", header)
    if length > MAX_MESSAGE_SIZE:
        return b""  # отвергаем подозрительно большое сообщение (защита от DoS)
    return recv_exactly(sock, length)


def start_server(host="127.0.0.1", port=5000, handler=None):
    """
    Запускает блокирующий TCP-сервер. На каждое сообщение вызывает
    handler(message_bytes) -> Optional[bytes-ответ].
    """
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((host, port))
    server.listen(5)
    print(f"[TCP] Сервер запущен на {host}:{port}")
    try:
        while True:
            conn, addr = server.accept()
            with conn:
                message = recv_message(conn)
                if not message:
                    continue
                response = handler(message) if handler else b"OK"
                if response:
                    send_message(conn, response)
    finally:
        server.close()


def connect_and_send(host="127.0.0.1", port=5000, message=b"") -> bytes:
    """Подключается, отправляет сообщение и возвращает ответ."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
        client.connect((host, port))
        send_message(client, message)
        return recv_message(client)


if __name__ == "__main__":
    print("TCP-помощники B-hydra. Импортируйте send_message/recv_message.")
