"""Терминал в НАСТОЯЩЕМ окне B-hydra Core, а не только движок команд.

`tests/test_console.py` проверяет разбор и выполнение команд без графики.
Здесь — то, что видно только в живом окне: попадает ли ответ на экран, какой
шрифт у вывода и не уходят ли деньги через сам интерфейс.

⚠️ Тесты ПРОПУСКАЮТСЯ без tkinter или без дисплея — ровно как тесты Bluetooth
без адаптера и обозревателя без браузера. В контейнере разработки tkinter нет,
поэтому прогон их не увидит; включатся они там, где графика есть (проверено
вручную на python3.12 + Xvfb).
"""

import os

import pytest

tk = pytest.importorskip("tkinter", reason="tkinter не установлен")


def _display_works():
    if not (os.environ.get("DISPLAY") or os.name == "nt"):
        return False
    try:
        probe = tk.Tk()
    except tk.TclError:
        return False
    probe.destroy()
    return True


needs_display = pytest.mark.skipif(not _display_works(),
                                   reason="нет дисплея для окна")


@pytest.fixture
def app(tmp_path, monkeypatch):
    """Живое окно поверх ЧИСТОГО состояния во временном каталоге.

    ⚠️ chdir обязателен: приложение пишет `bhydra_chain.json` рядом с собой, и
    без этого тест затирал бы настоящую цепочку разработчика.
    """
    monkeypatch.chdir(tmp_path)
    from b_hydra.gui import BHydraApp
    from b_hydra.wallet import generate_wallet

    window = BHydraApp()
    window.geometry("1100x800")
    window.update()
    if window.wallet is None:
        window.wallet = generate_wallet()
    for index in range(window._nb.index("end")):
        if "Терминал" in window._nb.tab(index, "text"):
            window._nb.select(index)
            break
    window.update()
    yield window
    window.destroy()


def _run(window, line):
    window.console_in.delete(0, "end")
    window.console_in.insert(0, line)
    window._console_submit()
    window.update()
    return window.console_out.get("1.0", "end")


# --- Что видно на экране -------------------------------------------------------
@needs_display
def test_the_console_tab_exists_in_the_window(app):
    titles = [app._nb.tab(i, "text") for i in range(app._nb.index("end"))]
    assert any("Терминал" in t for t in titles), titles


@needs_display
def test_a_command_answers_on_the_screen(app):
    """Ответ обязан появиться в окне, а не только вернуться из движка."""
    text = _run(app, "getinfo")
    assert "> getinfo" in text
    assert "версия" in text and "высота" in text


@needs_display
def test_the_output_font_is_actually_monospaced(app):
    """⚠️ НАЙДЕНО НА ЖИВОМ СНИМКЕ ОКНА, а не рассуждением.

    Весь вывод консоли — таблицы, выровненные пробелами. Шрифт задавался как
    `("TkFixedFont", 10)`, а Tk понимает это как СЕМЕЙСТВО с таким именем —
    его не существует, и Tk молча берёт пропорциональный. Ошибки нет, просто
    колонки разъезжаются в кашу. Проверяем по-настоящему: ширина узкого и
    широкого символа обязана совпасть.
    """
    import tkinter.font as tkfont

    resolved = tkfont.Font(font=app.console_out.cget("font"))
    assert resolved.measure("i") == resolved.measure("W"), \
        f"шрифт вывода не моноширинный: {resolved.actual()}"


@needs_display
def test_the_scam_warning_is_visible(app):
    """Предупреждение о мошенничестве обязано быть НА ЭКРАНЕ, а не в исходнике."""
    from b_hydra.console import WARNING

    assert app._console_warning.winfo_ismapped(), "предупреждение не показано"
    assert app._console_warning.cget("text") == WARNING


@needs_display
def test_an_unknown_command_is_shown_as_an_error_not_a_crash(app):
    text = _run(app, "сделайХорошо")
    assert "нет такой команды" in text
    assert app.winfo_exists(), "окно закрылось от неизвестной команды"


# --- Деньги через настоящий интерфейс ------------------------------------------
@needs_display
def test_send_through_the_window_asks_first(app):
    """⚠️ ГЛАВНОЕ: через живое окно перевод тоже не уходит с первого раза.

    Движок это уже гарантирует, но человек пользуется ОКНОМ, и проверить стоит
    именно тот путь, которым пойдёт он.
    """
    from b_hydra.wallet import generate_wallet

    for _ in range(2):
        app.node.mine_pending(app.wallet.address)
    stranger = generate_wallet().address

    text = _run(app, f"send {stranger} 10")
    assert "ПОДТВЕРДИТЕ" in text
    assert len(app.node.mempool) == 0, "деньги ушли без подтверждения"

    text = _run(app, f"send {stranger} 10 --yes")
    assert "принята в мемпул" in text
    assert len(app.node.mempool) == 1


@needs_display
def test_mining_from_the_console_updates_the_window(app):
    """Команда меняет состояние приложения, а не только печатает текст."""
    before = len(app.node.blockchain.chain)
    _run(app, "mine")
    assert len(app.node.blockchain.chain) == before + 1
    assert os.path.exists("bhydra_chain.json"), "блок не сохранён на диск"


@needs_display
def test_clear_empties_the_screen(app):
    _run(app, "getinfo")
    assert "версия" in app.console_out.get("1.0", "end")
    _run(app, "clear")
    left = app.console_out.get("1.0", "end")
    assert "версия" not in left
    assert "терминал" in left.lower(), "после очистки пропала шапка"


@needs_display
def test_history_walks_back_with_the_arrow_keys(app):
    _run(app, "getinfo")
    _run(app, "version")
    app._console_recall(-1)
    assert app.console_in.get() == "version"
    app._console_recall(-1)
    assert app.console_in.get() == "getinfo"


@needs_display
def test_the_dark_theme_keeps_the_console_readable(app):
    """Тёмная тема не должна оставить чёрный текст на чёрном фоне."""
    app._apply_theme(True)
    app.update()
    assert app.console_out.cget("bg") != app.console_out.cget("fg")
    assert app._console_warning.cget("bg") != app._console_warning.cget("fg")
