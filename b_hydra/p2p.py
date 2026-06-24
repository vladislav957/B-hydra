"""
P2P.py — одноранговая синхронизация узлов B-hydra.

Каждый узел поднимает TCP-сервер и общается с другими узлами JSON-сообщениями:
обменивается списком пиров, рассылает новые блоки и транзакции и подтягивает
у соседей самую длинную валидную цепочку (правило консенсуса «longest valid
chain»). Логика блокчейна — в Node.py (BHydraNode); здесь транспорт и протокол.

Демонстрация (три узла локально):
    python P2P.py
"""

import json
import socket
import threading

if __name__ == "__main__" and __package__ in (None, ""):
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    __package__ = "b_hydra"

from .tcp import recv_message, send_message
from .node import BHydraNode


class P2PNode:
    """Сетевой узел B-hydra: TCP-сервер + клиент + синхронизация."""

    def __init__(self, host="127.0.0.1", port=5000, node=None):
        self.host = host
        self.port = port
        self.node = node if node is not None else BHydraNode()
        self.peers = set()          # известные пиры: множество (host, port)
        self.seen_tx = set()        # txid уже виденных транзакций (анти-петля)
        self.seen_blocks = set()    # хеши уже виденных блоков (анти-петля)
        self._seen_lock = threading.Lock()
        self._server = None
        self._running = False

    # --- Протокол --------------------------------------------------------
    def _handle_message(self, raw: bytes) -> bytes:
        try:
            message = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return self._json({"type": "error", "error": "bad json"})

        mtype = message.get("type")

        if mtype == "ping":
            return self._json({"type": "pong"})

        if mtype == "hello":
            host, port = message.get("host"), message.get("port")
            if host and port:
                self.add_peer(host, port)
            return self._json({"type": "peers",
                               "peers": [list(p) for p in self.peers]})

        if mtype == "get_peers":
            return self._json({"type": "peers",
                               "peers": [list(p) for p in self.peers]})

        if mtype == "get_height":
            return self._json({"type": "height", "height": self.node.height})

        if mtype == "get_chain":
            return self._json({
                "type": "chain",
                "chain": self.node.blockchain.to_dicts(),
                "base_difficulty": self.node.blockchain.difficulty,
            })

        if mtype == "transaction":
            from .transaction import Transaction
            tx = Transaction.from_dict(message["transaction"])
            with self._seen_lock:
                first_seen = tx.txid not in self.seen_tx
                self.seen_tx.add(tx.txid)
            accepted = self.node.add_transaction(tx)
            if accepted and first_seen:
                # Gossip: пересылаем транзакцию дальше (кроме отправителя).
                origin = tuple(message["from"]) if message.get("from") else None
                self._gossip({"type": "transaction",
                              "transaction": message["transaction"],
                              "from": [self.host, self.port]},
                             exclude=origin, background=True)
            return self._json({"type": "ack", "accepted": accepted})

        if mtype == "block":
            block_dict = message["block"]
            bhash = block_dict.get("hash")
            with self._seen_lock:
                if bhash in self.seen_blocks:
                    return self._json({"type": "ack", "accepted": False,
                                       "height": self.node.height})
                self.seen_blocks.add(bhash)
            accepted = self.node.receive_block(block_dict)
            origin = tuple(message["from"]) if message.get("from") else None
            if accepted:
                # Gossip: пересылаем блок дальше по сети (кроме отправителя).
                self._gossip({"type": "block", "block": block_dict,
                              "from": [self.host, self.port]},
                             exclude=origin, background=True)
            elif origin:
                # Возможно, мы отстали — подтянуть цепочку у отправителя.
                self._sync_from(origin)
            return self._json({"type": "ack", "accepted": accepted,
                               "height": self.node.height})

        return self._json({"type": "ack"})

    @staticmethod
    def _json(obj):
        return json.dumps(obj).encode("utf-8")

    # --- Сервер ----------------------------------------------------------
    def _serve(self):
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind((self.host, self.port))
        self._server.listen(8)
        self._running = True
        while self._running:
            try:
                conn, _addr = self._server.accept()
            except OSError:
                break
            # Каждое соединение обслуживаем в отдельном потоке, чтобы узел мог
            # синхронизироваться, пока обрабатывает входящее сообщение.
            threading.Thread(target=self._handle_conn, args=(conn,),
                             daemon=True).start()

    def _handle_conn(self, conn):
        with conn:
            raw = recv_message(conn)
            if raw:
                send_message(conn, self._handle_message(raw))

    def start(self):
        thread = threading.Thread(target=self._serve, daemon=True)
        thread.start()
        return thread

    def stop(self):
        self._running = False
        if self._server is not None:
            self._server.close()

    # --- Клиент ----------------------------------------------------------
    def send(self, host, port, message: dict) -> dict:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
            client.settimeout(5)
            client.connect((host, port))
            send_message(client, self._json(message))
            raw = recv_message(client)
        return json.loads(raw.decode("utf-8")) if raw else {}

    def add_peer(self, host, port):
        if (host, port) != (self.host, self.port):
            self.peers.add((host, port))

    def connect(self, host, port):
        """Подключается к узлу, обменивается пирами и синхронизируется.

        Узел не только узнаёт пиров соседа, но и представляется им (hello),
        чтобы сеть превращалась в связный меш, а не звезду.
        """
        resp = self.send(host, port, {"type": "hello",
                                      "host": self.host, "port": self.port})
        self.add_peer(host, port)
        for h, p in resp.get("peers", []):
            if (h, p) == (self.host, self.port) or (h, p) in self.peers:
                continue
            self.add_peer(h, p)
            try:                       # представиться новому пиру
                self.send(h, p, {"type": "hello",
                                 "host": self.host, "port": self.port})
            except OSError:
                continue
        self.sync()

    def broadcast(self, message: dict):
        results = []
        for host, port in list(self.peers):
            try:
                results.append(self.send(host, port, message))
            except OSError:
                continue
        return results

    def _gossip(self, message: dict, exclude=None, background: bool = False):
        """Пересылает сообщение всем пирам, кроме `exclude`.

        В обработчике входящих сообщений используется background=True, чтобы
        пересылка шла в отдельном потоке и не блокировала ответ (и не
        приводила к взаимоблокировке в кольцевых топологиях).
        """
        def _run():
            for host, port in list(self.peers):
                if (host, port) == exclude:
                    continue
                try:
                    self.send(host, port, message)
                except OSError:
                    continue

        if background:
            threading.Thread(target=_run, daemon=True).start()
        else:
            _run()

    # --- Высокоуровневые операции ----------------------------------------
    def submit_transaction(self, tx) -> bool:
        """Добавляет транзакцию локально и распространяет её по сети."""
        accepted = self.node.add_transaction(tx)
        if accepted:
            with self._seen_lock:
                self.seen_tx.add(tx.txid)
            self._gossip({"type": "transaction", "transaction": tx.to_dict(),
                          "from": [self.host, self.port]})
        return accepted

    def mine(self, miner_address):
        """Майнит блок и распространяет его по сети."""
        block = self.node.mine_pending(miner_address)
        with self._seen_lock:
            self.seen_blocks.add(block.hash)
        self._gossip({"type": "block", "block": block.to_dict(),
                      "from": [self.host, self.port]})
        return block

    def sync(self) -> bool:
        """Находит пира с самой длинной цепочкой и подтягивает её."""
        best_peer, best_height = None, self.node.height
        for peer in list(self.peers):
            try:
                resp = self.send(peer[0], peer[1], {"type": "get_height"})
            except OSError:
                continue
            if resp.get("height", 0) > best_height:
                best_height, best_peer = resp["height"], peer
        return self._sync_from(best_peer) if best_peer else False

    def _sync_from(self, peer) -> bool:
        try:
            resp = self.send(peer[0], peer[1], {"type": "get_chain"})
        except OSError:
            return False
        return self.node.replace_chain(resp.get("chain", []))


def _demo():
    import time
    from .wallet import generate_wallet

    # Линейная топология: A — B — C. Узел C НЕ соединён с A напрямую,
    # блок должен дойти до него «через» B (multi-hop gossip).
    a = P2PNode("127.0.0.1", 5101, BHydraNode(difficulty=2))
    b = P2PNode("127.0.0.1", 5102, BHydraNode(difficulty=2))
    c = P2PNode("127.0.0.1", 5103, BHydraNode(difficulty=2))
    for n in (a, b, c):
        n.start()
    time.sleep(0.3)

    a.add_peer("127.0.0.1", 5102)          # A знает B
    b.add_peer("127.0.0.1", 5101)          # B знает A
    b.add_peer("127.0.0.1", 5103)          # B знает C
    c.add_peer("127.0.0.1", 5102)          # C знает B  (A и C — НЕ соседи)

    miner = generate_wallet()
    print("Топология: A — B — C (C не соединён с A напрямую)")
    print("Узел A майнит 3 блока…")
    for _ in range(3):
        a.mine(miner.address)

    # Ждём, пока gossip разнесёт блоки по сети.
    deadline = time.time() + 5
    while time.time() < deadline and not (
            b.node.height == a.node.height and c.node.height == a.node.height):
        time.sleep(0.1)

    print(f"\nВысота A: {a.node.height}")
    print(f"Высота B: {b.node.height}  (получил напрямую от A)")
    print(f"Высота C: {c.node.height}  (получил ЧЕРЕЗ B — multi-hop!)")
    print(f"Вершины совпадают у всех: "
          f"{a.node.blockchain.last_block.hash == b.node.blockchain.last_block.hash == c.node.blockchain.last_block.hash}")

    for n in (a, b, c):
        n.stop()


def main():
    import argparse
    import time

    parser = argparse.ArgumentParser(description="P2P-узел B-hydra")
    parser.add_argument("--port", type=int, help="порт узла (если не задан — демо)")
    parser.add_argument("--peer", help="узел для подключения, формат host:port")
    parser.add_argument("--difficulty", type=int, default=3, help="базовая сложность")
    parser.add_argument("--demo", action="store_true", help="запустить демо из 3 узлов")
    args = parser.parse_args()

    if args.demo or not args.port:
        _demo()
        return

    node = P2PNode("0.0.0.0", args.port, BHydraNode(difficulty=args.difficulty))
    node.start()
    if args.peer:
        host, port = args.peer.split(":")
        node.connect(host, int(port))
    print(f"P2P-узел B-hydra на :{args.port} | пиров: {len(node.peers)} "
          f"| высота: {node.node.height}  (Ctrl+C — стоп)")
    try:
        while True:                 # периодически подтягиваем цепочку у пиров
            time.sleep(5)
            node.sync()
    except KeyboardInterrupt:
        node.stop()


if __name__ == "__main__":
    main()
