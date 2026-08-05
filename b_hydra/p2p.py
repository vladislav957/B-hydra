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

from . import secure
from .tcp import MAX_MESSAGE_SIZE, recv_message, send_message
from .transport import TCPTransport

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
# …и не больше этого с ОДНОГО хоста. Пока соединение жило одно сообщение, слот
# освобождался сам через миллисекунды. Постоянное соединение держится, пока пир
# его не отпустит, поэтому один сосед мог бы занять все 64 слота и не пускать
# никого больше — причём совершенно легально, просто изредка пингуя. Лимит на
# хост оставляет ему свою долю и ничего не отнимает у остальных.
# Loopback не ограничиваем — по той же причине, что и не баним (ban_loopback):
# демо и тесты поднимают десятки узлов на 127.0.0.1, и общий лимит на хост
# запретил бы им разговаривать друг с другом.
MAX_INBOUND_PER_HOST = 16
FANOUT_WORKERS = 32              # параллелизм рассылки по пирам

# --- Постоянные соединения --------------------------------------------------
# Раньше на КАЖДОЕ сообщение открывался новый TCP-сокет: три пакета рукопожатия
# и полный круг задержки до передачи первого байта полезных данных. Больнее
# всего это било по синхронизации, где на один вызов _sync_from приходятся
# get_height + ~log2(длины) запросов get_hashes + по запросу на каждую пачку
# блоков — десятки соединений подряд к одному и тому же соседу.
# Теперь исходящие соединения переиспользуются: сокет после ответа возвращается
# в пул и следующий запрос к тому же пиру идёт по нему.
MAX_POOLED_PER_PEER = 4          # idle-соединений на одного пира
MAX_POOLED_CONNECTIONS = 64      # общий потолок пула (файловые дескрипторы)
# Держать сокет в пуле дольше, чем сосед готов его ждать, бессмысленно: сервер
# закрывает молчащее соединение по inbound_timeout, и переиспользование такого
# сокета всегда приводило бы к повторной попытке. Поэтому срок жизни в пуле
# СЧИТАЕТСЯ ОТ таймаута сервера, а не задаётся отдельным числом — два
# независимых значения неизбежно разъехались бы.
POOL_IDLE_TIMEOUT = INBOUND_TIMEOUT / 2

# Повтор чужого блока — повод попробовать догнать цепочку, но не чаще, чем
# раз в SYNC_RETRY_INTERVAL: иначе пир гонял бы нас за полной цепочкой
# сколько угодно часто.
SYNC_RETRY_INTERVAL = 2.0        # секунд между докачками цепочки по чужому блоку

# Цепочка передаётся ПАЧКАМИ. Раньше на запрос отдавалась вся цепочка одним
# сообщением, а на сообщение стоит лимит 32 МБ — то есть у сети был жёсткий
# потолок длины (~30 тыс. блоков с одним coinbase, ~10 тыс. с транзакциями),
# после которого новый узел не смог бы синхронизироваться в принципе. Плюс
# догнать один блок стоило скачивания и полной перепроверки всей цепочки.
MAX_BLOCKS_PER_MESSAGE = 500     # блоков в одной пачке (потолок по числу)
# …и потолок по РАЗМЕРУ. Одного счётчика блоков мало: блок вмещает до 5000
# транзакций (~3,8 МБ), поэтому 500 таких блоков — это около 1,9 ГБ, а
# сообщение больше MAX_MESSAGE_SIZE просто отбрасывается получателем. Тогда
# пачка не пролезает, докачка возвращает пустоту и синхронизация встаёт —
# ровно тот потолок длины, ради снятия которого вводились пачки.
# Считается ОТ лимита сообщения, а не задаётся отдельным числом: два
# независимых значения рано или поздно разъедутся, и пачка снова перестанет
# пролезать. Четверть — запас на JSON-обвязку ответа.
MAX_BATCH_BYTES = MAX_MESSAGE_SIZE // 4
MAX_SYNC_BLOCKS = 100_000        # потолок докачки за одну синхронизацию
SYNC_PEER_ATTEMPTS = 4           # сколько кандидатов пробуем за одну sync()

# Блок анонсируется ХЕШЕМ (inv), тело запрашивается отдельно (get_block).
# Раньше полный блок улетал каждому пиру, даже тому, у кого он уже есть:
# трафик равнялся размеру блока, умноженному на число соседей, а заполненный
# блок (до 5000 транзакций) — это мегабайты на каждого.
MAX_INFLIGHT_FETCHES = 16        # сколько тел блоков качаем одновременно
# Транзакции анонсируются так же, как блоки: сначала txid (inv), тело — по
# запросу get_tx. Прежде транзакция рассылалась ЦЕЛИКОМ каждому соседу, и в
# связной сети один и тот же байт приходил столько раз, сколько у узла соседей:
# каждый пересылал тело дальше, а получатель отбрасывал его по дедупу — то есть
# трафик тратился уже ПОСЛЕ того, как становился ненужным.
# Свой потолок, а не общий с блоками: поток анонсов транзакций не должен
# вытеснять докачку блоков — блоки двигают консенсус, транзакции ждут.
MAX_INFLIGHT_TX_FETCHES = 32     # сколько тел транзакций качаем одновременно

# Узел должен переживать перезапуск. Таблица пиров жила только в памяти, а
# UDP-маяк работает лишь в пределах одной локальной сети (широковещание за
# роутер не уходит) — поэтому узел в интернете после рестарта оставался ОДИН и
# требовал ручного --peer. Теперь соседи сохраняются на диск, а для самого
# первого запуска (файла ещё нет) есть seed-узлы — как DNS seeds в Bitcoin.
DEFAULT_PEERS_FILE = "bhydra_peers.json"
DEFAULT_SEEDS = ()               # адреса вида ("host", port); задаются сетью
# Долговременный ключ УЗЛА (не кошелька!): им подписывается рукопожатие, по нему
# соседи узнают нас после перезапуска. Утечка этого файла позволяет выдать себя
# за наш узел, но НЕ тронуть монеты — это разные ключи.
DEFAULT_IDENTITY_FILE = "bhydra_identity.json"

def local_ip() -> str:
    """IP этой машины в локальной сети — адрес, по которому достанут ДРУГИЕ.

    `127.0.0.1` виден только на своём компьютере, и узел, представившийся так,
    для сети бесполезен: соседи не смогут подключиться обратно, а телефон не
    откроет кошелёк. UDP-«коннект» пакетов не шлёт — он лишь заставляет ядро
    выбрать интерфейс и показать его адрес.
    """
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("8.8.8.8", 80))
        return probe.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        probe.close()


def parse_seeds(items) -> list:
    """Разбирает адреса вида `host:port` в пары. Мусор молча пропускается."""
    seeds = []
    for item in items or ():
        host, _, port = str(item).strip().rpartition(":")
        if host and port.isdigit():
            seeds.append((host, int(port)))
    return seeds


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


# Маркер «открытый текст, но он запрещён» — отличается и от сессии, и от None.
_REFUSED = object()


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

from .blockchain import CHAIN_ID
from .node import BHydraNode


class P2PNode:
    """Сетевой узел B-hydra: TCP-сервер + клиент + синхронизация."""

    def __init__(self, host="127.0.0.1", port=5000, node=None,
                 seen_limit=SEEN_LIMIT, max_peers=MAX_PEERS,
                 peers_file=None, seeds=None, encrypt=True,
                 require_encryption=False, identity=None, identity_file=None,
                 api_port=None, api_tls=False, transport=None):
        self.host = host
        self.port = port
        # Чем дотягиваемся до соседей. По умолчанию TCP/IP — как было всегда.
        # Протокол выше сокета транспорта не знает (см. transport.py), поэтому
        # подменой этого объекта сеть переносится на любой байтовый поток:
        # Bluetooth RFCOMM, Unix-сокет, что угодно.
        self.transport = transport or TCPTransport()
        # Адрес REST-API этого узла. Он объявляется соседям рядом с P2P-адресом
        # и нужен КОШЕЛЬКАМ: телефон и браузер не умеют говорить нашим TCP-
        # протоколом (сырых сокетов там нет вовсе), им нужна HTTP-точка входа.
        # Узнать её больше неоткуда — P2P-порт для этого не годится.
        self.api_port = int(api_port) if api_port else None
        self.api_tls = bool(api_tls)
        self._peer_api = {}         # (host, port) → (api_port, tls) соседей
        # Шифрование канала. `encrypt` управляет ИСХОДЯЩИМИ соединениями, сервер
        # же принимает и шифрованные, и открытые — иначе узел, намеренно
        # запущенный открытым, оказался бы отрезан от сети. Полностью закрыть
        # приём открытого текста — `require_encryption=True`.
        self.encrypt = bool(encrypt)
        self.require_encryption = bool(require_encryption)
        self.identity_file = identity_file
        self.identity = identity or self._load_identity()
        self._pins = {}                 # (host, port) → долговременный ключ пира
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
        self.max_batch_bytes = MAX_BATCH_BYTES
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
        # Докачка тел блоков по анонсам: дедуп по хешу и потолок параллелизма.
        self._inflight = set()
        self._fetch_slots = threading.Semaphore(MAX_INFLIGHT_FETCHES)
        # То же для транзакций — со своим потолком, чтобы поток анонсов
        # транзакций не вытеснял докачку блоков.
        self._inflight_tx = set()
        self._tx_fetch_slots = threading.Semaphore(MAX_INFLIGHT_TX_FETCHES)
        # Пул исходящих соединений: (host, port) → [(сокет, когда освободился)].
        self._pool = {}
        self._pooled = 0                # сколько сокетов лежит в пуле
        self._pool_open = True          # после stop() в пул больше не кладём
        self._pool_lock = threading.Lock()
        # Живые входящие соединения: их нужно закрывать в stop(), иначе
        # «остановленный» узел продолжает отвечать по уже открытым сокетам.
        self._conns = set()
        self._per_host = {}             # host → сколько его соединений обслуживаем
        self._conns_lock = threading.Lock()
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
        self._stopping = False      # stop() успел раньше, чем поднялся сокет
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
                # Заодно запоминаем, где у соседа REST: кошелькам нужен именно
                # он, а другого случая узнать этот адрес не будет.
                self.remember_api(host, port, message)
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
            # Набираем пачку, пока она укладывается в бюджет по размеру.
            blocks, used = [], 0
            for block in chain[start:start + count]:
                payload = block.to_dict()
                weight = len(json.dumps(payload))
                # Один блок отдаём всегда, даже если он больше бюджета: иначе
                # цепочку с крупным блоком нельзя было бы догнать вовсе.
                if blocks and used + weight > self.max_batch_bytes:
                    break
                blocks.append(payload)
                used += weight
            return self._json({
                "type": "blocks",
                "from": start,
                "blocks": blocks,
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
            # Тело транзакции напрямую: так приходит ответ на наш get_tx, так
            # её отдаёт кошелёк/API, и так же сосед может прислать её сам.
            accepted = self._process_tx(
                message["transaction"],
                tuple(message["from"]) if message.get("from") else None)
            return self._json({"type": "ack", "accepted": accepted})

        if mtype == "get_tx":
            tx = self.node.mempool.get(message.get("txid"))
            return self._json({"type": "tx_body",
                               "transaction": tx.to_dict() if tx else None})

        if mtype == "block":
            # Прямая доставка тела блока: так приходит ответ на наш get_block,
            # и так же сосед может прислать блок сам.
            accepted = self._process_block(
                message["block"],
                tuple(message["from"]) if message.get("from") else None,
                host)
            return self._json({"type": "ack", "accepted": accepted,
                               "height": self.node.height})

        if mtype == "inv":
            # Анонс: приходит только ХЕШ (блока или txid транзакции). Тело
            # запрашиваем, лишь если его у нас нет, — иначе полное тело улетало
            # бы каждому соседу, включая тех, у кого оно уже есть.
            digest = message.get("hash")
            announced = message.get("from") or []
            try:
                port = int(announced[1])
            except (TypeError, ValueError, IndexError):
                return self._json({"type": "ack", "wanted": False})
            # Качаем ТОЛЬКО у того, кто с нами реально соединился: хост берём
            # из соединения, а не из сообщения, иначе пир мог бы заставить нас
            # ходить по произвольным адресам.
            source = host or (announced[0] if announced else None)
            if not digest or not source:
                return self._json({"type": "ack", "wanted": False})
            is_tx = message.get("kind") == "tx"
            with self._seen_lock:
                known = digest in (self.seen_tx if is_tx else self.seen_blocks)
            reserve = self._start_tx_fetch if is_tx else self._start_fetch
            if known or not reserve(digest):
                return self._json({"type": "ack", "wanted": False})
            # Тянем тело в фоне: делать это прямо в обработчике значило бы
            # держать входящий поток на время исходящего запроса, а число
            # таких потоков ограничено.
            fetch = self._fetch_tx if is_tx else self._fetch_block
            threading.Thread(target=fetch,
                             args=((source, port), digest), daemon=True).start()
            return self._json({"type": "ack", "wanted": True})

        if mtype == "get_block":
            block = self.node.blockchain.block_by_hash(message.get("hash"))
            return self._json({"type": "block_body",
                               "block": block.to_dict() if block else None})

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

    # --- Адреса REST-API: как кошелёк узнаёт сеть -------------------------
    def api_payload(self) -> dict:
        """Свой REST-адрес для объявления соседям (пусто, если API не поднят)."""
        if not self.api_port:
            return {}
        return {"api": self.api_port, "api_tls": self.api_tls}

    def remember_api(self, host, port, message) -> bool:
        """Запоминает REST-адрес соседа из его сообщения.

        Хост берётся из адреса пира, а НЕ из тела сообщения: иначе любой узел
        объявлял бы API на чужом адресе и заводил кошельки не туда.
        """
        if not isinstance(message, dict):
            return False
        api_port = message.get("api")
        try:
            api_port = int(api_port)
        except (TypeError, ValueError):
            return False
        if not 1 <= api_port <= 65535:
            return False
        with self._peers_lock:
            if len(self._peer_api) >= self.max_peers:
                self._peer_api.pop(next(iter(self._peer_api)), None)
            self._peer_api[(str(host), int(port))] = (api_port,
                                                      bool(message.get("api_tls")))
        return True

    def api_nodes(self, include_self=True) -> list:
        """Адреса REST-API, известные узлу: свой и соседей.

        Это и есть «seed» для кошелька: телефон вводит ОДИН адрес, получает по
        нему остальные и дальше переживает падение любого отдельного узла.
        """
        found = []
        if include_self and self.api_port:
            scheme = "https" if self.api_tls else "http"
            # Свой адрес — тот, по которому нас достанет ТЕЛЕФОН. `127.0.0.1`
            # тут всегда ошибка: на телефоне это сам телефон. А значением по
            # умолчанию в GUI стоит именно loopback, так что случай не редкий.
            host = local_ip() if self._is_loopback(self.host) else self.host
            found.append(f"{scheme}://{host}:{self.api_port}")
        with self._peers_lock:
            known = dict(self._peer_api)
            peers = set(self.peers)
        for (host, port), (api_port, tls) in known.items():
            if (host, port) not in peers:
                continue              # соседа уже нет в таблице — и адреса нет
            url = f"{'https' if tls else 'http'}://{host}:{api_port}"
            if url not in found:
                found.append(url)
        return found

    def _peers_payload(self) -> dict:
        """Ответ со списком пиров — не длиннее MAX_PEERS_PER_MESSAGE.

        Отдавать всю таблицу целиком нельзя: тогда отравленный узел раздаёт
        сотни мусорных адресов каждому, кто спросит, и порча расползается по
        сети сама. Отпечаток сети в ответе позволяет спросившему убедиться,
        что список пришёл от своего узла.

        Вместе с адресами уходят и REST-адреса — свой (`api`) и известных
        соседей (`peer_api`). Свой обязателен отдельным полем: сам себя узел в
        список пиров не кладёт, и без этого спросивший не узнал бы адрес того,
        у кого только что спросил.
        """
        peers = self.peer_list()[:MAX_PEERS_PER_MESSAGE]
        with self._peers_lock:
            known = dict(self._peer_api)
        peer_api = {f"{h}:{p}": [known[(h, p)][0], known[(h, p)][1]]
                    for (h, p) in peers if (h, p) in known}
        return {"type": "peers",
                "peers": [list(p) for p in peers],
                "peer_api": peer_api,
                **self.api_payload(),
                **self.network_id()}

    def _absorb_peer_api(self, message) -> int:
        """Принимает REST-адреса соседей из ответа `peers`.

        Адрес API привязан к адресу ПИРА, который уже прошёл проверку сети,
        поэтому подмешать сюда произвольный хост нельзя: ключи, которых нет в
        присланном списке пиров, отбрасываются.
        """
        table = message.get("peer_api")
        if not isinstance(table, dict):
            return 0
        learned = 0
        for key, value in list(table.items())[:MAX_PEERS_PER_MESSAGE]:
            host, _, port = str(key).rpartition(":")
            try:
                port = int(port)
                api_port = int(value[0])
            except (TypeError, ValueError, IndexError):
                continue
            tls = bool(value[1]) if len(value) > 1 else False
            if (host, port) not in set(self.peer_list()):
                continue              # про чужих, не наших соседей, не слушаем
            if self.remember_api(host, port, {"api": api_port, "api_tls": tls}):
                learned += 1
        return learned

    # --- Сервер ----------------------------------------------------------
    def _serve(self):
        # Сокет собирается в ЛОКАЛЬНОЙ переменной и публикуется в self только
        # готовым: `stop()`, случившийся между bind и listen, обнулял self._server
        # прямо под ногами у этого потока — тот падал с AttributeError, а сокет
        # оставался открытым. Узел, остановленный сразу после старта, — обычное
        # дело в тестах и при ошибке настройки.
        # Сокет открывает ТРАНСПОРТ: здесь не должно быть ни AF_INET, ни
        # «0.0.0.0» — иначе узел навсегда привязан к TCP/IP (см. transport.py).
        # self.host остаётся «адресом для представления» (его узел сообщает
        # пирам, чтобы они могли подключиться обратно).
        server = self.transport.listen(self.port)
        if self._stopping:
            server.close()          # нас уже остановили — слушать некому
            return
        self._server = server
        self._running = True
        while self._running:
            try:
                conn, host = self.transport.accept(server)
            except OSError:
                break
            # Забаненного не обслуживаем вовсе — дешевле всего отказать сразу,
            # не тратя ни потока, ни разбора сообщения.
            if self.is_banned(host):
                conn.close()
                continue
            # Каждое соединение обслуживаем в отдельном потоке, чтобы узел мог
            # синхронизироваться, пока обрабатывает входящее сообщение. Число
            # таких потоков ограничено: иначе поток соединений от одного пира
            # исчерпает память узла.
            if not self._inbound.acquire(blocking=False):
                conn.close()
                continue
            # Доля одного хоста ограничена: постоянное соединение держится
            # долго, и без этого один сосед занял бы всю таблицу слотов.
            if not self._claim_host_slot(host):
                self._inbound.release()
                conn.close()
                continue
            threading.Thread(target=self._handle_conn, args=(conn, host),
                             daemon=True).start()

    def _accept_session(self, conn, first_frame):
        """Разбирает первый кадр: рукопожатие, открытый текст или отказ.

        Возвращает Session (шифрованный клиент), None (открытый — терпим) или
        _REFUSED, если открытый текст запрещён.
        """
        if secure.is_handshake(first_frame):
            return secure.server_handshake(conn, first_frame, self.identity)
        return _REFUSED if self.require_encryption else None

    def _claim_host_slot(self, host) -> bool:
        """Занимает слот входящего соединения под хост (или отказывает)."""
        if self._is_loopback(host):
            return True
        with self._conns_lock:
            if self._per_host.get(host, 0) >= MAX_INBOUND_PER_HOST:
                return False
            self._per_host[host] = self._per_host.get(host, 0) + 1
            return True

    def _release_host_slot(self, host) -> None:
        if self._is_loopback(host):
            return
        with self._conns_lock:
            left = self._per_host.get(host, 0) - 1
            if left > 0:
                self._per_host[host] = left
            else:
                self._per_host.pop(host, None)

    def inbound_connections(self, host=None) -> int:
        """Сколько входящих соединений обслуживается сейчас (всего или с хоста)."""
        with self._conns_lock:
            return self._per_host.get(host, 0) if host else len(self._conns)

    def _handle_conn(self, conn, host=None):
        """Обслуживает соединение, пока пир его не закроет или не замолчит.

        Соединение ПОСТОЯННОЕ: в одном сокете идёт сколько угодно запросов
        подряд, поэтому синхронизация (get_height + поиск развилки + пачки
        блоков) обходится одним рукопожатием вместо десятков.

        Таймаут обязателен: без него пир, который открыл соединение и замолчал
        (или прислал половину заголовка длины), держал бы поток бесконечно —
        полсотни таких «молчунов» навсегда занимают полсотни потоков. Он же
        освобождает слот из-под забытого постоянного соединения.
        """
        try:
            with conn:
                with self._conns_lock:
                    self._conns.add(conn)
                conn.settimeout(self.inbound_timeout)
                first = recv_message(conn)
                if not first:
                    return
                session = self._accept_session(conn, first)
                if session is _REFUSED:
                    return          # открытый текст при require_encryption
                if session is not None:
                    first = None    # первый кадр был рукопожатием, данных в нём нет
                while self._running:
                    if first is None:
                        frame = recv_message(conn)
                        if not frame:
                            break   # пир закрыл соединение, замолчал или прислал
                                    # кадр сверх лимита — поток дальше не разобрать
                        raw = session.decrypt(frame) if session else frame
                    else:
                        raw, first = first, None      # уже прочитанный кадр
                    response = self._handle_message(raw, host)
                    send_message(conn, session.encrypt(response) if session
                                 else response)
        except secure.DecryptError:
            # Тег не сошёлся: кадр подделан или испорчен в пути. Соединение
            # закрываем, но штраф НЕ начисляем — испортить кадр может кто-то НА
            # ПУТИ, а не сам сосед, и наказание позволило бы такому атакующему
            # ссорить нас с честными пирами и дробить сеть.
            pass
        except OSError:
            pass                    # таймаут или разрыв — просто закрываем
        finally:
            with self._conns_lock:
                self._conns.discard(conn)
            self._release_host_slot(host)
            self._inbound.release()

    def start(self):
        self._stopping = False
        thread = threading.Thread(target=self._serve, daemon=True)
        thread.start()
        return thread

    def stop(self):
        self._stopping = True
        # Сохраняем соседей до закрытия сокета: после перезапуска узел должен
        # знать, к кому идти, а не начинать с пустой таблицы.
        self.save_peers()
        self._running = False
        self._discovery_running = False
        # Постоянные соединения нужно рвать явно. Закрытия слушающего сокета
        # мало: уже принятые соединения живут отдельно от него, и узел
        # продолжал бы обслуживать по ним запросы — то есть выглядел бы живым
        # для всех, кто успел с ним соединиться.
        with self._conns_lock:
            conns, self._conns = list(self._conns), set()
            self._per_host = {}
        for conn in conns:
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self._close(conn)
        with self._pool_lock:
            self._pool_open = False
        self.close_pool()
        server, self._server = self._server, None
        if server is not None:
            # Разбудить поток, висящий в accept(), умеет только сам транспорт:
            # у TCP для этого нужен shutdown(), у других — своё. Одного close()
            # не хватает, и «остановленный» узел продолжал бы отвечать всем,
            # кто успеет соединиться.
            self.transport.close_listener(server)

    # --- Долговременный ключ узла и закрепление ключей соседей -------------
    def _load_identity(self):
        """Ключ узла с диска, а если файла нет — новый (и сохраняем).

        Ключ обязан переживать перезапуск: соседи закрепляют его за нашим
        адресом, и новый ключ после каждого старта выглядел бы для них ровно
        как подмена узла.
        """
        from .wallet import Wallet, generate_wallet

        path = self.identity_file
        if path:
            try:
                with open(path, encoding="utf-8") as handle:
                    stored = json.load(handle)
                return Wallet.from_private_hex(stored["private_key"])
            except (OSError, ValueError, KeyError):
                pass                      # нет файла или он испорчен — заведём новый
        identity = generate_wallet()
        if path:
            self._save_identity(identity, path)
        return identity

    @staticmethod
    def _save_identity(identity, path) -> bool:
        """Пишет ключ узла атомарно и только для владельца (0600)."""
        temporary = f"{path}.tmp"
        try:
            with open(temporary, "w", encoding="utf-8") as handle:
                json.dump({"private_key": identity.private_key_hex}, handle)
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
            return True
        except OSError:
            return False

    @property
    def node_key(self) -> str:
        """Публичный ключ узла (hex) — то, что соседи закрепляют за нами."""
        return self.identity.public_key_hex

    def pinned_key(self, host, port):
        """Ключ, закреплённый за пиром при первом успешном соединении."""
        with self._pool_lock:
            return self._pins.get((host, port))

    def pin_peer(self, host, port, key) -> None:
        """Запоминает ключ пира (TOFU). Первый ключ не перезаписывается."""
        if not key:
            return
        with self._pool_lock:
            self._pins.setdefault((host, port), key)

    # --- Клиент: пул постоянных соединений --------------------------------
    def _checkout(self, host, port):
        """Забирает из пула живое соединение к пиру (или None).

        Соединение отдаётся ИСКЛЮЧИТЕЛЬНО одному вызывающему: рассылка идёт из
        32 потоков, и два потока не могут читать/писать в один сокет — ответы
        перепутались бы между запросами.
        """
        deadline = time.time() - POOL_IDLE_TIMEOUT
        with self._pool_lock:
            idle = self._pool.get((host, port))
            while idle:
                sock, session, last_used = idle.pop()
                if last_used < deadline:
                    self._close(sock)     # залежался — сосед его уже закрыл
                    self._pooled -= 1
                    continue
                self._pooled -= 1
                return sock, session
            self._pool.pop((host, port), None)
        return None

    def _checkin(self, host, port, sock, session=None) -> None:
        """Возвращает соединение (вместе с его сессией) в пул."""
        with self._pool_lock:
            if not self._pool_open or self._pooled >= MAX_POOLED_CONNECTIONS:
                self._close(sock)         # пул полон — дешевле закрыть
                return
            idle = self._pool.setdefault((host, port), [])
            if len(idle) >= MAX_POOLED_PER_PEER:
                self._close(sock)
                return
            # Сессия неотделима от сокета: у неё счётчики кадров и ключи именно
            # этого соединения, к другому сокету её не приложить.
            idle.append((sock, session, time.time()))
            self._pooled += 1

    @staticmethod
    def _close(sock) -> None:
        try:
            sock.close()
        except OSError:
            pass

    def close_pool(self, host=None) -> int:
        """Закрывает пул целиком или все соединения к одному хосту."""
        with self._pool_lock:
            keys = [k for k in self._pool if host is None or k[0] == host]
            closed = 0
            for key in keys:
                for sock, _session, _ts in self._pool.pop(key, []):
                    self._close(sock)
                    closed += 1
            self._pooled = max(0, self._pooled - closed)
        return closed

    def _exchange(self, sock, payload: bytes, session=None):
        """Один запрос-ответ по готовому соединению.

        Возвращает распарсенный ответ, None — если пир закрыл соединение, не
        ответив (для соединения из пула это норма: сосед мог закрыть его по
        своему таймауту, пока мы молчали).
        """
        sock.settimeout(self.peer_timeout)
        send_message(sock, session.encrypt(payload) if session else payload)
        raw = recv_message(sock)
        if not raw:
            return None
        if session is not None:
            raw = session.decrypt(raw)
        return json.loads(raw.decode("utf-8"))

    def send(self, host, port, message: dict) -> dict:
        """Запрос к пиру с переиспользованием соединения.

        Сначала пробуем сокет из пула, и ТОЛЬКО при неудаче открываем новый.
        Повтор здесь обязателен: между нашими запросами сосед мог закрыть
        соединение по своему таймауту, и без второй попытки такое штатное
        закрытие выглядело бы как «пир недоступен». Повтор ровно один — на
        свежем соединении ошибка настоящая, и прятать её нельзя.

        ⚠️ Повтор безопасен только потому, что ПОВТОРНАЯ ДОСТАВКА безвредна:
        сосед мог успеть обработать первую попытку и умереть до ответа. Запросы
        (`get_*`, `hello`) — чтение, а `transaction`, `block` и `inv` сосед
        дедуплицирует сам по txid/хешу. Неидемпотентное сообщение здесь
        добавлять нельзя — его выполнят дважды.
        """
        payload = self._json(message)
        pooled = self._checkout(host, port)
        if pooled is not None:
            sock, session = pooled
            try:
                response = self._exchange(sock, payload, session)
            except (OSError, ValueError):
                response = None
            if response is not None:
                self._checkin(host, port, sock, session)
                return response
            self._close(sock)

        sock = self.transport.connect(host, port, self.peer_timeout)
        try:
            session = self._start_session(sock, host, port)
            response = self._exchange(sock, payload, session)
        except BaseException:
            self._close(sock)
            raise
        if response is None:
            self._close(sock)             # пир промолчал — сокет непригоден
            return {}
        self._checkin(host, port, sock, session)
        return response

    def _start_session(self, sock, host, port):
        """Шифрует свежее соединение (или оставляет открытым, если encrypt=False).

        Отката на открытый текст при неудаче НЕТ: молчаливое понижение — ровно
        то, чего добивается активный атакующий (испортил рукопожатие и читает
        дальше). Ошибка рукопожатия — это отказ соединения.
        """
        if not self.encrypt:
            return None
        session = secure.client_handshake(sock, expect_key=self.pinned_key(host, port))
        # Доверие при первом контакте: первый ключ запоминаем, дальше требуем
        # именно его (сверка — внутри рукопожатия, до вывода ключей сессии).
        self.pin_peer(host, port, session.peer_key)
        return session

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
        # И рвём постоянные соединения к нему: бан, при котором мы продолжаем
        # разговаривать по уже открытому сокету, — не бан.
        self.close_pool(host)

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
        # Закреплённые ключи соседей сохраняются вместе с адресами: иначе после
        # перезапуска каждое соединение снова было бы «первым контактом», и
        # подмена узла опять проходила бы незамеченной.
        with self._pool_lock:
            pins = {f"{h}:{p}": key for (h, p), key in self._pins.items()}
        # REST-адреса соседей тоже: иначе после перезапуска кошельки снова
        # знали бы ровно один узел — тот, что вписан руками.
        with self._peers_lock:
            api = {f"{h}:{p}": [port, tls]
                   for (h, p), (port, tls) in self._peer_api.items()}
        payload = {"peers": [list(peer) for peer in self.peer_list()],
                   "pins": pins, "api": api}
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
        for entry, key in (stored.get("pins") or {}).items():
            host, _, port = str(entry).rpartition(":")
            try:
                self.pin_peer(host, int(port), str(key))
            except (TypeError, ValueError):
                continue                  # мусор в закреплениях просто пропускаем
        for entry, value in (stored.get("api") or {}).items():
            host, _, port = str(entry).rpartition(":")
            try:
                self.remember_api(host, int(port),
                                  {"api": value[0],
                                   "api_tls": value[1] if len(value) > 1 else False})
            except (TypeError, ValueError, IndexError):
                continue
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
                # И где у них REST: вход в сеть по seed обязан давать кошельку
                # тот же список, что и знакомство через connect().
                self.remember_api(peer[0], peer[1], resp)
                self._absorb_peer_api(resp)
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
                **self.api_payload(), **self.network_id()}

    def _say_hello(self, peers):
        """Представляется каждому пиру (параллельно), чтобы сеть стала мешем.

        Кто ответил из чужой сети — из таблицы убирается.
        """
        for peer, resp in self._fanout(
                peers, lambda host, port: self.send(host, port,
                                                    self._hello_message())):
            if not self.same_network(resp):
                self.remove_peer(*peer)
                continue
            # Свой REST-адрес сосед прислал в ответе — запоминаем его и те,
            # что он знает про других.
            self.remember_api(peer[0], peer[1], resp)
            self._absorb_peer_api(resp)

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
        self.remember_api(host, port, resp)
        peers = self._accept_peers(resp, source=host)
        self._absorb_peer_api(resp)
        self._say_hello(peers)
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
            # Вместе с адресами разносятся и REST-адреса: кошелёк, спросивший
            # ЛЮБОЙ узел сети, получит список всех, а не только соседей входа.
            self.remember_api(_peer[0], _peer[1], resp)
            self._absorb_peer_api(resp)
        self._say_hello(fresh)

    # --- Авто-поиск в локальной сети (UDP-маяки, WiFi/LAN без интернета) ---
    def start_discovery(self, interval: int = 5):
        """Запустить рассылку и приём UDP-маяков для авто-поиска узлов в сети.

        Маяки — свойство IP-сети, а не протокола B-hydra: широковещания нет ни
        у Bluetooth, ни у соединения «точка-точка». Транспорт, который так не
        умеет, честно отвечает отказом вместо того, чтобы поднимать потоки,
        которые всё равно ничего не найдут (у него для поиска соседей свои
        средства — например, обзор устройств Bluetooth).
        """
        if not self.transport.supports_discovery:
            return False
        if self._discovery_running:
            return False
        self._discovery_running = True
        threading.Thread(target=self._discovery_listen, daemon=True).start()
        threading.Thread(target=self._discovery_announce, args=(interval,),
                         daemon=True).start()
        return True

    def stop_discovery(self):
        self._discovery_running = False

    def discover_nearby(self) -> int:
        """Соседи, которых транспорт видит САМ (осмотр Bluetooth и подобное).

        Тот же смысл, что у UDP-маяка, только средство другое: у TCP соседей
        приносит широковещание, у Bluetooth — обзор устройств вокруг. Поэтому
        метод общий и работает с любым транспортом, который умеет
        `neighbours()`; кто не умеет, возвращает пустой список, и вызов просто
        ничего не делает.

        Возвращает, сколько соседей НАШЕЙ сети добавилось. Чужие устройства
        (наушники, телефоны) отсеиваются сами: `connect` требует совпадения
        генезиса, и всё, что не наш узел, из таблицы вылетает.
        """
        added = 0
        for host, port in self.transport.neighbours():
            if (host, port) == (self.host, self.port):
                continue
            if not self.add_peer(host, port):
                continue              # уже знаем или таблица полна
            try:
                if not self.connect(host, port):
                    self.remove_peer(host, port)
                    continue
            except OSError:
                self.remove_peer(host, port)   # не отозвался — не сосед
                continue
            added += 1
            if self.on_discover:
                try:
                    self.on_discover(host, port)
                except Exception:
                    pass
        return added

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
        """Добавляет транзакцию локально и анонсирует её по сети."""
        accepted = self.node.add_transaction(tx)
        if accepted:
            with self._seen_lock:
                self.seen_tx.add(tx.txid)
            # Как и блок: рассылаем txid, тело сосед запросит сам.
            self._announce_tx(tx.txid, background=False)
        return accepted

    def mine(self, miner_address, message=None, wallet=None, on_progress=None):
        """Майнит блок и распространяет его по сети.

        `message` — заметка майнера, которая останется в блоке навсегда;
        `wallet` — ключ майнера, которым эта заметка подписывается.

        Майнинг БРОСАЕТСЯ, если сосед прислал блок раньше нас: наш родитель
        устарел, и всё, что мы намолотим дальше, сеть отвергнет. Раньше выйти
        из перебора было нечем — узел добивал заведомо мёртвый блок, а потом
        сам же его и отклонял. Возвращает None, если блок брошен.
        """
        parent = self.node.blockchain.last_block.hash
        block = self.node.mine_pending(miner_address, message=message,
                                       wallet=wallet, on_progress=on_progress,
                                       should_stop=lambda: (
                                           self.node.blockchain.last_block.hash
                                           != parent))
        if block is None:
            return None
        with self._seen_lock:
            self.seen_blocks.add(block.hash)
        # Рассылаем ХЕШ, а не тело: соседи сами попросят блок, если он им
        # нужен. Анонс уходит синхронно, докачка у соседей — асинхронно.
        self._announce_block(block.hash, background=False)
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

    # --- Приём и распространение блоков -----------------------------------
    def _process_block(self, block_dict, origin=None, host=None) -> bool:
        """Разбирает полученный блок; принятый анонсируется дальше по сети."""
        bhash = block_dict.get("hash")
        with self._seen_lock:
            already_seen = bhash in self.seen_blocks
        if already_seen:
            # Повтор уже обработанного блока. Раньше здесь был молчаливый
            # выход — и если блок отвергли из-за отставания, повторные анонсы
            # узел игнорировал, оставаясь позади до ближайшего sync.
            if origin and self._block_is_ahead(block_dict):
                self._sync_from_throttled(origin)
            return False

        accepted = self.node.receive_block(block_dict)
        if accepted:
            with self._seen_lock:
                self.seen_blocks.add(bhash)
            self._announce_block(bhash, exclude=origin)
        else:
            # Виденным помечаем только то, что нам и правда не подходит:
            # бракованный блок или чужую развилку. Блок «из будущего» (мы
            # просто отстали) НЕ помечаем — он ещё пригодится.
            if not self._block_is_ahead(block_dict):
                with self._seen_lock:
                    self.seen_blocks.add(bhash)
                self._penalise(host, PENALTY_INVALID_BLOCK)
            if origin:
                self._sync_from_throttled(origin)
        return accepted

    def _announce_block(self, block_hash, exclude=None, background=True):
        """Рассылает соседям ХЕШ блока — тело они запросят сами, если нужно."""
        self._gossip({"type": "inv", "kind": "block", "hash": block_hash,
                      "from": [self.host, self.port]},
                     exclude=exclude, background=background)

    # --- Приём и распространение транзакций -------------------------------
    def _process_tx(self, tx_dict, origin=None) -> bool:
        """Разбирает полученную транзакцию; принятая анонсируется дальше."""
        from .transaction import Transaction

        tx = Transaction.from_dict(tx_dict)
        with self._seen_lock:
            first_seen = tx.txid not in self.seen_tx
            self.seen_tx.add(tx.txid)
        accepted = self.node.add_transaction(tx)
        if accepted and first_seen:
            self._announce_tx(tx.txid, exclude=origin)
        return accepted

    def _announce_tx(self, txid, exclude=None, background=True):
        """Рассылает соседям txid — тело они запросят сами, если нужно."""
        self._gossip({"type": "inv", "kind": "tx", "hash": txid,
                      "from": [self.host, self.port]},
                     exclude=exclude, background=background)

    def _start_tx_fetch(self, txid) -> bool:
        """Резервирует место под докачку тела транзакции (дедуп + потолок)."""
        if not self._tx_fetch_slots.acquire(blocking=False):
            return False
        with self._seen_lock:
            if txid in self._inflight_tx:
                self._tx_fetch_slots.release()
                return False
            self._inflight_tx.add(txid)
        return True

    def _fetch_tx(self, origin, txid) -> bool:
        """Запрашивает тело анонсированной транзакции и применяет её."""
        from .transaction import Transaction

        try:
            resp = self.send(origin[0], origin[1],
                             {"type": "get_tx", "txid": txid})
            body = resp.get("transaction")
            if not body:
                return False
            # Пир обязан прислать именно то, что анонсировал. txid считается
            # ОТ СОДЕРЖИМОГО, поэтому сверяем пересчитанный, а не поле в JSON:
            # иначе под видом анонсированного txid пришло бы что угодно.
            if Transaction.from_dict(body).txid != txid:
                return False
            return self._process_tx(body, origin)
        except (OSError, ValueError, KeyError, TypeError):
            return False
        finally:
            with self._seen_lock:
                self._inflight_tx.discard(txid)
            self._tx_fetch_slots.release()

    def _start_fetch(self, block_hash) -> bool:
        """Резервирует место под докачку тела: дедуп по хешу и потолок."""
        if not self._fetch_slots.acquire(blocking=False):
            return False
        with self._seen_lock:
            if block_hash in self._inflight:
                self._fetch_slots.release()
                return False
            self._inflight.add(block_hash)
        return True

    def _fetch_block(self, origin, block_hash) -> bool:
        """Запрашивает тело анонсированного блока и применяет его."""
        try:
            resp = self.send(origin[0], origin[1],
                             {"type": "get_block", "hash": block_hash})
            body = resp.get("block")
            # Пир обязан прислать именно то, что анонсировал.
            if not body or body.get("hash") != block_hash:
                return False
            return self._process_block(body, origin, origin[0])
        except (OSError, ValueError):
            return False
        finally:
            with self._seen_lock:
                self._inflight.discard(block_hash)
            self._fetch_slots.release()

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
    parser.add_argument("--identity-file", default=DEFAULT_IDENTITY_FILE,
                        help="файл с долговременным ключом УЗЛА (не кошелька)")
    parser.add_argument("--no-encrypt", action="store_true",
                        help="не шифровать исходящие соединения")
    parser.add_argument("--require-encryption", action="store_true",
                        help="не обслуживать открытые (нешифрованные) соединения")
    parser.add_argument("--difficulty", type=int, default=3, help="базовая сложность")
    parser.add_argument("--demo", action="store_true", help="запустить демо из 3 узлов")
    parser.add_argument("--transport", choices=("tcp", "bluetooth"), default="tcp",
                        help="чем связываться: tcp (по умолчанию) или bluetooth "
                             "(D2D без роутера; --port здесь канал RFCOMM)")
    args = parser.parse_args()

    if args.demo or not args.port:
        _demo()
        return

    # Seed-узлы: из --seed и из переменной окружения BHYDRA_SEEDS
    # ("host:port,host:port") — так их удобно задавать в докере/сервисе.
    seeds = parse_seeds(list(args.seed)
                        + os.environ.get("BHYDRA_SEEDS", "").split(","))

    transport = None
    listen_host = "0.0.0.0"
    if args.transport == "bluetooth":
        from .transport import bluetooth_transport

        # Транспорт выбирается по системе: на Linux сокеты берутся из stdlib,
        # на Windows их там нет вовсе и работает нативная библиотека.
        transport = bluetooth_transport()
        if not type(transport).available():
            print("Bluetooth недоступен: нет адаптера, библиотеки или "
                  "поддержки в системе.")
            return
        # Свой адрес узнаём у нативного слоя: пирам мы должны называть MAC, а
        # не "0.0.0.0" — по нему они будут звонить обратно.
        listen_host = transport.adapter().get("address") or "0.0.0.0"

    node = P2PNode(listen_host, args.port, BHydraNode(difficulty=args.difficulty),
                   peers_file=args.peers_file, seeds=seeds,
                   encrypt=not args.no_encrypt,
                   require_encryption=args.require_encryption,
                   identity_file=args.identity_file, transport=transport)
    node.start()
    if args.transport == "bluetooth":
        found = node.discover_nearby()   # осмотр вокруг вместо UDP-маяка
        print(f"Осмотр Bluetooth: узлов B-hydra рядом — {found}")
    # Соседи с прошлого запуска + seed-узлы: узел находит сеть сам, без --peer.
    alive = node.bootstrap()
    if args.peer:
        host, _, port = args.peer.rpartition(":")
        node.connect(host, int(port))
    channel = "шифрован" if node.encrypt else "ОТКРЫТ"
    print(f"Транспорт: {node.transport.name} ({node.host})")
    print(f"P2P-узел B-hydra на :{args.port} | пиров: {len(node.peers)} "
          f"(живых при старте: {alive}) | высота: {node.node.height}"
          f" | канал: {channel} | ключ узла: {node.node_key[:16]}…"
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
