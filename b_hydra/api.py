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
    GET  /api/addresses[?limit=N]— обозреватель адресов: rich list цепочки
    GET  /api/mempool            — число неподтверждённых транзакций
    POST /api/transaction        — отправить ПОДПИСАННУЮ транзакцию (vin/vout)
    POST /api/send               — перевод {"private_key","to","amount","fee"}
                                   (узел подписывает сам — для своего локального узла)
    POST /api/mine               — добыть блок {"miner": "<address>"}

Смарт-контракты (средства реально блокируются на адресе контракта):
    GET  /api/contract                    — адрес контракта, эскроу и чеки
    GET  /api/contract/escrow/<id>        — эскроу-сделка по идентификатору
    GET  /api/contract/cheque/<id>        — смарт-чек по идентификатору
    POST /api/contract/escrow             — открыть {"private_key","seller",
                                            "amount","fee","deadline"?}
    POST /api/contract/escrow/confirm     — подтвердить {"escrow_id","private_key"}
    POST /api/contract/escrow/cancel      — отменить    {"escrow_id","private_key"}
    POST /api/contract/cheque             — выписать чек {"private_key","amount",
                                            "fee","expires_in"?,"recipient"?}
                                            → чек + секрет (выдаётся один раз)
    POST /api/contract/cheque/cash        — обналичить {"cheque_id","secret","to"}
    POST /api/contract/cheque/refund      — возврат по истёкшему чеку
                                            {"cheque_id","private_key"}

Запуск:
    python api.py            # http://0.0.0.0:8000  (обозреватель + API)
"""

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, unquote

if __name__ == "__main__" and __package__ in (None, ""):
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    __package__ = "b_hydra"

from .blockchain import MAX_SUPPLY
from .contract import ContractManager
from .node import BHydraNode
from .transaction import Transaction
from .wallet import is_valid_address, Wallet

DEFAULT_STATE = "bhydra_chain.json"
DEFAULT_DIFFICULTY = 3
MAX_BODY_SIZE = 16 * 1024 * 1024   # анти-DoS: предел размера тела запроса (16 МБ)
# explorer.html и wallet.html лежат в корне репозитория (на уровень выше пакета).
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_EXPLORER_HTML = os.path.join(_ROOT, "explorer.html")
_WALLET_HTML = os.path.join(_ROOT, "wallet.html")


class BHydraAPI(BaseHTTPRequestHandler):
    """Обработчик REST-запросов к узлу B-hydra."""

    node = None             # общий BHydraNode (устанавливается в make_server)
    contracts = None        # общий ContractManager (эскроу и смарт-чеки)
    state_file = None
    contracts_file = None
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
        if self.contracts_file and self.contracts is not None:
            with open(self.contracts_file, "w", encoding="utf-8") as f:
                json.dump(self.contracts.to_dict(), f,
                          ensure_ascii=False, indent=2)

    def _wallet_from(self, data):
        """Кошелёк из private_key тела запроса (модель доверия — как /api/send)."""
        pk = data.get("private_key")
        if not pk:
            raise ValueError("нужен приватный ключ (private_key)")
        return Wallet.from_private_hex(pk)

    def _handle_contract_post(self, action, data):
        """POST-операции смарт-контрактов; ValueError → понятный ответ 400."""
        def _num(name, default=None, required=False):
            raw = data.get(name, default)
            if raw is None:
                if required:
                    raise ValueError(f"нужно числовое поле '{name}'")
                return None
            try:
                return float(raw)
            except (TypeError, ValueError):
                raise ValueError(f"поле '{name}' должно быть числом")

        if action == ["escrow"]:
            buyer = self._wallet_from(data)
            with self.lock:
                escrow = self.contracts.open_escrow(
                    buyer, data.get("seller"), _num("amount", required=True),
                    fee=_num("fee", 0.0), deadline=_num("deadline"))
                self._save()
            self._send(200, escrow)
        elif action == ["escrow", "confirm"]:
            party = self._wallet_from(data).address
            with self.lock:
                escrow = self.contracts.confirm_escrow(
                    data.get("escrow_id"), party)
                self._save()
            self._send(200, escrow)
        elif action == ["escrow", "cancel"]:
            party = self._wallet_from(data).address
            with self.lock:
                escrow = self.contracts.cancel_escrow(
                    data.get("escrow_id"), party)
                self._save()
            self._send(200, escrow)
        elif action == ["cheque"]:
            payer = self._wallet_from(data)
            with self.lock:
                cheque, secret = self.contracts.write_cheque(
                    payer, _num("amount", required=True), fee=_num("fee", 0.0),
                    expires_in=_num("expires_in", 86400.0),
                    recipient=data.get("recipient"))
                self._save()
            # Секрет отдаётся ровно один раз — узел хранит только его хеш.
            self._send(200, {**cheque, "secret": secret})
        elif action == ["cheque", "cash"]:
            with self.lock:
                cheque = self.contracts.cash_cheque(
                    data.get("cheque_id"), data.get("secret"), data.get("to"))
                self._save()
            self._send(200, cheque)
        elif action == ["cheque", "refund"]:
            payer = self._wallet_from(data).address
            with self.lock:
                cheque = self.contracts.refund_cheque(
                    data.get("cheque_id"), payer)
                self._save()
            self._send(200, cheque)
        else:
            self._send(404, {"error": "not found"})

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
            if parts in (["wallet"], ["wallet.html"]):
                try:
                    with open(_WALLET_HTML, encoding="utf-8") as fh:
                        self._send_html(200, fh.read())
                except OSError:
                    self._send_html(404, "<h1>wallet.html not found</h1>")
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
                from .blockchain import TARGET_BLOCK_TIME
                bc = self.node.blockchain
                self._send(200, {
                    "network": "B-hydra",
                    "height": len(bc.chain),
                    "difficulty": bc.last_block.difficulty,
                    "block_work": bc.last_block.work,
                    "target_block_time_min": round(TARGET_BLOCK_TIME / 60, 1),
                    "retarget_interval": bc.retarget_interval,
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
            elif parts == ["api", "addresses"]:
                # Обозреватель адресов: rich list всех адресов цепочки.
                query = dict(p.split("=", 1) for p in
                             urlparse(self.path).query.split("&") if "=" in p)
                try:
                    limit = max(1, min(int(query.get("limit", 100)), 1000))
                except ValueError:
                    limit = 100
                ranked = self.node.address_stats()
                supply = self.node.blockchain.total_supply
                self._send(200, {
                    "count": len(ranked),
                    "total_supply": supply,
                    "addresses": ranked[:limit],
                })
            elif parts == ["api", "contract"]:
                self._send(200, {
                    "address": self.contracts.address,
                    "balance": self.node.get_balance(self.contracts.address),
                    "escrows": list(self.contracts.escrows.values()),
                    "cheques": list(self.contracts.cheques.values()),
                })
            elif len(parts) == 4 and parts[:3] == ["api", "contract", "escrow"]:
                escrow = self.contracts.escrows.get(unquote(parts[3]))
                self._send(200 if escrow else 404,
                           escrow or {"error": "эскроу не найден"})
            elif len(parts) == 4 and parts[:3] == ["api", "contract", "cheque"]:
                cheque = self.contracts.cheques.get(unquote(parts[3]))
                self._send(200 if cheque else 404,
                           cheque or {"error": "чек не найден"})
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
            elif parts == ["api", "wallet"]:
                # По приватному ключу вернуть его АДРЕС + баланс/историю, чтобы
                # кошелёк показал реальные данные после импорта ключа.
                # (Ключ уходит на узел — как и в /api/send; для своего узла.)
                pk = data.get("private_key")
                if not pk:
                    self._send(400, {"error": "нужен приватный ключ (private_key)"})
                    return
                try:
                    w = Wallet.from_private_hex(pk)
                except ValueError as err:
                    self._send(400, {"error": str(err)})
                    return
                self._send(200, {
                    "address": w.address,
                    "public_key": w.public_key_hex,
                    "balance": self.node.get_balance(w.address),
                    "history": self.node.address_history(w.address),
                })
            elif parts == ["api", "send"]:
                # Перевод на другой адрес: узел подписывает транзакцию ключом
                # отправителя и кладёт в мемпул. Возвращает ЧЁТКУЮ причину отказа
                # (неверный адрес / сумма / нехватка средств), а не общий отказ.
                # ВНИМАНИЕ: приватный ключ уходит на узел — годится для СВОЕГО
                # локального узла; для чужого узла подписывайте на устройстве.
                pk = data.get("private_key")
                to = data.get("to")
                if not pk:
                    self._send(400, {"error": "нужен приватный ключ (private_key)"})
                    return
                try:
                    sender = Wallet.from_private_hex(pk)
                except ValueError as err:
                    self._send(400, {"error": str(err)})
                    return
                if not is_valid_address(to):
                    self._send(400, {"error": "неверный адрес получателя (BHY…)"})
                    return
                try:
                    amount = float(data.get("amount"))
                    fee = float(data.get("fee", 0.0))
                except (TypeError, ValueError):
                    self._send(400, {"error": "сумма и комиссия должны быть числом"})
                    return
                if amount <= 0:
                    self._send(400, {"error": "сумма должна быть больше нуля"})
                    return
                if fee < 0:
                    self._send(400, {"error": "комиссия не может быть отрицательной"})
                    return
                balance = self.node.get_balance(sender.address)
                if amount + fee > balance + 1e-9:
                    self._send(400, {"error": (
                        f"недостаточно средств: нужно {amount + fee:.4f} BHY, "
                        f"доступно {balance:.4f} BHY")})
                    return
                with self.lock:
                    tx = self.node.create_transaction(sender, to, amount, fee)
                    if tx is None:
                        self._send(400, {"error": "не удалось собрать транзакцию из UTXO"})
                        return
                    accepted = self.node.add_transaction(tx)
                    if accepted:
                        self._save()
                self._send(200 if accepted else 400, {
                    "accepted": accepted,
                    "txid": tx.txid,
                    "from": sender.address,
                    "to": to,
                    "amount": amount,
                    "fee": fee,
                    "sender_balance": self.node.get_balance(sender.address),
                    "error": None if accepted else "транзакция отклонена (двойная трата?)",
                })
            elif len(parts) >= 3 and parts[:2] == ["api", "contract"]:
                # Смарт-контракты: понятная ошибка (400) вместо общего отказа.
                try:
                    self._handle_contract_post(parts[2:], data)
                except ValueError as err:
                    self._send(400, {"error": str(err)})
            elif parts == ["api", "mine"]:
                miner = data.get("miner")
                if not miner:
                    self._send(400, {"error": "field 'miner' is required"})
                    return
                if not is_valid_address(miner):
                    self._send(400, {"error": "invalid miner address"})
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
    # Смарт-контракты (эскроу, чеки) — в отдельном файле рядом с цепочкой:
    # там лежит приватный ключ контрактного кошелька, терять его нельзя.
    contracts_file = state_file + ".contracts" if state_file else None
    if contracts_file and os.path.exists(contracts_file):
        with open(contracts_file, encoding="utf-8") as f:
            contracts = ContractManager.from_dict(node, json.load(f))
    else:
        contracts = ContractManager(node)
    BHydraAPI.node = node
    BHydraAPI.contracts = contracts
    BHydraAPI.state_file = state_file
    BHydraAPI.contracts_file = contracts_file
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
