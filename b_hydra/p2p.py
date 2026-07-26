"""
P2P.py — одноранговая синхронизация узлов B-hydra.

Каждый узел поднимает TCP-сервер и общается с другими узлами JSON-сообщениями:
обменивается списком пиров, рассылает новые блоки и транзакции и подтягивает
у соседей самую ТРУДНУЮ валидную цепочку (консенсус по суммарной работе, а не
по длине). Логика блокчейна — в Node.py (BHydraNode); здесь транспорт и протокол.

Демонстрация (три узла локально):
    python P2P.py
"""

import json
import os
import secrets
import socket
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed

# Предел числа запомненных txid/хешей блоков для анти-петли gossip. Без него
# множества seen_* росли бы без границ (утечка памяти и мягкий DoS: атакующий
# шлёт много уникальных txid). 10 000 последних — с запасом хватает, чтобы не
# пересылать недавно виденное повторно.
SEEN_LIMIT = 10_000

# --- Защита сетевого слоя от недружелюбного пира ---------------------------
# Таблица пиров и обход её — главная точка приложения сил атакующего: раздуть
# её ничего не стоит (адреса не проверяются и ничего не стоят), а обход с
# таймаутом на каждого превращает это в паралич узла.
MAX_PEERS = 256                  # потолок таблицы пиров
MAX_PEERS_PER_MESSAGE = 32       # сколько адресов принимаем/отдаём за один раз
PEER_TIMEOUT = 2.0               # таймаут исходящего запроса к пиру, с
INBOUND_TIMEOUT = 10.0           # таймаут чтения входящего сообщения, с
MAX_INBOUND_CONNECTIONS = 64     # одновременно обслуживаемых входящих соединений
FANOUT_WORKERS = 32              # параллелизм рассылки по пирам

# Повтор чужого блока — повод попробовать догнать цепочку, но не чаще, чем
# раз в SYNC_RETRY_INTERVAL: иначе пир гонял бы нас за полной цепочкой
# сколько угодно часто.
SYNC_RETRY_INTERVAL = 2.0        # секунд между докачками цепочки по чужому блоку

# Цепочка передаётся ПАЧКАМИ. Раньше на запрос отдавалась вся цепочка одним
# сообщением, а на сообщение стоит лимит 32 МБ — то есть у сети был жёсткий
# потолок длины (~30 тыс. блоков с одним coinbase, ~10 тыс. с транзакциями),
# после которого новый узел не смог бы синхронизироваться в принципе. Плюс
# догнать один блок стоило скачивания и полной перепроверки всей цепочки.
MAX_BLOCKS_PER_MESSAGE = 500     # блоков в одной пачке
MAX_SYNC_BLOCKS = 100_000        # потолок докачки за одну синхронизацию
SYNC_PEER_ATTEMPTS = 4           # сколько кандидатов пробуем за одну sync()

# Узел должен переживать перезапуск. Таблица пиров жила только в памяти, а
# UDP-маяк работает лишь в пределах одной локальной сети (широковещание за
# роутер не уходит) — поэтому узел в интернете после рестарта оставался ОДИН и
# требовал ручного --peer. Теперь соседи сохраняются на диск, а для самого
# первого запуска (файла ещё нет) есть seed-узлы — как DNS seeds в Bitcoin.
DEFAULT_PEERS_FILE = "bhydra_peers.json"
DEFAULT_SEEDS = ()               # адреса вида ("host", port); задаются сетью

# --- Репутация пиров --------------------------------------------------------
# Лимиты выше ограничивают УЩЕРБ от плохого соседа, но не отсекают источник:
# пир, который шлёт мусор или заведомо негодные блоки, оставался в таблице, и
# мы продолжали слать ему gossip и спрашивать у него цепочку. Теперь за
# нарушения начисляются штрафные очки, а при переполнении адрес временно
# банится. Бан идёт по ХОСТУ: у входящего соединения виден только его IP, а
# порт там эфемерный и ни о чём не говорит.
BAN_SCORE = 100                  # порог, после которого адрес банится
BAN_DURATION = 3600.0            # на сколько секунд
SCORE_RESET_AFTER = 600.0        # одиночная оплошность прощается через 10 мин
PENALTY_BAD_MESSAGE = 25         # неразбираемое или битое сообщение
PENALTY_INVALID_BLOCK = 50       # заведомо негодный блок (не «мы отстали»)
PENALTY_FOREIGN_NETWORK = 20     # представился из чужой сети
PENALTY_GARBAGE_PEERS = 10       # мусор вместо адресов в списке пиров


class _BoundedSet:
    """Множество с пределом размера и FIFO-вытеснением (только stdlib).

    Хранит последние `max_size` добавленных элементов: при переполнении
    выталкивается самый старый. Поддерживает то же, что нужно для анти-петли:
    проверку `in` и `add`. Потокобезопасность обеспечивает вызывающий код
    (доступ к seen_* идёт под self._seen_lock), здесь блокировок нет.
    """

    def __init__(self, max_size: int = SEEN_LIMIT):
        self._max = max(1, int(max_size))
        self._data = OrderedDict()      # элемент → None, порядок = порядок добавления

    def __contains__(self, item) -> bool:
        return item in self._data

    def add(self, item) -> None:
        # Новый элемент дописывается в конец; повторное добавление существующего
        # не меняет его место (честный FIFO по первому появлению).
        if item not in self._data:
            self._data[item] = None
            if len(self._data) > self._max:
                self._data.popitem(last=False)   # вытолкнуть самый старый

    def __len__(self) -> int:
        return len(self._data)

# Авто-поиск узлов в локальной сети (WiFi/LAN) без интернета и без ввода IP:
# каждый узел рассылает короткий UDP-«маяк» в широковещание, а услышав чужой
# маяк — подключается. Работает на одном роутере/точке доступа даже офлайн.
DISCOVERY_PORT = 5999
DISCOVERY_MAGIC = "b-hydra-discovery-v1"

if __name__ == "__main__" and __package__ in (None, ""):
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    __package__ = "b_hydra"

from .tcp import recv_message, send_message
from .blockchain import CHAIN_ID
from .node import BHydraNode


class P2PNode:
    """Сетевой узел B-hydra: TCP-сервер + клиент + синхронизация."""

    def __init__(self, host="127.0.0.1", port=5000, node=None,
                 seen_limit=SEEN_LIMIT, max_peers=MAX_PEERS,
                 peers_file=None, seeds=None):
        self.host = host
        self.port = port
        self.node = node if node is not None else BHydraNode()
        self.peers = set()          # известные пиры: множество (host, port)
        self.max_peers = max(1, int(max_peers))
        # Куда сохранять соседей между запусками и с чего начинать, если файла
        # ещё нет. Без пути сохранение выключено (так работают тесты и демо).
        self.peers_file = peers_file
        self.seeds = [(str(h), int(p)) for h, p in (seeds or DEFAULT_SEEDS)]
        # Таймауты — атрибуты узла, а не константы: их удобно ужимать в тестах
        # и подкручивать под медленную сеть.
        self.peer_timeout = PEER_TIMEOUT
        self.inbound_timeout = INBOUND_TIMEOUT
        self.sync_retry_interval = SYNC_RETRY_INTERVAL
        self.max_blocks_per_message = MAX_BLOCKS_PER_MESSAGE
        self.max_sync_blocks = MAX_SYNC_BLOCKS
        self._sync_lock = threading.Lock()
        self._last_block_sync = 0.0     # когда последний раз тянули цепочку
        # Репутация: штрафные очки и сроки банов по хостам.
        self._ban_threshold = BAN_SCORE
        self.ban_duration = BAN_DURATION
        self.score_reset_after = SCORE_RESET_AFTER
        # Локальные адреса по умолчанию НЕ банятся: в реальной сети их не
        # бывает, а демо, тесты и локальная разработка живут на loopback, где
        # один банный список положил бы сразу все узлы машины.
        self.ban_loopback = False
        self._scores = {}               # хост → (очки, когда начислены)
        self._banned = {}               # хост → до какого момента забанен
        self._ban_lock = threading.Lock()
        # peers читают потоки gossip/sync, а пишут — discovery и обработчики
        # входящих сообщений. Без блокировки обход падал бы с RuntimeError
        # «Set changed size during iteration», убивая поток рассылки.
        self._peers_lock = threading.Lock()
        # Анти-петля gossip с пределом размера (FIFO) — без утечки памяти:
        self.seen_tx = _BoundedSet(seen_limit)      # txid уже виденных транзакций
        self.seen_blocks = _BoundedSet(seen_limit)  # хеши уже виденных блоков
        self._seen_lock = threading.Lock()
        # Потолок одновременно обслуживаемых входящих соединений.
        self._inbound = threading.Semaphore(MAX_INBOUND_CONNECTIONS)
        self._server = None
        self._running = False
        self._node_id = secrets.token_hex(8)   # чтобы не отвечать на свой же маяк
        self._discovery_running = False
        self.on_discover = None                # колбэк (host, port) при находке

    # --- Протокол --------------------------------------------------------
    def _handle_message(self, raw: bytes, host=None) -> bytes:
        # Верхний предохранитель: любое некорректное сообщение от пира
        # (нет обязательного поля, битая структура блока/транзакции) не должно
        # ронять поток-обработчик — иначе удалённый пир может вырубить узел.
        # Но и молча терпеть поток мусора не будем — начисляем штраф.
        try:
            return self._dispatch(raw, host)
        except Exception:
            self._penalise(host, PENALTY_BAD_MESSAGE)
            return self._json({"type": "error", "error": "bad message"})

    def _dispatch(self, raw: bytes, host=None) -> bytes:
        try:
            message = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            self._penalise(host, PENALTY_BAD_MESSAGE)
            return self._json({"type": "error", "error": "bad json"})

        mtype = message.get("type")

        if mtype == "ping":
            return self._json({"type": "pong"})

        if mtype == "hello":
            # Знакомимся только со СВОЕЙ сетью: чужой узел иначе занимал бы
            # место в таблице пиров и слал блоки, которые всё равно нечем
            # применить (другой генезис — другая цепочка).
            if not self.same_network(message):
                self._penalise(host, PENALTY_FOREIGN_NETWORK)
                return self._json({"type": "error", "error": "другая сеть",
                                   **self.network_id()})
            host, port = message.get("host"), message.get("port")
            if host and port:
                self.add_peer(host, port)
            return self._json(self._peers_payload())

        if mtype == "get_peers":
            return self._json(self._peers_payload())

        if mtype == "get_height":
            # Работа цепочки, а не только высота: по ней пир решает, у кого
            # синхронизироваться (то же правило, что и в replace_chain).
            return self._json({"type": "height", "height": self.node.height,
                               "work": self.node.blockchain.total_work})

        if mtype == "get_blocks":
            # Пачка блоков с указанной высоты. Отдавать цепочку целиком нельзя:
            # она упиралась бы в лимит размера сообщения (32 МБ), то есть у
            # сети был бы жёсткий потолок длины, после которого новые узлы уже
            # не смогли бы синхронизироваться вовсе.
            chain = self.node.blockchain.chain
            start = max(0, int(message.get("from", 0)))
            count = int(message.get("count", self.max_blocks_per_message))
            count = max(1, min(count, self.max_blocks_per_message))
            return self._json({
                "type": "blocks",
                "from": start,
                "blocks": [b.to_dict() for b in chain[start:start + count]],
                "height": len(chain),
                "work": self.node.blockchain.total_work,
                "base_difficulty": self.node.blockchain.difficulty,
            })

        if mtype == "get_hashes":
            # Только хеши — по ним ищется общий блок перед докачкой (дёшево).
            chain = self.node.blockchain.chain
            start = max(0, int(message.get("from", 0)))
            count = int(message.get("count", self.max_blocks_per_message))
            count = max(1, min(count, self.max_blocks_per_message))
            return self._json({
                "type": "hashes",
                "from": start,
                "hashes": [b.hash for b in chain[start:start + count]],
                "height": len(chain),
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
            origin = tuple(message["from"]) if message.get("from") else None

            with self._seen_lock:
                already_seen = bhash in self.seen_blocks
            if already_seen:
                # Повтор уже обработанного блока. Раньше здесь был молчаливый
                # выход — и если блок отвергли из-за отставания, повторные
                # рассылки того же блока узел игнорировал, оставаясь позади до
                # ближайшего периодического sync (а его может и не быть).
                # Теперь повтор — ещё один повод догнать.
                if origin and self._block_is_ahead(block_dict):
                    self._sync_from_throttled(origin)
                return self._json({"type": "ack", "accepted": False,
                                   "height": self.node.height})

            accepted = self.node.receive_block(block_dict)
            if accepted:
                with self._seen_lock:
                    self.seen_blocks.add(bhash)
                # Gossip: пересылаем блок дальше по сети (кроме отправителя).
                self._gossip({"type": "block", "block": block_dict,
                              "from": [self.host, self.port]},
                             exclude=origin, background=True)
            else:
                # Виденным помечаем только то, что нам и правда не подходит:
                # бракованный блок или чужую развилку — их дорогая проверка
                # выполнится один раз. Блок «из будущего» (мы просто отстали)
                # НЕ помечаем: он ещё пригодится, а повторно отбрасывается за
                # O(1) — receive_block сверяет previous_hash до всех проверок.
                if not self._block_is_ahead(block_dict):
                    with self._seen_lock:
                        self.seen_blocks.add(bhash)
                    # Блок не подошёл, и дело НЕ в нашем отставании: либо брак,
                    # либо чужая развилка. Проверка его дорога — за поток
                    # такого добра пир должен отвечать.
                    self._penalise(host, PENALTY_INVALID_BLOCK)
                if origin:
                    self._sync_from_throttled(origin)
            return self._json({"type": "ack", "accepted": accepted,
                               "height": self.node.height})

        return self._json({"type": "ack"})

    @staticmethod
    def _json(obj):
        return json.dumps(obj).encode("utf-8")

    def network_id(self) -> dict:
        """Отпечаток сети: идентификатор цепи и хеш генезиса.

        Одного `chain_id` мало — он общий для всей «сети», но узлы с разной
        базовой сложностью имеют РАЗНЫЙ генезис, а значит и несовместимые
        цепочки (`replace_chain` такую отвергнет). Проверяем оба.

        Это опознание сети, а НЕ аутентификация: злонамеренный узел может
        назвать чужие значения. Смысл в другом — не пускать в таблицу пиров
        соседей из другой сети и заставить атакующего хотя бы знать наш
        генезис. Настоящая аутентификация потребовала бы ключей пиров.
        """
        return {"chain_id": CHAIN_ID,
                "genesis": self.node.blockchain.chain[0].hash}

    def same_network(self, message) -> bool:
        """True, если собеседник назвал ту же сеть, что и наша.

        Отсутствие полей — тоже несовпадение: иначе проверку обходили бы,
        просто не присылая их.
        """
        if not isinstance(message, dict):
            return False
        ours = self.network_id()
        return (message.get("chain_id") == ours["chain_id"]
                and message.get("genesis") == ours["genesis"])

    def _peers_payload(self) -> dict:
        """Ответ со списком пиров — не длиннее MAX_PEERS_PER_MESSAGE.

        Отдавать всю таблицу целиком нельзя: тогда отравленный узел раздаёт
        сотни мусорных адресов каждому, кто спросит, и порча расползается по
        сети сама. Отпечаток сети в ответе позволяет спросившему убедиться,
        что список пришёл от своего узла.
        """
        return {"type": "peers",
                "peers": [list(p) for p in self.peer_list()[:MAX_PEERS_PER_MESSAGE]],
                **self.network_id()}

    # --- Сервер ----------------------------------------------------------
    def _serve(self):
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # Слушаем на ВСЕХ интерфейсах (0.0.0.0): тогда узел доступен и по
        # localhost, и по IP в локальной сети — другой компьютер сможет
        # подключиться. self.host остаётся «адресом для представления» (его
        # узел сообщает пирам, чтобы они могли подключиться обратно).
        self._server.bind(("0.0.0.0", self.port))
        self._server.listen(8)
        self._running = True
        while self._running:
            try:
                conn, addr = self._server.accept()
            except OSError:
                break
            # Забаненного не обслуживаем вовсе — дешевле всего отказать сразу,
            # не тратя ни потока, ни разбора сообщения.
            if self.is_banned(addr[0]):
                conn.close()
                continue
            # Каждое соединение обслуживаем в отдельном потоке, чтобы узел мог
            # синхронизироваться, пока обрабатывает входящее сообщение. Число
            # таких потоков ограничено: иначе поток соединений от одного пира
            # исчерпает память узла.
            if not self._inbound.acquire(blocking=False):
                conn.close()
                continue
            threading.Thread(target=self._handle_conn, args=(conn, addr[0]),
                             daemon=True).start()

    def _handle_conn(self, conn, host=None):
        """Обслуживает одно входящее сообщение и закрывает соединение.

        Таймаут обязателен: без него пир, который открыл соединение и замолчал
        (или прислал половину заголовка длины), держал бы поток бесконечно —
        полсотни таких «молчунов» навсегда занимают полсотни потоков.
        """
        try:
            with conn:
                conn.settimeout(self.inbound_timeout)
                raw = recv_message(conn)
                if raw:
                    send_message(conn, self._handle_message(raw, host))
        except OSError:
            pass                    # таймаут или разрыв — просто закрываем
        finally:
            self._inbound.release()

    def start(self):
        thread = threading.Thread(target=self._serve, daemon=True)
        thread.start()
        return thread

    def stop(self):
        # Сохраняем соседей до закрытия сокета: после перезапуска узел должен
        # знать, к кому идти, а не начинать с пустой таблицы.
        self.save_peers()
        self._running = False
        self._discovery_running = False
        server, self._server = self._server, None
        if server is not None:
            # Одного close() мало: поток, висящий в accept(), от него не
            # просыпается, и «остановленный» узел продолжает отвечать на
            # запросы. shutdown() прерывает accept и по-настоящему гасит узел.
            try:
                server.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            server.close()

    # --- Клиент ----------------------------------------------------------
    def send(self, host, port, message: dict) -> dict:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
            client.settimeout(self.peer_timeout)
            client.connect((host, port))
            send_message(client, self._json(message))
            raw = recv_message(client)
        return json.loads(raw.decode("utf-8")) if raw else {}

    def peer_list(self):
        """Снимок таблицы пиров — безопасен при параллельных add_peer."""
        with self._peers_lock:
            return list(self.peers)

    def add_peer(self, host, port) -> bool:
        """Добавляет пира. Возвращает True, только если он ДЕЙСТВИТЕЛЬНО новый.

        Потолок обязателен: адреса ничего не стоят и никак не проверяются, так
        что без него один ответ на hello раздувает таблицу до любого размера.
        """
        if (host, port) == (self.host, self.port):
            return False
        if self.is_banned(host):
            return False                  # забаненный сосед нам не нужен
        with self._peers_lock:
            if (host, port) in self.peers or len(self.peers) >= self.max_peers:
                return False
            self.peers.add((host, port))
            return True

    def remove_peer(self, host, port) -> None:
        with self._peers_lock:
            self.peers.discard((host, port))

    # --- Репутация пиров --------------------------------------------------
    @staticmethod
    def _is_loopback(host) -> bool:
        return str(host) in ("localhost", "::1") or str(host).startswith("127.")

    def is_banned(self, host) -> bool:
        """Забанен ли адрес прямо сейчас (истёкшие баны снимаются сами)."""
        with self._ban_lock:
            until = self._banned.get(host)
            if until is None:
                return False
            if time.monotonic() >= until:
                del self._banned[host]          # срок вышел — прощаем
                return False
            return True

    def ban_score(self, host) -> int:
        """Текущие штрафные очки адреса (для диагностики и тестов)."""
        with self._ban_lock:
            score, _when = self._scores.get(host, (0, 0.0))
            return score

    def ban_peer(self, host, duration=None) -> None:
        """Банит адрес и выкидывает все его порты из таблицы пиров."""
        with self._ban_lock:
            self._banned[host] = time.monotonic() + (duration or self.ban_duration)
            self._scores.pop(host, None)
        for peer in self.peer_list():
            if peer[0] == host:
                self.remove_peer(*peer)

    def _penalise(self, host, points) -> bool:
        """Начисляет штраф и банит при переполнении. True, если забанили.

        Одиночная оплошность не должна копиться годами: если прошлое нарушение
        было давно (score_reset_after), счёт обнуляется. Наказываем всплеск,
        а не редкие сбои у честного соседа.
        """
        if not host or (self._is_loopback(host) and not self.ban_loopback):
            return False
        now = time.monotonic()
        with self._ban_lock:
            score, when = self._scores.get(host, (0, now))
            if now - when > self.score_reset_after:
                score = 0
            score += points
            if score < self._ban_threshold:
                self._scores[host] = (score, now)
                return False
            self._scores.pop(host, None)
            self._banned[host] = now + self.ban_duration
        for peer in self.peer_list():
            if peer[0] == host:
                self.remove_peer(*peer)
        return True

    # --- Соседи между запусками ------------------------------------------
    def save_peers(self, path=None) -> bool:
        """Сохраняет таблицу пиров, чтобы после перезапуска не начинать с нуля.

        Запись атомарная (во временный файл и переименование): оборванное
        сохранение не должно оставить узел с испорченным списком соседей.
        """
        path = path or self.peers_file
        if not path:
            return False
        payload = {"peers": [list(peer) for peer in self.peer_list()]}
        temporary = f"{path}.tmp"
        try:
            with open(temporary, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False)
            os.replace(temporary, path)
            return True
        except OSError:
            return False

    def load_peers(self, path=None) -> int:
        """Читает соседей с прошлого запуска. Возвращает, скольких добавили.

        Недоступные адреса не отбрасываем: узел мог просто быть выключен и ещё
        вернётся. Отсеивает их bootstrap — и только тех, кто ответил из ЧУЖОЙ
        сети.
        """
        path = path or self.peers_file
        if not path:
            return 0
        try:
            with open(path, encoding="utf-8") as handle:
                stored = json.load(handle)
        except (OSError, ValueError):
            return 0                      # файла нет или он испорчен — не беда
        added = 0
        for entry in (stored.get("peers") or [])[:self.max_peers]:
            try:
                host, port = str(entry[0]), int(entry[1])
            except (TypeError, ValueError, IndexError, KeyError):
                continue
            if self.add_peer(host, port):
                added += 1
        return added

    def bootstrap(self) -> int:
        """Первое подключение: соседи с диска плюс seed-узлы.

        Здоровается со всеми параллельно, забирает их списки пиров и один раз
        синхронизируется. Возвращает число живых соседей нашей сети.
        """
        self.load_peers()
        for host, port in self.seeds:
            self.add_peer(host, port)

        candidates = self.peer_list()
        if not candidates:
            return 0
        alive = 0
        for peer, resp in self._fanout(
                candidates,
                lambda host, port: self.send(host, port, self._hello_message())):
            if self.same_network(resp):
                self._accept_peers(resp, source=peer[0])  # узнаём их соседей
                alive += 1
            elif resp:
                self.remove_peer(*peer)    # ответил, но из другой сети
        if alive:
            self.sync()
            self.save_peers()
        return alive

    def _accept_peers(self, resp: dict, source=None):
        """Берёт адреса из ответа пира: не больше MAX_PEERS_PER_MESSAGE и с
        учётом потолка таблицы. Возвращает только по-настоящему новых пиров."""
        if not self.same_network(resp):
            return []                     # список от чужой сети не берём
        fresh, garbage = [], 0
        entries = resp.get("peers") or []
        for entry in list(entries)[:MAX_PEERS_PER_MESSAGE]:
            try:
                host, port = entry[0], int(entry[1])
            except (TypeError, ValueError, IndexError, KeyError):
                garbage += 1                  # мусор в ответе — пропускаем
                continue
            if self.add_peer(host, port):
                fresh.append((host, port))
        if garbage:
            self._penalise(source, PENALTY_GARBAGE_PEERS)
        return fresh

    def _fanout(self, peers, action):
        """Параллельно выполняет action(host, port) по всем пирам.

        Последовательный обход — главная уязвимость сетевого слоя: каждый
        недостижимый адрес держит поток до таймаута, поэтому десяток
        «чёрных дыр» в таблице останавливал узел на минуты. Возвращает пары
        (пир, результат) только для тех, кто ответил.
        """
        peers = list(peers)
        if not peers:
            return []
        results = []
        with ThreadPoolExecutor(max_workers=min(len(peers), FANOUT_WORKERS)) as pool:
            futures = {pool.submit(action, host, port): (host, port)
                       for host, port in peers}
            for future in as_completed(futures):
                try:
                    results.append((futures[future], future.result()))
                except (OSError, ValueError):
                    continue          # недоступен или прислал мусор — пропускаем
        return results

    def _hello_message(self) -> dict:
        return {"type": "hello", "host": self.host, "port": self.port,
                **self.network_id()}

    def _say_hello(self, peers):
        """Представляется каждому пиру (параллельно), чтобы сеть стала мешем.

        Кто ответил из чужой сети — из таблицы убирается.
        """
        for peer, resp in self._fanout(
                peers, lambda host, port: self.send(host, port,
                                                    self._hello_message())):
            if not self.same_network(resp):
                self.remove_peer(*peer)

    def connect(self, host, port) -> bool:
        """Подключается к узлу, обменивается пирами и синхронизируется.

        Узел не только узнаёт пиров соседа, но и представляется им (hello),
        чтобы сеть превращалась в связный меш, а не звезду. Возвращает False,
        если на том конце другая сеть — такого соседа в пиры не берём.
        """
        resp = self.send(host, port, self._hello_message())
        if not self.same_network(resp):
            self.remove_peer(host, port)
            return False
        self.add_peer(host, port)
        self._say_hello(self._accept_peers(resp, source=host))
        self.sync()
        return True

    def discover_peers(self):
        """Сплетни об адресах: спросить у известных пиров их списки пиров и
        подключиться к новым (как `addr`-обмен в Bitcoin — сеть расползается
        сама, без ручного ввода адресов каждого узла)."""
        replies = self._fanout(
            self.peer_list(),
            lambda host, port: self.send(host, port, {"type": "get_peers"}))
        fresh = []
        for _peer, resp in replies:
            fresh.extend(self._accept_peers(resp, source=_peer[0]))
        self._say_hello(fresh)

    # --- Авто-поиск в локальной сети (UDP-маяки, WiFi/LAN без интернета) ---
    def start_discovery(self, interval: int = 5):
        """Запустить рассылку и приём UDP-маяков для авто-поиска узлов в сети."""
        if self._discovery_running:
            return
        self._discovery_running = True
        threading.Thread(target=self._discovery_listen, daemon=True).start()
        threading.Thread(target=self._discovery_announce, args=(interval,),
                         daemon=True).start()

    def stop_discovery(self):
        self._discovery_running = False

    def _discovery_announce(self, interval: int):
        """Периодически кричим в сеть «я — узел B-hydra на порту N»."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        # Отпечаток сети прямо в маяке: сосед из чужой сети отсеивается ещё
        # до TCP-подключения.
        beacon = json.dumps({"magic": DISCOVERY_MAGIC, "port": self.port,
                             "id": self._node_id,
                             **self.network_id()}).encode("utf-8")
        while self._discovery_running and self._running:
            try:
                sock.sendto(beacon, ("255.255.255.255", DISCOVERY_PORT))
            except OSError:
                pass
            time.sleep(interval)
        sock.close()

    def _discovery_listen(self):
        """Слушаем маяки соседей и подключаемся к новым узлам."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:                                   # на одной машине — несколько узлов
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except (AttributeError, OSError):
            pass
        try:
            sock.bind(("", DISCOVERY_PORT))
        except OSError:
            return
        sock.settimeout(1.0)
        while self._discovery_running and self._running:
            try:
                data, addr = sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                msg = json.loads(data.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                continue
            if msg.get("magic") != DISCOVERY_MAGIC or msg.get("id") == self._node_id:
                continue                        # чужой протокол или наш же маяк
            if not self.same_network(msg):
                continue                        # узел другой сети — не наш сосед
            peer = (addr[0], int(msg.get("port", 0)))
            if not peer[1] or peer == (self.host, self.port):
                continue
            if not self.add_peer(*peer):
                continue            # уже знаем его или таблица пиров полна
            try:
                if not self.connect(*peer):     # обмен пирами + синхронизация
                    self.remove_peer(*peer)     # оказался из другой сети
                    continue
            except OSError:
                self.remove_peer(*peer)
                continue
            if self.on_discover:
                try:
                    self.on_discover(*peer)
                except Exception:
                    pass
        sock.close()

    def broadcast(self, message: dict):
        results = self._fanout(self.peer_list(),
                               lambda host, port: self.send(host, port, message))
        return [resp for _peer, resp in results]

    def _gossip(self, message: dict, exclude=None, background: bool = False):
        """Пересылает сообщение всем пирам, кроме `exclude`.

        В обработчике входящих сообщений используется background=True, чтобы
        пересылка шла в отдельном потоке и не блокировала ответ (и не
        приводила к взаимоблокировке в кольцевых топологиях).
        """
        def _run():
            targets = [peer for peer in self.peer_list() if peer != exclude]
            self._fanout(targets,
                         lambda host, port: self.send(host, port, message))

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
        """Находит пира с самой ТЯЖЁЛОЙ цепочкой и подтягивает её.

        Ранжируем по суммарной работе — тому же правилу, по которому
        replace_chain принимает решение. Выбор по высоте расходился с
        консенсусом: более короткая, но более трудная цепочка не выигрывала
        и узел на неё не переходил. Пиры старых версий работу не сообщают —
        для них остаётся сравнение по высоте.

        Ошибиться выбором безопасно: окончательное решение всё равно за
        replace_chain, который сверяет работу сам.
        """
        replies = self._fanout(
            self.peer_list(),
            lambda host, port: self.send(host, port, {"type": "get_height"}))

        heavier = [(resp["work"], peer) for peer, resp in replies
                   if isinstance(resp.get("work"), int)
                   and resp["work"] > self.node.blockchain.total_work]
        # Запасной путь: пир не сообщил работу (старая версия) — судим по высоте.
        taller = [(resp.get("height", 0), peer) for peer, resp in replies
                  if resp.get("work") is None
                  and resp.get("height", 0) > self.node.height]

        # Пробуем нескольких кандидатов подряд, а не только лучшего. Иначе один
        # пир — упавший, подвисший или намеренно объявивший огромную работу —
        # навсегда перекрывал бы синхронизацию со всеми остальными: он всегда
        # выигрывал выбор, а докачка с него всегда проваливалась.
        ranked = sorted(heavier, reverse=True) or sorted(taller, reverse=True)
        for _rank, peer in ranked[:SYNC_PEER_ATTEMPTS]:
            if self._sync_from(peer):
                return True
        return False

    def _block_is_ahead(self, block_dict) -> bool:
        """True, если блок выше нашей вершины — значит мы просто отстали.

        Блок, продолжающий нашу цепочку, имеет index == height; всё, что
        больше, — признак отставания, а не брака.
        """
        index = block_dict.get("index")
        return isinstance(index, int) and not isinstance(index, bool) \
            and index > self.node.height

    def _sync_from_throttled(self, peer) -> bool:
        """Тянет цепочку у пира, но не чаще sync_retry_interval.

        Ограничение обязательно: повтор чужого блока запускает докачку целой
        цепочки, и без него сосед мог бы гонять нас за ней сколько угодно
        часто.
        """
        now = time.monotonic()
        with self._sync_lock:
            if now - self._last_block_sync < self.sync_retry_interval:
                return False
            self._last_block_sync = now
        return self._sync_from(peer)

    def _peer_hash(self, peer, index):
        """Хеш блока пира на высоте index (или None, если недоступен)."""
        try:
            resp = self.send(peer[0], peer[1],
                             {"type": "get_hashes", "from": index, "count": 1})
        except OSError:
            return None
        hashes = resp.get("hashes") or []
        return hashes[0] if hashes else None

    def _common_height(self, peer, their_height):
        """Наибольшая высота, на которой наш блок совпадает с блоком пира.

        Обычный случай (пир просто ушёл вперёд) решается одним запросом.
        Развилка — двоичным поиском, это ~log2(длины) запросов вместо
        скачивания всей чужой цепочки ради поиска точки расхождения.
        """
        high = min(self.node.height, their_height) - 1
        if high < 0:
            return None
        chain = self.node.blockchain.chain
        if self._peer_hash(peer, high) == chain[high].hash:
            return high                      # общий префикс — вся наша цепочка
        if self._peer_hash(peer, 0) != chain[0].hash:
            return None                      # чужой генезис — это другая сеть

        low = 0
        while low < high:
            middle = (low + high + 1) // 2
            if self._peer_hash(peer, middle) == chain[middle].hash:
                low = middle
            else:
                high = middle - 1
        return low

    def _fetch_blocks(self, peer, start, stop):
        """Тянет блоки [start; stop) пачками, а не одним куском."""
        stop = min(stop, start + self.max_sync_blocks)
        blocks = []
        at = start
        while at < stop:
            try:
                resp = self.send(peer[0], peer[1], {
                    "type": "get_blocks", "from": at,
                    "count": min(self.max_blocks_per_message, stop - at)})
            except OSError:
                break
            batch = resp.get("blocks") or []
            if not batch:
                break                        # пир больше ничего не даёт
            blocks.extend(batch)
            at += len(batch)
        return blocks

    def _sync_from(self, peer) -> bool:
        """Догоняет цепочку пира инкрементально.

        Раньше здесь скачивалась ВСЯ чужая цепочка одним сообщением и
        перепроверялась с нуля — даже ради одного блока. Теперь ищется общий
        блок, докачивается только хвост после него, и если это простое
        продолжение нашей цепочки, блоки применяются по одному: проверяется
        только новое, поверх уже проверенного префикса.
        """
        try:
            info = self.send(peer[0], peer[1], {"type": "get_height"})
        except OSError:
            return False
        their_height = int(info.get("height") or 0)
        if their_height <= 0:
            return False

        fork = self._common_height(peer, their_height)
        if fork is None:
            return False

        blocks = self._fetch_blocks(peer, fork + 1, their_height)
        if not blocks:
            return False

        if fork + 1 == self.node.height:
            # Чистое продолжение: применяем по одному. Каждый блок проверяется
            # против текущего набора UTXO — это O(новых блоков), а не O(цепи).
            applied = 0
            for block in blocks:
                if not self.node.receive_block(block):
                    break
                applied += 1
            return applied > 0

        # Развилка: собираем кандидата из нашего общего префикса и чужого
        # хвоста. Решение принимает replace_chain — по суммарной работе.
        prefix = [b.to_dict() for b in self.node.blockchain.chain[:fork + 1]]
        return self.node.replace_chain(prefix + blocks)


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
    parser.add_argument("--seed", action="append", default=[],
                        help="seed-узел для первого запуска (можно повторять)")
    parser.add_argument("--peers-file", default=DEFAULT_PEERS_FILE,
                        help="файл с соседями между запусками")
    parser.add_argument("--difficulty", type=int, default=3, help="базовая сложность")
    parser.add_argument("--demo", action="store_true", help="запустить демо из 3 узлов")
    args = parser.parse_args()

    if args.demo or not args.port:
        _demo()
        return

    # Seed-узлы: из --seed и из переменной окружения BHYDRA_SEEDS
    # ("host:port,host:port") — так их удобно задавать в докере/сервисе.
    raw_seeds = list(args.seed) + [
        item for item in os.environ.get("BHYDRA_SEEDS", "").split(",") if item]
    seeds = []
    for item in raw_seeds:
        host, _, port = item.rpartition(":")
        if host and port.isdigit():
            seeds.append((host, int(port)))

    node = P2PNode("0.0.0.0", args.port, BHydraNode(difficulty=args.difficulty),
                   peers_file=args.peers_file, seeds=seeds)
    node.start()
    # Соседи с прошлого запуска + seed-узлы: узел находит сеть сам, без --peer.
    alive = node.bootstrap()
    if args.peer:
        host, _, port = args.peer.rpartition(":")
        node.connect(host, int(port))
    print(f"P2P-узел B-hydra на :{args.port} | пиров: {len(node.peers)} "
          f"(живых при старте: {alive}) | высота: {node.node.height}"
          f"  (Ctrl+C — стоп)")
    try:
        while True:                 # периодически подтягиваем цепочку у пиров
            time.sleep(5)
            node.sync()
            node.save_peers()       # свежий список переживёт даже аварийный выход
    except KeyboardInterrupt:
        node.stop()                 # stop() тоже сохраняет соседей


if __name__ == "__main__":
    main()
