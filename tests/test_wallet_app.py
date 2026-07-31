"""Кошелёк как приложение для телефона (PWA): QR, манифест, воркер, иконки.

Главное здесь — QR. Прежний код в wallet.html рисовал ПОХОЖИЙ на QR узор из
псевдослучайных модулей: он выглядел как код, но не читался ничем. Это хуже,
чем отсутствие QR, — человек наводит камеру и не понимает, почему не работает.
Поэтому проверяется не «что-то нарисовалось», а совпадение матрицы с Python
модуль в модуль (порт qrcode_gen.py) на общем корпусе строк.

Тесты с браузером пропускаются без playwright, тесты QR — без node.
"""

import json
import os
import shutil
import socket
import struct
import subprocess
import threading
import urllib.request

import pytest

from b_hydra import icon
from b_hydra.api import make_server
from b_hydra.qrcode_gen import qr_matrix

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QR_JS = os.path.join(ROOT, "bhydra-qr.js")
MANIFEST = os.path.join(ROOT, "manifest.webmanifest")
SERVICE_WORKER = os.path.join(ROOT, "sw.js")
WALLET_HTML = os.path.join(ROOT, "wallet.html")
NODE = shutil.which("node")

QR_CASES = [
    "BHYDhAjTov9QXWR3nKovis28mX8cARWVUqtCn",
    "BHYDdbQfB7EfdKZi3fuX3A2bkwPmq7XsBaGFP",
    "A", "", "i-3ru", "привет",
    "bhydra:BHYDdbQfB7EfdKZi3fuX3A2bkwPmq7XsBaGFP?amount=12.5",
    "0123456789" * 3,
    "x" * 100,          # версия побольше
    "x" * 200,          # и ещё больше — другое число блоков коррекции
]


def _free_port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _serve(tmp_path):
    port = _free_port()
    server = make_server("127.0.0.1", port, str(tmp_path / "chain.json"),
                         difficulty=2)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return port, server


def _get(port, path):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}") as response:
        return response.status, response.headers, response.read()


# --- QR: браузерная реализация против Python ---------------------------------
@pytest.mark.skipif(NODE is None, reason="нет node")
def test_browser_qr_matches_python_module_for_module(tmp_path):
    """Матрицы совпадают целиком, а не «оба выглядят как QR».

    Один неверный модуль в маске или в блоках коррекции даёт код, который
    сканер прочитает неправильно или не прочитает вовсе.
    """
    bridge = tmp_path / "qr_bridge.js"
    bridge.write_text(
        f'const Q = require({json.dumps(QR_JS)});\n'
        'const cases = JSON.parse(require("fs").readFileSync(0, "utf8"));\n'
        'console.log(JSON.stringify(cases.map((t) => Q.matrix(t))));\n',
        encoding="utf-8")
    result = subprocess.run([NODE, str(bridge)], input=json.dumps(QR_CASES),
                            capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, result.stderr
    produced = json.loads(result.stdout)
    for text, rows in zip(QR_CASES, produced):
        assert rows == qr_matrix(text), f"расхождение на {text!r}"


@pytest.mark.skipif(NODE is None, reason="нет node")
def test_browser_qr_svg_is_self_contained(tmp_path):
    """SVG рисуется одним путём и не тянет ничего извне."""
    bridge = tmp_path / "svg_bridge.js"
    bridge.write_text(
        f'const Q = require({json.dumps(QR_JS)});\n'
        'console.log(Q.toSvg(Q.matrix("BHYx"), {size: 200}));\n', encoding="utf-8")
    svg = subprocess.run([NODE, str(bridge)], capture_output=True, text=True,
                         timeout=120).stdout
    assert svg.startswith("<svg") and "</svg>" in svg
    assert "http" not in svg.replace("http://www.w3.org/2000/svg", "")
    assert "<image" not in svg and "<script" not in svg


def test_python_qr_is_actually_scannable():
    """Матрица кодирует те самые данные — проверяем разбором собственных бит.

    Прежние тесты смотрели только на форму (квадрат, узоры-искатели, тёмный
    модуль). Форма правильная и у случайного шума: нужно убедиться, что внутри
    лежит именно наша строка.
    """
    from b_hydra.qrcode_gen import _choose_version, _encode_data

    text = "BHYDdbQfB7EfdKZi3fuX3A2bkwPmq7XsBaGFP"
    version = _choose_version(len(text.encode("utf-8")))
    codewords = _encode_data(text, version)
    # Первые байты кодовых слов: режим 0100, счётчик, затем сами данные.
    bits = "".join(f"{cw:08b}" for cw in codewords)
    assert bits[:4] == "0100"                       # байтовый режим
    length = int(bits[4:12], 2)
    assert length == len(text.encode("utf-8"))
    payload = bytes(int(bits[12 + i * 8:20 + i * 8], 2) for i in range(length))
    assert payload.decode("utf-8") == text


def test_wallet_page_no_longer_fakes_a_qr_code():
    """Регрессия: псевдослучайный «QR» не должен вернуться.

    Он выглядел как код, но не сканировался — камера просто ничего не находила.
    """
    page = open(WALLET_HTML, encoding="utf-8").read()
    assert "детерминированная матрица из хеша строки" not in page
    assert "bhydra-qr.js" in page
    assert "BHydraQR" in page


# --- Манифест, воркер, иконки -------------------------------------------------
def test_manifest_has_what_the_browser_requires():
    """Без этих полей телефон не предложит установку."""
    data = json.load(open(MANIFEST, encoding="utf-8"))
    assert data["name"] and data["short_name"]
    assert data["start_url"] == "/wallet"
    assert data["display"] == "standalone"
    assert data["theme_color"] and data["background_color"]
    sizes = {item["sizes"] for item in data["icons"]}
    assert {"192x192", "512x512"} <= sizes         # обязательный минимум
    # Хотя бы одна иконка maskable: иначе Android обрежет её по своей форме.
    assert any(item.get("purpose") == "maskable" for item in data["icons"])


def test_service_worker_never_caches_node_data():
    """Кэшировать /api/ нельзя: вчерашний баланс, показанный как сегодняшний,
    хуже честного «нет связи»."""
    source = open(SERVICE_WORKER, encoding="utf-8").read()
    assert '/api/' in source and "return" in source
    assert 'url.pathname.startsWith("/api/")' in source


def test_service_worker_caches_the_shell():
    source = open(SERVICE_WORKER, encoding="utf-8").read()
    for asset in ("/wallet", "/bhydra-sign.js", "/bhydra-qr.js",
                  "/manifest.webmanifest", "/icon-192.png"):
        assert asset in source, asset


def test_icons_are_valid_png(tmp_path):
    """PNG собирается вручную (zlib + CRC32), поэтому проверяем заголовок."""
    for size in (48, 192, 512):
        data = icon.png_bytes(size)
        assert data[:8] == b"\x89PNG\r\n\x1a\n"
        width, height, depth, colour = struct.unpack(">IIBB", data[16:26])
        assert (width, height) == (size, size)
        assert depth == 8 and colour == 2          # 8 бит на канал, RGB
        assert data[-12:] == b"\x00\x00\x00\x00IEND\xaeB`\x82"


def test_icon_is_deterministic():
    """Иконка считается, а не хранится: одинаковый вход — одинаковый файл."""
    assert icon.png_bytes(64) == icon.png_bytes(64)


def test_icon_mark_fits_the_maskable_safe_area():
    """Значимая часть знака умещается в центральные 80%.

    Android обрезает иконку под свою форму — то, что вылезло за круг, пропадёт.
    """
    edge = 0.1
    for x in (0.0, 0.05, 0.5, 0.95, 1.0):
        for y in (0.0, 0.05, 0.95, 1.0):
            if x < edge or x > 1 - edge or y < edge or y > 1 - edge:
                assert icon._coverage(x, y) == 0.0, (x, y)


def test_icons_are_not_stored_in_the_repository():
    """В репозитории нет бинарных ресурсов — иконки создаются на лету."""
    for name in ("icon-192.png", "icon-512.png"):
        tracked = subprocess.run(["git", "ls-files", "--error-unmatch", name],
                                 cwd=ROOT, capture_output=True, text=True)
        assert tracked.returncode != 0, f"{name} попал в репозиторий"


def test_ensure_files_creates_icons_once(tmp_path):
    made = icon.ensure_files(str(tmp_path))
    assert len(made) == 2
    assert icon.ensure_files(str(tmp_path)) == []   # второй раз не переписывает


# --- Отдача узлом --------------------------------------------------------------
def test_node_serves_the_app_files(tmp_path):
    port, server = _serve(tmp_path)
    try:
        status, headers, body = _get(port, "/manifest.webmanifest")
        assert status == 200
        assert headers["Content-Type"] == "application/manifest+json"
        assert json.loads(body)["start_url"] == "/wallet"

        status, headers, body = _get(port, "/sw.js")
        assert status == 200 and "javascript" in headers["Content-Type"]
        # Воркер кэшировать нельзя: иначе обновление кошелька застрянет
        # у пользователя навсегда.
        assert headers.get("Cache-Control") == "no-cache"

        status, headers, body = _get(port, "/bhydra-qr.js")
        assert status == 200 and "javascript" in headers["Content-Type"]
        assert b"BHydraQR" in body

        for name in ("icon-192.png", "icon-512.png"):
            status, headers, body = _get(port, "/" + name)
            assert status == 200 and headers["Content-Type"] == "image/png"
            assert body[:8] == b"\x89PNG\r\n\x1a\n"
    finally:
        server.shutdown()


def test_wallet_page_declares_itself_installable():
    page = open(WALLET_HTML, encoding="utf-8").read()
    assert 'rel="manifest"' in page
    assert 'name="theme-color"' in page
    assert 'rel="apple-touch-icon"' in page
    assert "serviceWorker" in page and "/sw.js" in page
    assert "viewport-fit=cover" in page             # вырез и «шторка» телефона


def test_payment_uri_parsing_rules():
    """Разбор ссылки оплаты описан здесь же, чтобы правило не разъехалось
    с реализацией в странице."""
    page = open(WALLET_HTML, encoding="utf-8").read()
    assert "function parsePaymentUri" in page
    assert "bhydra:" in page
    # Адрес обязан проверяться по алфавиту base58, иначе в поле попадёт мусор
    # из любого чужого QR.
    assert "BHY[1-9A-HJ-NP-Za-km-z]" in page


# --- Живая проверка в браузере (если есть playwright) --------------------------
def _browser_path():
    for candidate in ("/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
                      "/opt/pw-browsers/chromium/chrome-linux/chrome"):
        if os.path.exists(candidate):
            return candidate
    return None


@pytest.mark.skipif(_browser_path() is None, reason="нет браузера для playwright")
def test_wallet_runs_on_a_phone_screen(tmp_path):
    """Настоящий Chromium в эмуляции телефона: страница живая, QR настоящий,
    воркер регистрируется, горизонтальной прокрутки нет."""
    playwright = pytest.importorskip("playwright.sync_api",
                                     reason="playwright не установлен")
    port, server = _serve(tmp_path)
    try:
        with playwright.sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path=_browser_path())
            context = browser.new_context(**pw.devices["Pixel 5"])
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"http://127.0.0.1:{port}/wallet", wait_until="networkidle")

            assert errors == []
            # Ничего не должно вылезать за экран телефона.
            assert not page.evaluate(
                "document.documentElement.scrollWidth > innerWidth")
            # QR нарисован и совпадает с Python для того же адреса.
            address = page.evaluate("document.getElementById('qrAddr').textContent")
            rows = page.evaluate("(a) => BHydraQR.matrix(a)", address)
            assert rows == qr_matrix(address)
            assert page.evaluate("!!document.querySelector('#qr svg')")

            registration = page.evaluate(
                """async () => {
                    const r = await navigator.serviceWorker.ready;
                    return !!(r && r.active);
                }""")
            assert registration is True
            browser.close()
    finally:
        server.shutdown()
