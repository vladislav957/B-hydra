"""Обозреватель блоков: дата создания блока видна в списке.

Раньше время блока показывалось только в КАРТОЧКЕ — то есть увидеть, когда
блок добыт, можно было лишь открыв его. В списке стояли номер, майнер, обрезок
хеша и число транзакций.

⚠️ Проверяется в НАСТОЯЩЕМ браузере, а не разбором HTML: список рисует
JavaScript из ответа `/api/chain`, и «строка есть в файле» ничего не доказывает
— важно, что она появилась на экране с настоящими данными узла.

Тесты пропускаются без playwright или без браузера.
"""

import os
import socket
import threading
import urllib.request

import pytest

from b_hydra.api import make_server

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _serve(tmp_path):
    port = _free_port()
    server = make_server("127.0.0.1", port, str(tmp_path / "chain.json"),
                         difficulty=2)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return port, server


def _post(port, path, payload=b"{}"):
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}",
                                     data=payload,
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request) as response:
        return response.read()


def _browser_path():
    for candidate in ("/opt/pw-browsers/chromium",
                      "/opt/pw-browsers/chromium/chrome-linux/chrome"):
        if os.path.exists(candidate):
            return candidate
    return None


needs_browser = pytest.mark.skipif(_browser_path() is None,
                                   reason="нет браузера для playwright")


@pytest.fixture
def page_with_blocks(tmp_path):
    """Живой узел с добытыми блоками + открытый в браузере обозреватель."""
    playwright = pytest.importorskip("playwright.sync_api",
                                     reason="playwright не установлен")
    port, server = _serve(tmp_path)
    address = "BHYDhAjTov9QXWR3nKovis28mX8cARWVUqtCn"
    for _ in range(3):
        _post(port, "/api/mine",
              ('{"miner": "%s"}' % address).encode("utf-8"))

    with playwright.sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=_browser_path())
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda event: errors.append(str(event)))
        page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
        # Ждём саму отрисовку, а не «сеть затихла»: список строит JS.
        page.wait_for_function("() => document.querySelectorAll('.blk').length > 0")
        assert errors == [], errors
        yield page
        browser.close()
    server.shutdown()


# --- Дата в списке блоков ------------------------------------------------------
@needs_browser
def test_every_block_row_shows_its_age(page_with_blocks):
    """У КАЖДОГО блока в списке есть колонка времени.

    Именно у каждого: пропуск у одного блока выглядел бы как сбой данных, а не
    как «времени нет».
    """
    page = page_with_blocks
    rows = page.query_selector_all(".blk")
    assert len(rows) >= 4                      # генезис + три добытых
    for row in rows:
        when = row.query_selector(".when")
        assert when is not None, "в строке блока нет колонки времени"
        assert when.inner_text().strip(), "колонка времени пустая"


@needs_browser
def test_the_genesis_shows_a_dash_and_explains_why(page_with_blocks):
    """⚠️ У генезиса метка времени НУЛЕВАЯ, и это намеренно.

    Она входит в хеш, а хеш генезиса обязан совпадать у всех узлов: возьми мы
    там текущее время — у каждого узла была бы своя цепочка. Поэтому в списке
    не «1 января 1970» и не «20 000 дн назад», а прочерк с объяснением.
    """
    page = page_with_blocks
    rows = page.query_selector_all(".blk")
    genesis = rows[-1].query_selector(".when")       # список от новых к старым
    assert genesis.inner_text().strip() == "—"
    title = genesis.get_attribute("title") or ""
    assert "генезис" in title.lower(), title
    # И ни у одного блока не должно быть «1970» или абсурдного возраста.
    for row in rows:
        text = row.query_selector(".when").inner_text()
        assert "1970" not in text, text


@needs_browser
def test_the_age_is_human_readable_and_recent(page_with_blocks):
    """Только что добытый блок — «только что», а не сырая метка времени."""
    page = page_with_blocks
    newest = page.query_selector_all(".blk")[0].query_selector(".when")
    text = newest.inner_text().strip()
    assert "назад" in text or text == "только что", text
    assert "1786" not in text, "показана сырая метка времени"


@needs_browser
def test_the_exact_time_is_in_the_tooltip(page_with_blocks):
    """Точное время не потеряно — оно в подсказке при наведении.

    В списке нужен возраст («5 мин назад»), но точная секунда обязана
    оставаться доступной, иначе обозреватель перестаёт быть инструментом.
    """
    page = page_with_blocks
    when = page.query_selector_all(".blk")[0].query_selector(".when")
    title = when.get_attribute("title")
    assert title, "нет подсказки с точным временем"
    assert any(ch.isdigit() for ch in title)
    assert ":" in title, f"подсказка не похожа на дату и время: {title}"


@needs_browser
def test_the_row_does_not_overflow_the_screen(page_with_blocks):
    """Добавленная дата не должна ломать вёрстку списка."""
    page = page_with_blocks
    assert not page.evaluate(
        "document.documentElement.scrollWidth > document.documentElement.clientWidth")


@needs_browser
def test_the_block_card_still_shows_the_full_date(page_with_blocks):
    """Дата в карточке блока была и раньше — она обязана остаться."""
    page = page_with_blocks
    page.query_selector_all(".blk")[0].click()
    page.wait_for_selector("#detail .card")
    assert "время" in page.inner_text("#detail")


# --- Сам расчёт возраста -------------------------------------------------------
@needs_browser
@pytest.mark.parametrize("offset,expected", [
    (0, "только что"),
    (-30, "только что"),
    (-90, "1 мин назад"),
    (-60 * 5, "5 мин назад"),
    (-60 * 60 * 3, "3 ч назад"),
    (-60 * 60 * 24 * 2, "2 дн назад"),
])
def test_age_wording(page_with_blocks, offset, expected):
    """Словами, а не числами: список читают глазами."""
    page = page_with_blocks
    got = page.evaluate("(o) => fmtAgo(Date.now()/1000 + o)", offset)
    assert got == expected


@needs_browser
def test_a_block_from_the_future_does_not_show_negative_time(page_with_blocks):
    """⚠️ Метка блока может быть В БУДУЩЕМ — это законно.

    Сеть допускает расхождение часов до MAX_FUTURE_DRIFT (2 часа), поэтому
    наивная разность дала бы «-5 мин назад» и выглядела бы как поломка.
    """
    page = page_with_blocks
    for ahead in (60, 600, 3600):
        got = page.evaluate("(o) => fmtAgo(Date.now()/1000 + o)", ahead)
        assert not got.startswith("-"), got
        assert got == "только что", got


@needs_browser
def test_an_ancient_block_falls_back_to_a_date(page_with_blocks):
    """Старше месяца — обычная дата: «400 дн назад» никому не помогает."""
    page = page_with_blocks
    got = page.evaluate("() => fmtAgo(Date.now()/1000 - 60*60*24*400)")
    assert "назад" not in got
    assert any(ch.isdigit() for ch in got)


@needs_browser
def test_a_missing_timestamp_does_not_break_the_row(page_with_blocks):
    """Блок без метки времени не должен ронять отрисовку списка."""
    page = page_with_blocks
    assert page.evaluate("() => fmtAgo(undefined)") == ""
    assert page.evaluate("() => fmtAgo('мусор')") == ""
