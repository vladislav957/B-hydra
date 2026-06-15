"""
P2P.py — одноранговый сетевой слой B-hydra.

Узел поднимает TCP-сервер в отдельном потоке и умеет подключаться к другим
узлам, обмениваясь JSON-сообщениями (транзакции, блоки, запрос цепочки).
Логика блокчейна живёт в Node.py; здесь — только транспорт и протокол.
"""

import json
import socket
import threading

from TCP import send_message, recv_message


class P2PNode:
    """Сетевой одноранговый узел B-hydra."""

    def __init__(self, host="127.0.0.1", port=5000, node=None):
        self.host = host
        self.port = port
        self.node = node            # ссылка на BHydraNode (логика), опционально
        self.peers = set()          # известные пиры (host, port)
        self._server = None
        self._running = False

    # --- Протокол --------------------------------------------------------
    def _handle_message(self, raw: bytes) -> bytes:
        """Обрабатывает входящее сообщение и формирует ответ."""
        try:
            message = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return json.dumps({"type": "error", "error": "bad json"}).encode("utf-8")

        msg_type = message.get("type")

        if msg_type == "ping":
            return json.dumps({"type": "pong"}).encode("utf-8")

        if msg_type == "get_chain" and self.node is not None:
            chain = [b.to_dict() for b in self.node.blockchain.chain]
            return json.dumps({"type": "chain", "chain": chain}).encode("utf-8")

        if msg_type == "transaction" and self.node is not None:
            from Transactinons import Transaction
            tx = Transaction.from_dict(message["transaction"])
            accepted = self.node.add_transaction(tx)
            return json.dumps({"type": "ack", "accepted": accepted}).encode("utf-8")

        return json.dumps({"type": "ack"}).encode("utf-8")

    # --- Сервер ----------------------------------------------------------
    def _serve(self):
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind((self.host, self.port))
        self._server.listen(5)
        self._running = True
        while self._running:
            try:
                conn, _addr = self._server.accept()
            except OSError:
                break
            with conn:
                raw = recv_message(conn)
                if raw:
                    send_message(conn, self._handle_message(raw))

    def start(self):
        """Запускает сервер в фоновом потоке."""
        thread = threading.Thread(target=self._serve, daemon=True)
        thread.start()
        return thread

    def stop(self):
        self._running = False
        if self._server is not None:
            self._server.close()

    # --- Клиент ----------------------------------------------------------
    def send(self, host, port, message: dict) -> dict:
        """Отправляет сообщение пиру и возвращает разобранный ответ."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
            client.connect((host, port))
            send_message(client, json.dumps(message).encode("utf-8"))
            raw = recv_message(client)
        return json.loads(raw.decode("utf-8")) if raw else {}

    def add_peer(self, host, port):
        self.peers.add((host, port))

    def broadcast(self, message: dict):
        """Рассылает сообщение всем известным пирам."""
        results = []
        for host, port in list(self.peers):
            try:
                results.append(self.send(host, port, message))
            except OSError:
                continue
        return results


if __name__ == "__main__":
    # Демонстрация: поднимаем узел и пингуем сами себя.
    server = P2PNode(port=5050)
    server.start()
    client = P2PNode()
    print("Ответ на ping:", client.send("127.0.0.1", 5050, {"type": "ping"}))
    server.stop()
