"""
api.py — REST API узла B-hydra (для мобильных кошельков).

Сервер на стандартной библиотеке (http.server, без зависимостей). Он НЕ хранит
и НЕ запрашивает приватные ключи: подпись транзакции выполняется на устройстве
(на телефоне), а на сервер приходит уже подписанная транзакция.

Эндпоинты:
    GET  /                       — веб-обозреватель блоков (HTML)
    GET  /api/info               — параметры сети и высота цепочки
    GET  /api/balance/<address>  — баланс адреса (сумма UTXO)
    GET  /api/utxos/<address>    — непотраченные выходы адреса (для входов)
    GET  /api/chain              — вся цепочка блоков
    GET  /api/block/<index>      — блок по высоте
    GET  /api/tx/<txid>          — транзакция по идентификатору
    GET  /api/address/<address>  — баланс и история транзакций адреса
    GET  /api/mempool            — число неподтверждённых транзакций
    POST /api/transaction        — отправить ПОДПИСАННУЮ транзакцию (vin/vout)
    POST /api/mine               — добыть блок {"miner": "<address>"}

Запуск:
    python api.py            # http://0.0.0.0:8000  (обозреватель + API)
"""

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, unquote

from .blockchain import MAX_SUPPLY
from .node import BHydraNode
from .transaction import Transaction

DEFAULT_STATE = "bhydra_chain.json"
DEFAULT_DIFFICULTY = 3
MAX_BODY_SIZE = 16 * 1024 * 1024   # анти-DoS: предел размера тела запроса (16 МБ)
# explorer.html лежит в корне репозитория (на уровень выше пакета).
_EXPLORER_HTML = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "explorer.html"
)


class BHydraAPI(BaseHTTPRequestHandler):
    """Обработчик REST-запросов к узлу B-hydra."""

    node = None             # общий BHydraNode (устанавливается в make_server)
    state_file = None
    lock = threading.Lock()

    # --- Вспомогательное -------------------------------------------------
    def _send(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        if not length:
            return {}
        if length > MAX_BODY_SIZE:
            raise ValueError("request body too large")  # анти-DoS
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def _send_html(self, code, html):
        body = html.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _save(self):
        if self.state_file:
            self.node.save(self.state_file)

    def log_message(self, *args):
        pass  # тихий режим

    def do_OPTIONS(self):
        # CORS preflight для запросов из браузера/приложения.
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    # --- GET -------------------------------------------------------------
    def do_GET(self):
        parts = [p for p in urlparse(self.path).path.strip("/").split("/") if p]
        try:
            # Обозреватель блоков (веб-страница).
            if parts in ([], ["explorer"], ["index.html"]):
                try:
                    with open(_EXPLORER_HTML, encoding="utf-8") as fh:
                        self._send_html(200, fh.read())
                except OSError:
                    self._send_html(404, "<h1>explorer.html not found</h1>")
                return
            if parts == ["api", "block"] or (len(parts) == 3 and parts[:2] == ["api", "block"]):
                index = int(parts[2]) if len(parts) == 3 else -1
                block = self.node.get_block(index)
                self._send(200 if block else 404,
                           block or {"error": "block not found"})
                return
            if len(parts) == 3 and parts[:2] == ["api", "tx"]:
                found = self.node.find_transaction(unquote(parts[2]))
                self._send(200 if found else 404,
                           found or {"error": "transaction not found"})
                return
            if len(parts) == 3 and parts[:2] == ["api", "address"]:
                addr = unquote(parts[2])
                self._send(200, {
                    "address": addr,
                    "balance": self.node.get_balance(addr),
                    "history": self.node.address_history(addr),
                })
                return

            if parts == ["api", "info"]:
                from .economics import mining_end_year
                bc = self.node.blockchain
                self._send(200, {
                    "network": "B-hydra",
                    "height": len(bc.chain),
                    "base_difficulty": bc.difficulty,
                    "difficulty": bc.expected_difficulty(len(bc.chain)),
                    "participants": len(bc.distinct_miners()),
                    "next_block_reward": bc.block_reward(len(bc.chain)),
                    "max_supply": MAX_SUPPLY,
                    "mining_end_year": round(mining_end_year()),
                    "hash_algorithm": "SHA-512",
                    "model": "UTXO",
                })
            elif len(parts) == 3 and parts[:2] == ["api", "balance"]:
                addr = parts[2]
                self._send(200, {"address": addr,
                                 "balance": self.node.get_balance(addr)})
            elif len(parts) == 3 and parts[:2] == ["api", "utxos"]:
                addr = parts[2]
                utxos = [{"txid": op[0], "index": op[1], "amount": amount}
                         for op, amount in self.node.find_spendable(addr)]
                self._send(200, {"address": addr, "utxos": utxos,
                                 "total": sum(u["amount"] for u in utxos)})
            elif parts == ["api", "chain"]:
                self._send(200, {"height": len(self.node.blockchain.chain),
                                 "chain": self.node.blockchain.to_dicts()})
            elif parts == ["api", "mempool"]:
                self._send(200, {"pending": len(self.node.mempool)})
            else:
                self._send(404, {"error": "not found"})
        except Exception as exc:  # noqa: BLE001 — вернуть ошибку клиенту
            self._send(500, {"error": str(exc)})

    # --- POST ------------------------------------------------------------
    def do_POST(self):
        parts = [p for p in urlparse(self.path).path.strip("/").split("/") if p]
        try:
            data = self._read_json()
            if parts == ["api", "transaction"]:
                tx = Transaction.from_dict(data)
                with self.lock:
                    accepted = self.node.add_transaction(tx)
                    if accepted:
                        self._save()
                self._send(200 if accepted else 400,
                           {"accepted": accepted, "txid": tx.txid})
            elif parts == ["api", "mine"]:
                miner = data.get("miner")
                if not miner:
                    self._send(400, {"error": "field 'miner' is required"})
                    return
                with self.lock:
                    block = self.node.mine_pending(miner)
                    self._save()
                self._send(200, {
                    "index": block.index,
                    "hash": block.hash,
                    "difficulty": block.difficulty,
                    "nonce": block.nonce,
                    "attempts": getattr(block, "mining_attempts", None),
                    "transactions": len(block.data) if isinstance(block.data, list) else 1,
                })
            else:
                self._send(404, {"error": "not found"})
        except Exception as exc:  # noqa: BLE001
            self._send(500, {"error": str(exc)})


def make_server(host="0.0.0.0", port=8000, state_file=DEFAULT_STATE,
                difficulty=DEFAULT_DIFFICULTY):
    """Создаёт HTTP-сервер с загруженным (или новым) узлом B-hydra."""
    if state_file and os.path.exists(state_file):
        node = BHydraNode.load(state_file)
    else:
        node = BHydraNode(difficulty=difficulty)
    BHydraAPI.node = node
    BHydraAPI.state_file = state_file
    return ThreadingHTTPServer((host, port), BHydraAPI)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="B-hydra REST API сервер")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--file", default=DEFAULT_STATE)
    args = parser.parse_args()

    server = make_server(args.host, args.port, args.file)
    print(f"B-hydra обозреватель: http://{args.host}:{args.port}/")
    print(f"REST API           : http://{args.host}:{args.port}/api/info")
    print(f"Состояние цепочки  : {args.file}   (Ctrl+C — стоп)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановка сервера…")
        server.shutdown()


if __name__ == "__main__":
    main()
