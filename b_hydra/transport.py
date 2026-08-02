"""
transport.py — чем узлы B-hydra дотягиваются друг до друга.

Весь протокол выше сокета транспорта НЕ ЗНАЕТ: `tcp.py` пользуется только
`sendall`/`recv`, `secure.py` — ими же плюс таймаутами, а сообщения, gossip и
синхронизация видят уже готовые словари. Значит, чтобы пустить B-hydra поверх
чего-то кроме TCP/IP, переписывать протокол не нужно — нужно подменить четыре
места, где сокет создаётся. Они и собраны здесь.

Что обязан уметь транспорт:

    listen(port)              → объект, принимающий входящие
    accept(server)            → (соединение, хост) — хост нужен для банов
    connect(host, port, t)    → соединение (байтовый поток)

Больше ничего. Любой байтовый поток подходит: Bluetooth RFCOMM — поток,
Unix-сокет — поток, TCP — поток. Адрес везде пара «хост + номер»: для TCP это
IP и порт, для Bluetooth это MAC и канал, — поэтому `send(host, port)` в
`p2p.py` менять не пришлось.

⚠️ Маяки (UDP-широковещание) — НЕ часть этого интерфейса. Дешёвый способ
крикнуть «я тут» есть не у каждого транспорта: у Bluetooth вместо этого свой
поиск устройств. Поэтому у транспорта есть флаг `supports_discovery`, и узел
просто не включает маяки там, где их нет, вместо того чтобы делать вид.
"""

import json
import os
import queue
import socket
import subprocess
import threading


class Transport:
    """Интерфейс транспорта. Сам по себе не работает — см. TCPTransport."""

    #: Короткое имя для логов и диагностики.
    name = "?"
    #: Умеет ли транспорт широковещательные маяки (авто-поиск соседей).
    supports_discovery = False

    def listen(self, port):
        """Открывает приём входящих соединений. Возвращает объект с close()."""
        raise NotImplementedError

    def accept(self, server):
        """Ждёт соединение. Возвращает (соединение, хост-для-репутации).

        При закрытии server обязан бросить OSError — на этом узел завершает
        поток приёма.
        """
        raise NotImplementedError

    def connect(self, host, port, timeout):
        """Открывает соединение к пиру. Возвращает байтовый поток."""
        raise NotImplementedError

    def close_listener(self, server):
        """Гасит приём и БУДИТ поток, висящий в accept().

        Отдельный метод, а не просто `server.close()`: у TCP закрытия мало —
        поток в accept() от него не просыпается, и «остановленный» узел
        продолжает отвечать всем, кто успел соединиться. Как именно будить —
        знает только сам транспорт.
        """
        server.close()

    def neighbours(self):
        """Соседи, которых транспорт видит САМ, без чужой подсказки.

        Пусто по умолчанию: у TCP такого способа нет — там адрес узнают из
        UDP-маяка, от seed-узла или руками. А у Bluetooth есть: он умеет
        осмотреться вокруг. Возвращает список пар (хост, номер), готовых для
        `P2PNode.connect`.
        """
        return []


class TCPTransport(Transport):
    """Обычный TCP/IP — то, на чём сеть работала всегда."""

    name = "tcp"
    supports_discovery = True          # UDP-широковещание в локальной сети

    def listen(self, port):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # Слушаем на ВСЕХ интерфейсах (0.0.0.0): тогда узел доступен и по
        # localhost, и по IP в локальной сети — другой компьютер сможет
        # подключиться. Адрес «для представления» узел хранит отдельно.
        server.bind(("0.0.0.0", port))
        server.listen(8)
        return server

    def accept(self, server):
        conn, addr = server.accept()
        # Хост без порта: у входящего соединения порт эфемерный, и репутация
        # с лимитами считаются именно по хосту.
        return conn, addr[0]

    def connect(self, host, port, timeout):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.settimeout(timeout)
            sock.connect((host, port))
        except BaseException:
            sock.close()
            raise
        return sock

    def close_listener(self, server):
        try:
            server.shutdown(socket.SHUT_RDWR)   # прерывает accept()
        except OSError:
            pass
        server.close()


#: Канал RFCOMM узла B-hydra — то же самое, что порт 5000 у TCP: обе стороны
#: обязаны знать его заранее. Каналов всего 30, поэтому число небольшое.
BLUETOOTH_CHANNEL = 5

#: Где искать нативный слой (`cpp/bhydra_bt.cpp`). Собирается командой из
#: cpp/README.md; путь можно задать переменной окружения.
BT_BRIDGE_ENV = "BHYDRA_BT_BRIDGE"


class BluetoothTransport(Transport):
    """B-hydra поверх Bluetooth RFCOMM — связь устройств БЕЗ роутера (D2D).

    Само соединение — стандартная библиотека Python: `AF_BLUETOOTH` и
    `BTPROTO_RFCOMM` есть в ней на Linux, а RFCOMM — обычный байтовый поток,
    поэтому кадры, рукопожатие и шифрование идут поверх него без единой правки.
    Адрес пира — пара (MAC, канал) вместо (IP, порт), и `send(host, port)` в
    `p2p.py` этого даже не замечает.

    ПОИСК соседей — другое дело: его в стандартной библиотеке нет вовсе, нужен
    `hci_inquiry` из BlueZ. Он вынесен в нативный слой `cpp/bhydra_bt.cpp` и
    вызывается отсюда. Без собранного слоя транспорт работает — просто соседей
    придётся называть по адресу вручную.

    ⚠️ Скорость RFCOMM — сотни КБ/с, то есть блок в 4 МиБ идёт десятки секунд.
    Bluetooth здесь — запасной путь на случай «роутера нет вообще», а не замена
    TCP/IP.
    """

    name = "bluetooth"
    #: UDP-широковещания у Bluetooth нет — соседи ищутся осмотром (neighbours).
    supports_discovery = False

    def __init__(self, bridge=None, scan_seconds=6):
        self.bridge = bridge or os.environ.get(BT_BRIDGE_ENV) or "bhydra_bt"
        self.scan_seconds = int(scan_seconds)

    @staticmethod
    def available():
        """Есть ли в этой системе Bluetooth-сокеты вообще.

        Константы есть и там, где адаптера нет, поэтому проверяем созданием
        сокета: именно оно отвечает «Address family not supported».
        """
        if not hasattr(socket, "AF_BLUETOOTH"):
            return False
        try:
            probe = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM,
                                  socket.BTPROTO_RFCOMM)
        except (AttributeError, OSError):
            return False
        probe.close()
        return True

    def _socket(self):
        return socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM,
                             socket.BTPROTO_RFCOMM)

    def listen(self, port):
        server = self._socket()
        try:
            # BDADDR_ANY — «на любом адаптере», аналог 0.0.0.0 у TCP.
            server.bind((socket.BDADDR_ANY, int(port)))
            server.listen(8)
        except BaseException:
            server.close()
            raise
        return server

    def accept(self, server):
        conn, addr = server.accept()
        return conn, addr[0]          # MAC пира — по нему считается репутация

    def connect(self, host, port, timeout):
        sock = self._socket()
        try:
            sock.settimeout(timeout)
            sock.connect((host, int(port)))
        except BaseException:
            sock.close()
            raise
        return sock

    def close_listener(self, server):
        try:
            server.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        server.close()

    # --- Нативный слой: то, чего нет в стандартной библиотеке --------------
    def _bridge_json(self, *args):
        """Зовёт `cpp/bhydra_bt`. Возвращает {} при любой беде.

        Беда здесь — норма: слой могли не собрать, адаптера может не быть,
        прав может не хватить. Узел обязан всё это пережить и продолжить
        работать по адресам, названным вручную.
        """
        try:
            result = subprocess.run([self.bridge, *[str(a) for a in args]],
                                    capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.SubprocessError):
            return {}
        try:
            answer = json.loads(result.stdout or "{}")
        except ValueError:
            return {}
        return answer if isinstance(answer, dict) else {}

    def adapter(self):
        """Свой адрес и имя (или {}, если адаптера/слоя нет)."""
        answer = self._bridge_json("adapter")
        return {} if "error" in answer else answer

    def scan(self, seconds=None):
        """Устройства поблизости: [{address, name, channel}, …].

        Возвращает ВСЕ видимые устройства — наушники и телефоны в том числе.
        Отличить свои узлы отсюда нельзя, это делает рукопожатие B-hydra, где
        сверяется отпечаток сети. UDP-маяк устроен так же: летит всем, чужие
        отсеиваются по генезису.
        """
        answer = self._bridge_json("scan", seconds or self.scan_seconds)
        devices = answer.get("devices")
        return devices if isinstance(devices, list) else []

    def neighbours(self):
        """Кандидаты для подключения: пары (MAC, канал)."""
        found = []
        for device in self.scan():
            address = device.get("address")
            if not address:
                continue
            channel = device.get("channel") or BLUETOOTH_CHANNEL
            try:
                found.append((str(address), int(channel)))
            except (TypeError, ValueError):
                continue
        return found


class _PairServer:
    """Приёмник для PairTransport: очередь готовых соединений."""

    def __init__(self, transport, port):
        self._transport = transport
        self._port = port
        self._incoming = queue.Queue()
        self._closed = False

    def put(self, conn, host):
        if self._closed:
            raise OSError("приёмник закрыт")
        self._incoming.put((conn, host))

    def accept(self):
        item = self._incoming.get()
        if item is None:                       # сигнал закрытия
            raise OSError("приёмник закрыт")
        return item

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._transport._unregister(self._port)
        # Будим поток, висящий в accept(): без этого «остановленный» узел
        # держал бы поток навсегда.
        self._incoming.put(None)


class PairTransport(Transport):
    """Транспорт БЕЗ СЕТИ: соединения — socketpair внутри одного процесса.

    Нужен, чтобы доказать, что протокол действительно не завязан на TCP/IP.
    Здесь нет ни IP, ни портов ОС, ни маршрутизации — `socket.socketpair()`
    даёт пару AF_UNIX, то есть просто два конца байтового потока. Если поверх
    него работают рукопожатие, шифрование, gossip и синхронизация — значит,
    абстракция настоящая, а не декоративная.

    Это же и образец для Bluetooth RFCOMM: там ровно так же — поток и пара
    «адрес, канал» вместо «IP, порт».
    """

    name = "pair"
    supports_discovery = False         # крикнуть «я тут» тут некому

    def __init__(self):
        self._servers = {}
        self._lock = threading.Lock()

    def listen(self, port):
        with self._lock:
            if port in self._servers:
                raise OSError(f"порт {port} уже занят")
            server = _PairServer(self, port)
            self._servers[port] = server
            return server

    def accept(self, server):
        return server.accept()

    def connect(self, host, port, timeout):
        with self._lock:
            server = self._servers.get(port)
        if server is None:
            raise OSError(f"некому отвечать на {host}:{port}")
        ours, theirs = socket.socketpair()
        ours.settimeout(timeout)
        try:
            server.put(theirs, host)
        except OSError:
            ours.close()
            theirs.close()
            raise
        return ours

    def _unregister(self, port):
        with self._lock:
            self._servers.pop(port, None)
