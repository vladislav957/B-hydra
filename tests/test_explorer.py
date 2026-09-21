"""Обозреватель блоков: дата создания блока и шторка для адресов.

Раньше время блока показывалось только в КАРТОЧКЕ — то есть увидеть, когда
блок добыт, можно было лишь открыв его. В списке стояли номер, майнер, обрезок
хеша и число транзакций.

⚠️ Проверяется в НАСТОЯЩЕМ браузере, а не разбором HTML: список рисует
JavaScript из ответа `/api/chain`, и «строка есть в файле» ничего не доказывает
— важно, что она появилась на экране с настоящими данными узла.

Тесты пропускаются без playwright или без браузера.
"""

import contextlib
import json
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


MINER = "BHYDhAjTov9QXWR3nKovis28mX8cARWVUqtCn"


class _Explorer:
    """Открытая страница + адрес узла: части тестов нужен и REST под ней."""

    def __init__(self, page, port, address):
        self.page = page
        self.port = port
        self.address = address


@contextlib.contextmanager
def _open(tmp_path):
    playwright = pytest.importorskip("playwright.sync_api",
                                     reason="playwright не установлен")
    port, server = _serve(tmp_path)
    for _ in range(3):
        _post(port, "/api/mine",
              ('{"miner": "%s"}' % MINER).encode("utf-8"))

    with playwright.sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=_browser_path())
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda event: errors.append(str(event)))
        page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
        # Ждём саму отрисовку, а не «сеть затихла»: список строит JS.
        page.wait_for_function("() => document.querySelectorAll('.blk').length > 0")
        assert errors == [], errors
        yield _Explorer(page, port, MINER)
        browser.close()
    server.shutdown()


@pytest.fixture
def page_with_blocks(tmp_path):
    """Живой узел с добытыми блоками + открытый в браузере обозреватель."""
    with _open(tmp_path) as explorer:
        yield explorer.page


@pytest.fixture
def explorer(tmp_path):
    with _open(tmp_path) as ready:
        yield ready


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


# --- Шторка для адресов --------------------------------------------------------
# ⚠️ ГЛАВНОЕ, ЧТО ЗДЕСЬ ПРОВЕРЯЕТСЯ, — ЧЕСТНОСТЬ НАЗВАНИЯ. Это ШТОРКА НА ЭКРАНЕ,
# а не приватность: цепочка публична по устройству, и те же адреса лежат в
# `/api/chain`. Последний тест в этом разделе закрепляет именно это — чтобы
# переключатель никогда не начали считать защитой.
def _visible(page):
    """Текст, который реально виден на экране (а не разметка страницы)."""
    return page.inner_text("body")


def _hide(page):
    """Включает шторку и ДОЖИДАЕТСЯ перерисовки.

    ⚠️ Ждать нужно именно результат на экране: `refresh()` асинхронный (ходит в
    сеть за цепочкой), и проверка сразу после клика читала бы ещё старую
    отрисовку — тест «падал» бы на исправном коде.
    """
    page.click("#hideAddr")
    page.wait_for_function(
        "() => document.getElementById('hideAddr').classList.contains('on')")
    page.wait_for_function("() => document.querySelectorAll('.blk').length > 0")
    # Списки перерисовываются по очереди (`refresh` идёт последовательно), и
    # ждать «хоть где-то появилась заглушка» мало: список богатейших рисуется
    # ПОСЛЕДНИМ, и проверка успевала прочитать его в прежнем виде.
    page.wait_for_function(
        "() => document.getElementById('blocks').innerText.includes('•')"
        "   && document.getElementById('rich').innerText.includes('•')")


def _show(page):
    page.click("#hideAddr")
    page.wait_for_function(
        "() => !document.getElementById('hideAddr').classList.contains('on')")
    page.wait_for_function("() => !document.body.innerText.includes('•')")


@needs_browser
def test_addresses_are_visible_by_default(explorer):
    """По умолчанию обозреватель показывает всё: это инструмент, а не сейф."""
    page = explorer.page
    assert explorer.address[:9] in _visible(page)
    assert page.eval_on_selector("#hideAddr", "e => e.classList.contains('on')") is False


@needs_browser
def test_hiding_removes_addresses_from_the_screen(explorer):
    """Включили — ни одного адреса на экране не осталось.

    Проверяется НЕ факт вызова функции, а результат: узнаваемое начало адреса
    больше нигде не встречается в видимом тексте страницы.
    """
    page = explorer.page
    _hide(page)
    assert explorer.address[:9] not in _visible(page)
    assert explorer.address not in _visible(page)


@needs_browser
def test_the_masked_address_is_still_recognisable_as_an_address(explorer):
    """Вместо адреса — не пустота, а явная заглушка с префиксом BHY.

    Пустое место читалось бы как «данных нет» и выглядело бы поломкой; шторка
    обязана быть видна как шторка.
    """
    page = explorer.page
    _hide(page)
    miner = page.query_selector(".blk .miner").inner_text().strip()
    assert miner.startswith("BHY"), miner
    assert "•" in miner, miner


@needs_browser
def test_the_tooltip_does_not_leak_the_address(explorer):
    """⚠️ Подсказка всплывает НА ЭКРАНЕ — значит попадает в скриншот.

    В списке богатейших адрес лежал в `title`. Замаскировать строку, оставив
    его в подсказке, значило бы не скрыть ничего: наведение мышью показало бы
    адрес целиком, да ещё и полностью, а не обрезанным.
    """
    page = explorer.page
    _hide(page)
    for element in page.query_selector_all("[title]"):
        title = element.get_attribute("title") or ""
        assert explorer.address[:9] not in title, title


@needs_browser
def test_hiding_covers_the_cards_too(explorer):
    """Карточки блока, транзакции и адреса — тоже под шторкой.

    Иначе один клик по строке сводил бы всю затею на нет.
    """
    page = explorer.page
    _hide(page)
    page.query_selector_all(".blk")[0].click()
    page.wait_for_selector("#detail .card")
    detail = page.inner_text("#detail")
    assert explorer.address[:9] not in detail, detail
    assert "•" in detail                       # майнер и выход coinbase

    # И страница адреса, открытая поиском по полному адресу.
    page.fill("#q", explorer.address)
    page.click("#q + button")
    page.wait_for_function(
        "() => document.querySelector('#detail .card h4')"
        "  && document.querySelector('#detail .card h4').innerText.includes('Адрес')")
    assert explorer.address not in page.inner_text("#detail")
    assert "адрес скрыт" in page.inner_text("#detail")


@needs_browser
def test_the_open_card_is_closed_on_switching(explorer):
    """⚠️ Открытая карточка перерисовкой списков НЕ трогается.

    `refresh()` обновляет только списки, а `#detail` живёт сам по себе: не
    закрой мы его явно, адрес остался бы висеть на экране после включения
    шторки — то есть переключатель врал бы ровно в тот момент, когда он нужен.
    """
    page = explorer.page
    page.query_selector_all(".blk")[0].click()
    page.wait_for_selector("#detail .card")
    _hide(page)
    assert page.inner_text("#detail").strip() == ""


@needs_browser
def test_the_choice_survives_a_reload(explorer):
    """Выбор запоминается: показ не должен начинаться со случайной засветки."""
    page = explorer.page
    _hide(page)
    page.reload(wait_until="networkidle")
    page.wait_for_function("() => document.querySelectorAll('.blk').length > 0")
    assert page.eval_on_selector("#hideAddr", "e => e.classList.contains('on')") is True
    assert explorer.address[:9] not in _visible(page)


@needs_browser
def test_showing_brings_the_addresses_back(explorer):
    """Второе нажатие возвращает всё как было — переключатель, а не режим."""
    page = explorer.page
    _hide(page)
    _show(page)
    assert explorer.address[:9] in _visible(page)


@needs_browser
def test_the_button_says_which_state_it_is_in(explorer):
    """Кнопка обязана показывать состояние: забыть про включённую шторку легко."""
    page = explorer.page
    assert "видны" in page.inner_text("#hideAddr")
    _hide(page)
    assert "скрыт" in page.inner_text("#hideAddr")


@needs_browser
def test_the_layout_survives_the_extra_button(explorer):
    page = explorer.page
    assert not page.evaluate(
        "document.documentElement.scrollWidth > document.documentElement.clientWidth")


@needs_browser
def test_hiding_does_not_break_navigation(explorer):
    """Под шторкой обозреватель остаётся рабочим: по адресу всё ещё кликается.

    Именно поэтому настоящий адрес остаётся в `data-id` — иначе строка списка
    перестала бы вести на страницу адреса.
    """
    page = explorer.page
    _hide(page)
    page.query_selector(".lg[data-action='address']").click()
    page.wait_for_selector("#detail .card")
    text = page.inner_text("#detail")
    assert "Адрес" in text
    assert explorer.address not in text


@needs_browser
def test_the_curtain_is_not_privacy_and_the_chain_proves_it(explorer):
    """⚠️ САМЫЙ ВАЖНЫЙ ТЕСТ РАЗДЕЛА: это НЕ приватность.

    Адреса скрыты только на экране. Цепочка публична по устройству: тот же
    адрес лежит в ответе `/api/chain`, и любой заберёт его curl'ом в обход
    страницы. Настоящая приватность требует другой криптографии в САМОЙ цепочке
    (скрытые адреса, смешивание, конфиденциальные суммы), то есть смены
    консенсуса. Тест стоит здесь, чтобы переключатель никогда не начали
    считать защитой.
    """
    page = explorer.page
    _hide(page)
    assert explorer.address[:9] not in _visible(page)

    with urllib.request.urlopen(
            f"http://127.0.0.1:{explorer.port}/api/chain") as response:
        chain = json.loads(response.read().decode("utf-8"))
    assert explorer.address in json.dumps(chain), \
        "адрес пропал из цепочки — шторка не смеет менять данные узла"

    # И сам код страницы говорит об этом прямо, а не только тесты.
    source = open(os.path.join(ROOT, "explorer.html"), encoding="utf-8").read()
    assert "ЭТО НЕ ПРИВАТНОСТЬ" in source
