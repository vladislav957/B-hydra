"""Bluetooth-транспорт: D2D без роутера. Python — соединение, C++ — поиск.

Разделение не случайное. Соединение по RFCOMM Python умеет сам (`AF_BLUETOOTH`,
`BTPROTO_RFCOMM` есть в стандартной библиотеке на Linux), а RFCOMM — обычный
байтовый поток, поэтому кадры и шифрование идут поверх него без правок.
А вот ПОИСКА устройств поблизости в стандартной библиотеке нет вовсе — нужен
`hci_inquiry` из BlueZ, то есть C. Он и вынесен в `cpp/bhydra_bt.cpp`.

⚠️ Главное ограничение этих тестов: **радиоканала здесь нет**. В контейнере нет
адаптера Bluetooth, поэтому проверяется всё, кроме самой передачи по воздуху:
сборка нативного слоя и его контрольные векторы, разбор ответов, поведение при
ОТСУТСТВИИ железа (узел обязан это пережить), устройство транспорта и то, что
сеть поверх него собирается. Живую передачу проверит только владелец двух
машин с Bluetooth — тесты с адаптером сами включатся, когда он появится.
"""

import json
import os
import shutil
import socket
import subprocess

import pytest

from b_hydra.node import BHydraNode
from b_hydra.p2p import P2PNode
from b_hydra.transport import (BLUETOOTH_CHANNEL, BluetoothTransport,
                               PairTransport, TCPTransport)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(ROOT, "cpp", "bhydra_bt.cpp")

COMPILER = None
for candidate in ("g++", "clang++"):
    if shutil.which(candidate):
        COMPILER = candidate
        break

HAS_BLUEZ = os.path.exists("/usr/include/bluetooth/bluetooth.h")
HAS_ADAPTER = BluetoothTransport.available()


@pytest.fixture(scope="module")
def bridge(tmp_path_factory):
    """Собирает нативный слой. Пропуск, если нет компилятора или заголовков."""
    if COMPILER is None:
        pytest.skip("нет компилятора C++")
    if not HAS_BLUEZ:
        pytest.skip("нет заголовков BlueZ (libbluetooth-dev)")
    binary = str(tmp_path_factory.mktemp("bt") / "bhydra_bt")
    result = subprocess.run(
        [COMPILER, "-O2", "-std=c++17", "-Wall", "-Wextra", "-o", binary,
         SOURCE, "-lbluetooth"],
        capture_output=True, text=True, timeout=300)
    assert result.returncode == 0, result.stderr
    # Предупреждения компилятора — тоже результат: в этом слое их быть не должно.
    assert result.stderr.strip() == "", result.stderr
    return binary


def _run(bridge_path, *args):
    result = subprocess.run([bridge_path, *[str(a) for a in args]],
                            capture_output=True, text=True, timeout=90)
    return result, json.loads(result.stdout or "{}")


# --- Нативный слой ------------------------------------------------------------
def test_native_layer_passes_its_own_vectors(bridge):
    """Проверяет то, что можно проверить без железа: разбор адресов и JSON.

    «Собралось» ничего не доказывает — это правило проекта. Здесь сверяется
    порядок байтов bdaddr (адрес хранится задом наперёд) и экранирование,
    то есть что мы работаем с настоящими структурами BlueZ.
    """
    result, answer = _run(bridge, "selftest")
    assert result.returncode == 0, result.stdout + result.stderr
    assert answer["ok"] is True
    assert answer["bdaddr_size"] == 6          # MAC — ровно 6 байт
    assert answer["channel"] == BLUETOOTH_CHANNEL


def test_native_layer_agrees_with_python_about_the_channel(bridge):
    """Канал RFCOMM — как порт: обе стороны обязаны знать одно число.

    Разъедься эти константы — узлы слушали бы и звонили на разные каналы и
    никогда бы не встретились, причём молча.
    """
    _result, answer = _run(bridge, "selftest")
    assert answer["channel"] == BLUETOOTH_CHANNEL


@pytest.mark.skipif(HAS_ADAPTER, reason="адаптер есть — проверяем обратное")
def test_native_layer_survives_a_missing_adapter(bridge):
    """Без адаптера — понятная ошибка в JSON, а не падение.

    Узел с Bluetooth-транспортом запускают и там, где железа нет; он обязан
    это пережить и работать по адресам, названным вручную.
    """
    for command in ("adapter", "scan 1"):
        result, answer = _run(bridge, *command.split())
        assert result.returncode == 1
        assert "error" in answer and answer["error"]
        assert result.stdout.strip().startswith("{")   # всегда валидный JSON


def test_unknown_command_is_refused(bridge):
    result = subprocess.run([bridge, "выдумка"], capture_output=True, text=True)
    assert result.returncode == 2


# --- Транспорт на стороне Python ----------------------------------------------
def test_bluetooth_transport_is_wired_but_not_default():
    """TCP остаётся транспортом по умолчанию — Bluetooth только по просьбе."""
    node = P2PNode("127.0.0.1", 5000, node=BHydraNode(difficulty=1))
    assert isinstance(node.transport, TCPTransport)

    bt = BluetoothTransport()
    assert bt.name == "bluetooth"
    # UDP-широковещания у Bluetooth нет: соседи ищутся осмотром, не маяком.
    assert bt.supports_discovery is False


def test_transport_degrades_without_the_native_layer():
    """Слой не собран — транспорт всё равно годен.

    Поиск соседей отвалится, а соединение по названному адресу должно
    остаться: иначе несобранный C++ ломал бы то, что от него не зависит.
    """
    bt = BluetoothTransport(bridge="/несуществующий/bhydra_bt")
    assert bt.adapter() == {}
    assert bt.scan() == []
    assert bt.neighbours() == []


def test_transport_ignores_junk_from_the_native_layer(tmp_path):
    """Мусор вместо JSON не должен ронять узел."""
    fake = tmp_path / "fake"
    fake.write_text("#!/bin/sh\necho не json\n", encoding="utf-8")
    fake.chmod(0o755)
    bt = BluetoothTransport(bridge=str(fake))
    assert bt.scan() == []
    assert bt.neighbours() == []


def test_neighbours_reads_the_native_layer_answer(tmp_path):
    """Ответ слоя превращается в пары (адрес, канал) для connect."""
    fake = tmp_path / "fake"
    fake.write_text(
        '#!/bin/sh\n'
        'echo \'{"devices": [{"address": "11:22:33:44:55:66", "name": "узел",'
        ' "channel": 5}, {"address": "AA:BB:CC:DD:EE:FF", "name": ""}]}\'\n',
        encoding="utf-8")
    fake.chmod(0o755)
    bt = BluetoothTransport(bridge=str(fake))
    assert bt.neighbours() == [("11:22:33:44:55:66", 5),
                               ("AA:BB:CC:DD:EE:FF", BLUETOOTH_CHANNEL)]


def test_neighbours_skips_broken_entries(tmp_path):
    """Устройство без адреса или с мусорным каналом просто пропускаем."""
    fake = tmp_path / "fake"
    fake.write_text(
        '#!/bin/sh\n'
        'echo \'{"devices": [{"name": "без адреса"},'
        ' {"address": "11:22:33:44:55:66", "channel": "канал"},'
        ' {"address": "AA:BB:CC:DD:EE:FF", "channel": 7}]}\'\n',
        encoding="utf-8")
    fake.chmod(0o755)
    bt = BluetoothTransport(bridge=str(fake))
    assert bt.neighbours() == [("AA:BB:CC:DD:EE:FF", 7)]


def test_available_tells_the_truth_about_this_machine():
    """`available()` обязан отвечать по факту, а не по наличию констант.

    Константы `AF_BLUETOOTH` есть и там, где адаптера нет вовсе, — проверять
    надо созданием сокета.
    """
    assert BluetoothTransport.available() is HAS_ADAPTER


# --- Поиск соседей узлом ------------------------------------------------------
def test_discover_nearby_works_with_any_transport():
    """`discover_nearby` — общий метод, а не «блютус-метод».

    У TCP своего осмотра нет (там маяк и seed), поэтому список пуст и вызов
    ничего не делает — но и не падает.
    """
    node = P2PNode("127.0.0.1", 5000, node=BHydraNode(difficulty=1))
    assert node.transport.neighbours() == []
    assert node.discover_nearby() == 0


def test_discover_nearby_connects_to_what_the_transport_found():
    """Найденное транспортом превращается в настоящее знакомство.

    Радио здесь подменено PairTransport — проверяется именно логика: осмотр →
    add_peer → connect → сосед в таблице.
    """
    wire = PairTransport()

    class Seeing(PairTransport):
        """Транспорт, который «видит» соседа — как Bluetooth видит устройство."""

        def __init__(self, shared, found):
            self._shared = shared
            self._found = found

        def listen(self, port):
            return self._shared.listen(port)

        def accept(self, server):
            return self._shared.accept(server)

        def connect(self, host, port, timeout):
            return self._shared.connect(host, port, timeout)

        def neighbours(self):
            return self._found

    a = P2PNode("узел-А", 1, node=BHydraNode(difficulty=1), transport=wire)
    a.start()
    b = P2PNode("узел-Б", 2, node=BHydraNode(difficulty=1),
                transport=Seeing(wire, [("узел-А", 1)]))
    b.start()
    try:
        assert b.discover_nearby() == 1
        assert ("узел-А", 1) in b.peer_list()
        # Повторный осмотр не плодит дублей.
        assert b.discover_nearby() == 0
    finally:
        a.stop()
        b.stop()


def test_discover_nearby_drops_strangers():
    """Наушники и телефоны в ответе осмотра — норма, соседями они не станут.

    Bluetooth-осмотр возвращает ВСЁ, что видно вокруг. Отсеивает их
    рукопожатие: не отозвался на нашем канале — вон из таблицы.
    """
    wire = PairTransport()

    class Seeing(PairTransport):
        def __init__(self, shared, found):
            self._shared = shared
            self._found = found

        def listen(self, port):
            return self._shared.listen(port)

        def accept(self, server):
            return self._shared.accept(server)

        def connect(self, host, port, timeout):
            return self._shared.connect(host, port, timeout)

        def neighbours(self):
            return self._found

    node = P2PNode("узел-Б", 2, node=BHydraNode(difficulty=1),
                   transport=Seeing(wire, [("чужие-наушники", 99)]))
    node.start()
    try:
        assert node.discover_nearby() == 0
        assert ("чужие-наушники", 99) not in node.peer_list()
    finally:
        node.stop()


# --- С живым адаптером (сами включатся, когда он появится) --------------------
@pytest.mark.skipif(not HAS_ADAPTER, reason="нет адаптера Bluetooth")
def test_listener_opens_on_a_real_adapter():
    """На машине с Bluetooth узел обязан открыть приём на своём канале."""
    bt = BluetoothTransport()
    server = bt.listen(BLUETOOTH_CHANNEL)
    try:
        assert server.family == socket.AF_BLUETOOTH
    finally:
        bt.close_listener(server)


@pytest.mark.skipif(not HAS_ADAPTER, reason="нет адаптера Bluetooth")
def test_adapter_reports_its_own_address(bridge):
    bt = BluetoothTransport(bridge=bridge)
    info = bt.adapter()
    assert info.get("address", "").count(":") == 5
    assert info.get("channel") == BLUETOOTH_CHANNEL


# --- Windows: сборка mingw + запуск под Wine ----------------------------------
WIN_SOURCE = os.path.join(ROOT, "cpp", "bhydra_bt_win.cpp")
MINGW = shutil.which("x86_64-w64-mingw32-g++")
WINE = next((p for p in ("/usr/lib/wine/wine64", "/usr/lib/wine/wine")
             if os.path.exists(p)), None) or shutil.which("wine64") \
    or shutil.which("wine")


@pytest.fixture(scope="module")
def windows_build(tmp_path_factory):
    """Кросс-сборка .exe и .dll. Пропуск, если нет mingw-w64."""
    if MINGW is None:
        pytest.skip("нет кросс-компилятора mingw-w64")
    out = tmp_path_factory.mktemp("win")
    exe, dll = str(out / "bhydra_bt.exe"), str(out / "bhydra_bt.dll")
    common = [MINGW, "-O2", "-std=c++17", "-Wall", "-Wextra", "-static"]
    for command in ([*common, "-o", exe, WIN_SOURCE, "-lws2_32", "-lbthprops"],
                    [*common, "-shared", "-DBHYDRA_BT_DLL", "-o", dll,
                     WIN_SOURCE, "-lws2_32", "-lbthprops"]):
        result = subprocess.run(command, capture_output=True, text=True,
                                timeout=600)
        assert result.returncode == 0, result.stderr
        # Предупреждений быть не должно ни в одной из двух сборок: код
        # командной строки в DLL не попадает и наоборот.
        assert result.stderr.strip() == "", result.stderr
    return exe, dll


def test_windows_build_produces_a_pe_binary_and_a_dll(windows_build):
    """Собирается именно ВИНДОВЫЙ бинарник, а не «что-то собралось»."""
    exe, dll = windows_build
    for path, marker in ((exe, b"PE\0\0"), (dll, b"PE\0\0")):
        with open(path, "rb") as handle:
            head = handle.read(0x200)
        assert head[:2] == b"MZ"           # DOS-заголовок
        assert marker in head              # и настоящая PE-часть
    assert os.path.getsize(dll) > 10_000


@pytest.mark.skipif(WINE is None, reason="нет wine — запустить .exe негде")
def test_windows_binary_passes_selftest_under_wine(windows_build, tmp_path):
    """Настоящий .exe запускается и проходит свои векторы.

    Проверяются те части, что не зависят от радио: разбор адресов в обе
    стороны, отказ от мусорных адресов, экранирование JSON и подъём Winsock.
    Это не замена проверке на живой Windows-машине, но и не «оно собралось».
    """
    exe, _dll = windows_build
    environment = dict(os.environ, WINEDEBUG="-all",
                       WINEPREFIX=str(tmp_path / "wine"))
    result = subprocess.run([WINE, exe, "selftest"], capture_output=True,
                            text=True, timeout=600, env=environment)
    assert result.returncode == 0, result.stdout + result.stderr
    answer = json.loads(result.stdout)
    assert answer["ok"] is True
    assert answer["channel"] == BLUETOOTH_CHANNEL
    assert answer["af_bth"] == 32               # AF_BTH из ws2bth.h
    assert answer["sockaddr_bth_size"] == 30    # USHORT+ULONGLONG+GUID+ULONG


@pytest.mark.skipif(WINE is None, reason="нет wine — запустить .exe негде")
def test_windows_binary_survives_a_missing_adapter(windows_build, tmp_path):
    """Под Wine адаптера нет — и ответом обязан быть JSON с ошибкой."""
    exe, _dll = windows_build
    environment = dict(os.environ, WINEDEBUG="-all",
                       WINEPREFIX=str(tmp_path / "wine"))
    result = subprocess.run([WINE, exe, "adapter"], capture_output=True,
                            text=True, timeout=600, env=environment)
    assert result.returncode == 1
    assert "error" in json.loads(result.stdout)


def test_channel_constant_agrees_in_all_three_places(windows_build):
    """Канал RFCOMM живёт в Python, в Linux-слое и в Windows-слое.

    Три копии одного числа — три возможности разъехаться, а последствие
    молчаливое: узлы слушают и звонят на разные каналы и просто не находят
    друг друга.
    """
    import re

    for path in (SOURCE, WIN_SOURCE):
        text = open(path, encoding="utf-8").read()
        found = re.search(r"kRfcommChannel\s*=\s*(\d+)", text)
        assert found, path
        assert int(found.group(1)) == BLUETOOTH_CHANNEL, path


# --- Windows: сторона Python --------------------------------------------------
def test_socket_wrapper_has_everything_the_stack_uses():
    """У Python на Windows Bluetooth-сокетов нет, поэтому соединение —
    обёртка над нативным дескриптором. Она обязана уметь ровно то, чем
    пользуется стек: иначе подмена вылезет в самом неудобном месте."""
    from b_hydra.transport import _WinBluetoothSocket

    for method in ("sendall", "recv", "settimeout", "gettimeout", "shutdown",
                   "close"):
        assert callable(getattr(_WinBluetoothSocket, method, None)), method


def test_factory_picks_the_transport_for_this_system():
    """`--transport bluetooth` обязан работать одинаково на обеих системах."""
    import sys

    from b_hydra.transport import WindowsBluetoothTransport, bluetooth_transport

    transport = bluetooth_transport()
    if sys.platform.startswith("win"):
        assert isinstance(transport, WindowsBluetoothTransport)
    else:
        assert isinstance(transport, BluetoothTransport)
        assert not isinstance(transport, WindowsBluetoothTransport)


def test_windows_transport_is_unavailable_here():
    """На Linux виндовый транспорт обязан честно сказать «нет».

    Иначе `--transport bluetooth` пошёл бы искать DLL там, где её быть не
    может, и падал бы с невнятной ошибкой ctypes вместо понятного отказа.
    """
    from b_hydra.transport import WindowsBluetoothTransport

    assert WindowsBluetoothTransport.available() is False


def test_windows_transport_reuses_the_common_discovery():
    """Поиск соседей общий: `bhydra_bt.exe` отвечает тем же JSON, что и
    Linux-слой, поэтому кода на два языка не появилось."""
    from b_hydra.transport import WindowsBluetoothTransport

    assert WindowsBluetoothTransport.neighbours is BluetoothTransport.neighbours
    assert WindowsBluetoothTransport.scan is BluetoothTransport.scan
    assert WindowsBluetoothTransport().bridge.endswith("bhydra_bt.exe")
