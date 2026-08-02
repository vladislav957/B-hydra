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

import queue
import socket
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
