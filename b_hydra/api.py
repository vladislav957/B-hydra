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
    GET  /api/block/<index>      — блок по высоте (+ miner_message и его
                                   проверенный автор miner_message_author)
    GET  /api/tx/<txid>          — транзакция по идентификатору
    GET  /api/proof/<txid>       — доказательство включения (SPV audit-путь)
    GET  /api/address/<address>  — баланс и история транзакций адреса
    GET  /api/addresses[?limit=N]— обозреватель адресов: rich list цепочки
    GET  /api/mempool            — число неподтверждённых транзакций
    POST /api/transaction        — отправить ПОДПИСАННУЮ транзакцию (vin/vout)
    POST /api/send               — перевод {"private_key","to","amount","fee"}
                                   (узел подписывает сам — для своего локального узла)
    POST /api/mine               — добыть блок {"miner": "<address>",
                                   "message"?: "заметка майнера",
                                   "private_key"?: подписать заметку ключом}

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

from .blockchain import CHAIN_ID, MAX_SUPPLY
from . import icon
from .contract import ContractManager
from .node import BHydraNode
from .transaction import Transaction
from .wallet import is_valid_address, Wallet

DEFAULT_STATE = "bhydra_chain.json"
DEFAULT_DIFFICULTY = 3
# Файлы сертификата для --tls. Ключ сертификата — НЕ ключ кошелька: его утечка
# позволяет выдать себя за наш сервер, но не тронуть монеты.
DEFAULT_CERT = "bhydra_cert.pem"
DEFAULT_KEY = "bhydra_cert.key"
MAX_BODY_SIZE = 16 * 1024 * 1024   # анти-DoS: предел размера тела запроса (16 МБ)
# explorer.html и wallet.html лежат в корне репозитория (на уровень выше пакета).
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_EXPLORER_HTML = os.path.join(_ROOT, "explorer.html")
_WALLET_HTML = os.path.join(_ROOT, "wallet.html")
# Подпись транзакций в браузере: кошелёк грузит этот файл и подписывает сам,
# поэтому приватный ключ не уходит на узел.
_SIGN_JS = os.path.join(_ROOT, "bhydra-sign.js")
# Кошелёк ставится на телефон как приложение (PWA): манифест, сервис-воркер и
# иконки. Иконки не хранятся в репозитории — их рисует b_hydra/icon.py.
_QR_JS = os.path.join(_ROOT, "bhydra-qr.js")
_NET_JS = os.path.join(_ROOT, "bhydra-net.js")
_MANIFEST = os.path.join(_ROOT, "manifest.webmanifest")
_SERVICE_WORKER = os.path.join(_ROOT, "sw.js")


class BHydraAPI(BaseHTTPRequestHandler):
    """Обработчик REST-запросов к узлу B-hydra."""

    node = None             # общий BHydraNode (устанавливается в make_server)
    p2p = None              # P2PNode, если узел ещё и участник сети
    contracts = None        # общий ContractManager (эскроу и смарт-чеки)
    state_file = None
    contracts_file = None
    tls = False             # поднят ли сервер по HTTPS
    allow_key_endpoints = None   # None — решать по обстоятельствам
    lock = threading.Lock()

    # --- Вспомогательное -------------------------------------------------
    def _client_is_local(self) -> bool:
        host = (self.client_address or ("",))[0]
        return host in ("127.0.0.1", "::1", "::ffff:127.0.0.1", "localhost")

    def _keys_allowed(self) -> bool:
        """Можно ли на этом соединении принимать приватный ключ.

        `/api/send`, `/api/wallet` и контрактные POST принимают приватный ключ
        целиком — это описано как «для СВОЕГО локального узла». Но описание в
        документации ничего не запрещает: по открытому HTTP ключ уходил в сеть
        открытым текстом, и его читал любой на пути. Теперь правило проверяется
        кодом: по TLS — можно, без TLS — только с локального адреса, где
        перехватывать нечего. Явный `allow_key_endpoints` перекрывает оба
        случая (для тех, кто ставит свой обратный прокси с TLS).
        """
        if self.allow_key_endpoints is not None:
            return bool(self.allow_key_endpoints)
        return self.tls or self._client_is_local()

    def _refuse_key_endpoint(self) -> None:
        self._send(403, {"error": "приватный ключ по открытому каналу не "
                                  "принимается: включите TLS (--tls) или "
                                  "подписывайте транзакцию на устройстве "
                                  "(POST /api/transaction)"})

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

    def _send_file(self, path, content_type, no_store=False):
        """Отдаёт файл с диска. 404, если его нет."""
        try:
            with open(path, "rb") as handle:
                body = handle.read()
        except OSError:
            self._send(404, {"error": f"{os.path.basename(path)} не найден"})
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if no_store:
            # Сервис-воркер кэшировать нельзя: иначе обновление кошелька
            # застрянет у пользователя навсегда.
            self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _send_script(self, code, script):
        body = script.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/javascript; charset=utf-8")
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
            if parts == ["bhydra-sign.js"]:
                try:
                    with open(_SIGN_JS, encoding="utf-8") as fh:
                        self._send_script(200, fh.read())
                except OSError:
                    self._send_script(404, "// bhydra-sign.js not found")
                return
            if parts == ["bhydra-qr.js"]:
                try:
                    with open(_QR_JS, encoding="utf-8") as fh:
                        self._send_script(200, fh.read())
                except OSError:
                    self._send_script(404, "// bhydra-qr.js not found")
                return
            if parts == ["bhydra-net.js"]:
                try:
                    with open(_NET_JS, encoding="utf-8") as fh:
                        self._send_script(200, fh.read())
                except OSError:
                    self._send_script(404, "// bhydra-net.js not found")
                return
            if parts == ["manifest.webmanifest"]:
                self._send_file(_MANIFEST, "application/manifest+json")
                return
            if parts == ["sw.js"]:
                # Сервис-воркер обязан отдаваться из КОРНЯ: его область
                # действия не может быть шире каталога, из которого он отдан,
                # а кошелёк лежит на /wallet.
                self._send_file(_SERVICE_WORKER,
                                "application/javascript; charset=utf-8",
                                no_store=True)
                return
            if len(parts) == 1 and parts[0] in ("icon-192.png", "icon-512.png"):
                # Иконки не лежат в репозитории — они СЧИТАЮТСЯ (b_hydra/icon.py)
                # и создаются при первом запросе рядом с остальными ресурсами.
                icon.ensure_files(_ROOT)
                self._send_file(os.path.join(_ROOT, parts[0]), "image/png")
                return
            if parts == ["api", "block"] or (len(parts) == 3 and parts[:2] == ["api", "block"]):
                index = int(parts[2]) if len(parts) == 3 else -1
                block = self.node.get_block(index)
                if block:
                    # Заметка майнера — рядом с блоком, чтобы её не пришлось
                    # выковыривать из coinbase вручную.
                    block = dict(
                        block,
                        miner_message=self.node.block_message(index),
                        # Проверенный автор заметки или None: подпись
                        # необязательна, но если есть — она уже проверена.
                        miner_message_author=self.node.block_message_author(index),
                    )
                self._send(200 if block else 404,
                           block or {"error": "block not found"})
                return
            if len(parts) == 3 and parts[:2] == ["api", "tx"]:
                found = self.node.find_transaction(unquote(parts[2]))
                self._send(200 if found else 404,
                           found or {"error": "transaction not found"})
                return
            if len(parts) == 3 and parts[:2] == ["api", "proof"]:
                # Доказательство включения (SPV): audit-путь до merkle_root.
                proof = self.node.merkle_proof(unquote(parts[2]))
                self._send(200 if proof else 404,
                           proof or {"error": "transaction not found"})
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
                    # Суммарная работа и генезис — чтобы клиент мог выбрать
                    # лучший узел ПО ТОМУ ЖЕ правилу, что и сами узлы
                    # (replace_chain сравнивает работу, а не высоту), и не
                    # принять за свою сеть чужую с другим генезисом.
                    "total_work": bc.total_work,
                    "genesis": bc.chain[0].hash,
                    "chain_id": CHAIN_ID,
                    # Кто отвечает. Нужно кошельку, чтобы не счесть ОДИН узел,
                    # доступный по двум адресам (localhost и IP в сети), за два
                    # независимых: на числе независимых узлов держится SPV.
                    "node_id": getattr(self.p2p, "_node_id", None),
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
            elif parts == ["api", "nodes"]:
                # Список REST-адресов сети — «seed» для кошелька. Телефон
                # вводит ОДИН адрес, отсюда узнаёт остальные и дальше переживает
                # падение любого узла. Отпечаток сети рядом: клиент обязан
                # убедиться, что список пришёл из ЕГО сети, иначе он подцепит
                # чужую цепочку.
                bc = self.node.blockchain
                p2p = getattr(self, "p2p", None)
                if p2p is not None:
                    nodes = p2p.api_nodes()
                    peers = len(p2p.peer_list())
                else:
                    nodes, peers = [], None
                self._send(200, {
                    "nodes": nodes,
                    # Сколько соседей у САМОГО узла: ноль здесь означает, что
                    # он в сети один, и список из одного адреса — не ошибка.
                    "peers": peers,
                    "p2p": None if p2p is None else f"{p2p.host}:{p2p.port}",
                    "genesis": bc.chain[0].hash,
                    "chain_id": CHAIN_ID,
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
                if not self._keys_allowed():
                    self._refuse_key_endpoint()
                    return
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
                if not self._keys_allowed():
                    self._refuse_key_endpoint()
                    return
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
                if not self._keys_allowed():
                    self._refuse_key_endpoint()   # все контрактные POST с ключом
                    return
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
                note = data.get("message")
                # Ключ (необязательный) подписывает заметку — как и /api/send,
                # это путь ДЛЯ СВОЕГО узла: приватный ключ наружу не шлют.
                key = data.get("private_key")
                if key and not self._keys_allowed():
                    self._refuse_key_endpoint()   # без ключа майнить можно
                    return
                with self.lock:
                    try:
                        wallet = Wallet.from_private_hex(key) if key else None
                        block = self.node.mine_pending(miner, message=note,
                                                       wallet=wallet)
                    except ValueError as err:
                        self._send(400, {"error": str(err)})
                        return
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


def make_tls_context(certfile, keyfile):
    """Контекст TLS для сервера: TLS 1.2+ и никакого старья.

    Свой TLS мы НЕ пишем: для браузера нужен проверенный стек, и он есть в
    стандартной библиотеке. Собственный протокол `secure.py` — для канала
    узел↔узел, где обе стороны наши; браузеру он не годится.

    Минимум TLS 1.2: TLS 1.0/1.1 сломаны и выключены везде, а оставленная
    поддержка старой версии — это готовое понижение для активного атакующего.
    """
    import ssl

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(certfile, keyfile)
    return context


def make_server(host="0.0.0.0", port=8000, state_file=DEFAULT_STATE,
                difficulty=DEFAULT_DIFFICULTY, certfile=None, keyfile=None,
                allow_key_endpoints=None, p2p=None, node=None):
    """Создаёт сервер с загруженным (или новым) узлом B-hydra.

    `certfile`/`keyfile` включают HTTPS. `allow_key_endpoints` разрешает
    эндпоинты, принимающие приватный ключ, и по умолчанию выводится сам:
    с TLS — да, без TLS — только для локальных клиентов.

    `p2p` — уже созданный `P2PNode` поверх ТОГО ЖЕ узла: тогда REST-сервер не
    просто отвечает про свою цепочку, а является участником сети, и кошелёк,
    подключённый к нему, видит общую цепочку, а не изолированную копию.
    `node` — готовый узел (так его отдаёт GUI, где цепочка уже в памяти).
    """
    if p2p is not None:
        # Узел уже создан снаружи и живёт в сети — берём ЕГО цепочку, иначе
        # REST отвечал бы про свою копию, а сеть жила бы отдельно.
        node = p2p.node
    elif node is not None:
        pass                        # цепочку дали снаружи — ничего не грузим
    elif state_file and os.path.exists(state_file):
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
    BHydraAPI.p2p = p2p
    BHydraAPI.contracts = contracts
    BHydraAPI.state_file = state_file
    BHydraAPI.contracts_file = contracts_file
    BHydraAPI.tls = bool(certfile and keyfile)
    BHydraAPI.allow_key_endpoints = allow_key_endpoints
    server = ThreadingHTTPServer((host, port), BHydraAPI)
    if certfile and keyfile:
        server.socket = make_tls_context(certfile, keyfile).wrap_socket(
            server.socket, server_side=True)
    return server


def main():
    import argparse
    parser = argparse.ArgumentParser(description="B-hydra REST API сервер")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--file", default=DEFAULT_STATE)
    parser.add_argument("--tls", action="store_true",
                        help="включить HTTPS (сертификат создаётся сам, если нет)")
    parser.add_argument("--cert", default=DEFAULT_CERT,
                        help="файл сертификата (PEM)")
    parser.add_argument("--key", default=DEFAULT_KEY,
                        help="файл приватного ключа сертификата (PEM)")
    parser.add_argument("--allow-insecure-keys", action="store_true",
                        help="принимать приватный ключ и без TLS (не надо так)")
    parser.add_argument("--p2p", action="store_true",
                        help="войти в сеть B-hydra (иначе узел живёт сам по себе)")
    parser.add_argument("--p2p-port", type=int, default=5000,
                        help="порт узла в сети (по умолчанию 5000)")
    parser.add_argument("--seed", action="append", default=[],
                        help="seed-узел host:port (можно повторять)")
    parser.add_argument("--peers-file", default="bhydra_peers.json",
                        help="где хранить соседей между запусками")
    parser.add_argument("--no-discovery", action="store_true",
                        help="не искать соседей UDP-маяком в локальной сети")
    args = parser.parse_args()

    certfile = keyfile = None
    if args.tls:
        from . import certgen

        if certgen.ensure_files(args.cert, args.key):
            print(f"Создан самоподписанный сертификат: {args.cert}")
            print("  ⚠ браузер предупредит про неизвестный центр сертификации —")
            print("    для публичного узла возьмите настоящий (Let's Encrypt).")
        certfile, keyfile = args.cert, args.key

    p2p = None
    if args.p2p:
        from .p2p import P2PNode, local_ip, parse_seeds
        from .node import BHydraNode

        if args.file and os.path.exists(args.file):
            chain_node = BHydraNode.load(args.file)
        else:
            chain_node = BHydraNode()
        # Адрес для представления — тот, по которому нас достанут ДРУГИЕ
        # машины. 127.0.0.1 виден только этому компьютеру, и узел с таким
        # адресом в сети бесполезен: соседи не смогут подключиться обратно.
        p2p = P2PNode(local_ip(), args.p2p_port, node=chain_node,
                      peers_file=args.peers_file,
                      seeds=parse_seeds(args.seed) + parse_seeds(
                          os.environ.get("BHYDRA_SEEDS", "").split(",")),
                      api_port=args.port, api_tls=bool(certfile))

    server = make_server(args.host, args.port, args.file, certfile=certfile,
                         keyfile=keyfile,
                         allow_key_endpoints=True if args.allow_insecure_keys
                         else None, p2p=p2p)
    scheme = "https" if certfile else "http"
    print(f"B-hydra обозреватель: {scheme}://{args.host}:{args.port}/")
    print(f"REST API           : {scheme}://{args.host}:{args.port}/api/info")
    print(f"Состояние цепочки  : {args.file}   (Ctrl+C — стоп)")
    if p2p is not None:
        p2p.start()
        if not args.no_discovery:
            p2p.start_discovery()
        alive = p2p.bootstrap()
        print(f"Узел в сети        : {p2p.host}:{p2p.port}  "
              f"(соседей на старте: {alive})")
        print(f"Кошелькам дайте    : {scheme}://{p2p.host}:{args.port}"
              f"   — остальные узлы они узнают сами (/api/nodes)")
        if not args.seed and not alive:
            print("  ⚠ соседей нет. В локальной сети они найдутся сами по "
                  "UDP-маяку; через интернет нужен --seed host:5000")
    if not certfile:
        # Молча оставлять открытый канал нельзя: пользователь должен знать, что
        # приватный ключ по нему не примут (и почему).
        print("Канал ОТКРЫТ (без TLS): эндпоинты с приватным ключом доступны "
              "только с локального адреса. HTTPS — флаг --tls.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановка сервера…")
        server.shutdown()
    finally:
        if p2p is not None:
            p2p.stop()          # заодно сохранит соседей и их REST-адреса


if __name__ == "__main__":
    main()
