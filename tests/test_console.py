"""Терминал B-hydra Core: команды внутри приложения (как консоль Bitcoin Core).

⚠️ БОЛЬШАЯ ЧАСТЬ ФАЙЛА — ПРО ТО, ЧЕГО КОНСОЛЬ ДЕЛАТЬ НЕ ДОЛЖНА. Она живёт в
одном процессе с кошельком, где лежат деньги, поэтому опасны не команды,
которые работают, а те, которых не должно существовать вовсе: выполнение
произвольного кода и печать приватного ключа. Ровно через них уводят средства
— человеку диктуют «команду для починки кошелька», он её вставляет.

Движок проверяется БЕЗ tkinter: `console.py` про окно не знает, ему хватает
поддельного приложения. Поэтому тесты идут везде, а не только там, где есть
графика.
"""

import re

import pytest

from b_hydra import console as console_module
from b_hydra.console import COMMANDS, WARNING, Console, ConsoleError
from b_hydra.node import BHydraNode
from b_hydra.wallet import generate_wallet


class FakeApp:
    """Приложение для консоли: узел, кошелёк и два действия. Больше ей не надо."""

    def __init__(self, with_wallet=True, p2p=None):
        self.node = BHydraNode(difficulty=1)
        self.wallet = generate_wallet() if with_wallet else None
        self.p2p = p2p
        self.saves = 0
        self.broadcasts = []

    def save_state(self):
        self.saves += 1

    def broadcast_transaction(self, tx):
        self.broadcasts.append(tx)


@pytest.fixture
def app():
    return FakeApp()


@pytest.fixture
def console(app):
    return Console(app)


@pytest.fixture
def funded(app, console):
    """Кошелёк с деньгами: два добытых блока."""
    for _ in range(2):
        app.node.mine_pending(app.wallet.address)
    return app


# --- Чего в консоли НЕТ --------------------------------------------------------
def test_there_is_no_way_to_run_arbitrary_code(console):
    """⚠️ ГЛАВНОЕ: это НЕ оболочка ОС и НЕ Python.

    Консоль в одном процессе с кошельком. Дай она выполнить произвольный код —
    любая продиктованная строчка отдавала бы приватный ключ. Набор команд
    закрытый, и ничего похожего на запуск кода в нём быть не может.
    """
    for attempt in ["eval", "exec", "python", "import", "shell", "sh", "bash",
                    "system", "os", "subprocess", "open", "!ls", "$(ls)",
                    "eval print(1)", "__import__('os')"]:
        with pytest.raises(ConsoleError):
            console.run(attempt)


def test_no_command_ever_prints_the_private_key(funded, console):
    """⚠️ Команды, печатающей приватный ключ, НЕТ СОВСЕМ — и это осознанно.

    Именно так ключи и уводят: «покажи dumpprivkey и пришли мне». Проверяется
    не отсутствие имени команды, а то, что ключ не вылезает НИ ИЗ ОДНОГО
    ответа: прогоняем все безопасные команды и ищем ключ в выводе.
    """
    secret = funded.wallet.private_key_hex
    assert secret, "у кошелька нет приватного ключа — тест бессмыслен"

    for name in ["dumpprivkey", "privkey", "private", "key", "export",
                 "exportkey", "dumpwallet", "seed"]:
        with pytest.raises(ConsoleError):
            console.run(name)

    safe = ["help", "version", "getinfo", "getbalance", "getaddress",
            "chain", "mempool", "getblock 0", "getblock -1", "validate"]
    for line in safe:
        answer = console.run(line)
        assert secret not in answer, f"команда {line!r} напечатала приватный ключ"
        assert secret[:16] not in answer, f"команда {line!r} печатает часть ключа"


def test_the_command_table_is_closed(console):
    """Неизвестная команда — отказ с подсказкой, а не попытка угадать."""
    with pytest.raises(ConsoleError, match="нет такой команды"):
        console.run("сделайХорошо")
    # И подсказка про `help` обязана быть: консоль без неё бесполезна новичку.
    try:
        console.run("getinf")
    except ConsoleError as error:
        assert "help" in str(error)


def test_the_scam_warning_exists_and_is_shown(console):
    """⚠️ Предупреждение — часть защиты, а не украшение.

    Bitcoin Core держит над своей консолью такое же: уловка «вставьте команду,
    чтобы починить кошелёк» стара и работает до сих пор.
    """
    assert "мошенник" in WARNING.lower() or "продиктова" in WARNING.lower()
    assert "приватный ключ" in WARNING.lower()

    import os
    gui = open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "b_hydra", "gui.py"), encoding="utf-8").read()
    assert "WARNING" in gui, "предупреждение не выводится в окне"


# --- Деньги: единственная опасная команда --------------------------------------
def test_send_does_not_move_money_without_confirmation(funded, console):
    """⚠️ САМОЕ ВАЖНОЕ ПРО ДЕНЬГИ: с первого раза перевод НЕ уходит.

    Продиктованная строчка обязана упереться в экран подтверждения, где сумма
    и адрес написаны словами. Это и есть та секунда, за которую человек
    успевает понять, что его разводят.
    """
    victim = generate_wallet().address
    before = funded.node.get_balance(funded.wallet.address)

    answer = console.run(f"send {victim} 10")

    assert "ПОДТВЕРДИТЕ" in answer
    assert victim in answer and "10.0000" in answer
    assert len(funded.node.mempool) == 0, "транзакция ушла без подтверждения"
    assert funded.broadcasts == [], "транзакция разослана без подтверждения"
    assert funded.node.get_balance(funded.wallet.address) == before


def test_send_with_yes_actually_sends(funded, console):
    """А с --yes — работает, иначе команда была бы бесполезной."""
    victim = generate_wallet().address
    answer = console.run(f"send {victim} 10 --yes")

    assert "принята в мемпул" in answer
    assert len(funded.node.mempool) == 1
    assert len(funded.broadcasts) == 1, "перевод не разослан соседям"
    assert funded.saves >= 1, "состояние не сохранено на диск"


def test_the_confirmation_screen_names_the_real_amount(funded, console):
    """В подтверждении обязаны стоять НАСТОЯЩИЕ суммы, включая комиссию.

    Иначе экран подтверждения — формальность: человек жмёт «да», не понимая,
    сколько списывают.
    """
    victim = generate_wallet().address
    answer = console.run(f"send {victim} 7.5 --fee 0.25")
    assert "7.5000" in answer          # сумма
    assert "0.2500" in answer          # комиссия
    assert "7.7500" in answer          # итого


def test_send_refuses_what_the_node_would_refuse(funded, console):
    """Проверки те же, что у остальных путей: адрес, сумма, средства."""
    good = generate_wallet().address
    for line, complaint in [
        (f"send {good} -5 --yes", "больше нуля"),
        (f"send МУСОР 1 --yes", "неверный адрес"),
        (f"send {good} 10 --fee -1 --yes", "отрицательной"),
        (f"send {good} 999999 --yes", "недостаточно средств"),
        ("send", "send <адрес>"),
    ]:
        with pytest.raises(ConsoleError, match=re.escape(complaint)):
            console.run(line)
    assert len(funded.node.mempool) == 0


def test_send_without_a_wallet_says_so(console):
    """Без загруженного кошелька — внятный ответ, а не трассировка."""
    console.app.wallet = None
    with pytest.raises(ConsoleError, match="кошелёк не загружен"):
        console.run(f"send {generate_wallet().address} 1 --yes")


# --- Полезная работа -----------------------------------------------------------
def test_help_lists_every_command(console):
    answer = console.run("help")
    for name in ("getinfo", "getbalance", "getblock", "mine", "send", "help"):
        assert name in answer, name


def test_help_explains_a_single_command(console):
    answer = console.run("help send")
    assert "--yes" in answer
    assert "дикту" in answer.lower(), \
        "справка по send молчит о том, ради чего сделано подтверждение"


def test_getinfo_reports_the_real_chain(funded, console):
    answer = console.run("getinfo")
    assert str(len(funded.node.blockchain.chain)) in answer
    assert funded.wallet.address in answer
    assert "100.0000" in answer                 # две награды по 50


def test_mine_actually_extends_the_chain(app, console):
    before = len(app.node.blockchain.chain)
    answer = console.run("mine")
    assert len(app.node.blockchain.chain) == before + 1
    assert f"#{before}" in answer
    assert app.saves >= 1, "добытый блок не сохранён"


def test_mine_can_carry_a_note(app, console):
    console.run('mine --message "привет из терминала"')
    block = app.node.blockchain.chain[-1]
    assert app.node.blockchain.coinbase_message(block) == "привет из терминала"


def test_getblock_reads_a_real_block(funded, console):
    answer = console.run("getblock 1")
    block = funded.node.blockchain.chain[1]
    assert block.hash in answer
    assert str(block.nonce) in answer


def test_getblock_minus_one_is_the_last_one(funded, console):
    assert funded.node.blockchain.chain[-1].hash in console.run("getblock -1")


def test_getblock_complains_about_nonsense(console):
    with pytest.raises(ConsoleError, match="целое число"):
        console.run("getblock хвост")
    with pytest.raises(ConsoleError, match="нет"):
        console.run("getblock 99999")


def test_gettx_finds_a_confirmed_transaction(funded, console):
    block = funded.node.blockchain.chain[1]
    data = block.data if isinstance(block.data, list) else [block.data]
    txid = (data[0] if isinstance(data[0], dict) else data[0].to_dict())["txid"]
    answer = console.run(f"gettx {txid}")
    assert txid in answer
    assert funded.wallet.address in answer


def test_gettx_finds_a_transaction_still_in_the_mempool(funded, console):
    """⚠️ Неподтверждённую транзакцию тоже надо уметь показать.

    Её нет в цепочке, и поиск только по индексу блока отвечал бы «нет такой» —
    ровно тогда, когда человек и смотрит: «ушёл мой перевод или нет?».
    """
    victim = generate_wallet().address
    console.run(f"send {victim} 5 --yes")
    txid = next(iter(funded.node.mempool.transactions))
    txid = (txid if isinstance(txid, dict) else txid.to_dict())["txid"]
    answer = console.run(f"gettx {txid}")
    assert victim in answer


def test_quotes_let_you_pass_spaces(app, console):
    """Разбор через shlex: заметка с пробелами — обычное дело."""
    console.run('mine --message "две части"')
    block = app.node.blockchain.chain[-1]
    assert app.node.blockchain.coinbase_message(block) == "две части"


def test_an_unclosed_quote_is_a_message_not_a_crash(console):
    with pytest.raises(ConsoleError, match="не разобрать"):
        console.run('mine --message "забыл закрыть')


def test_an_empty_line_does_nothing(console):
    assert console.run("") == ""
    assert console.run("   ") == ""


def test_network_commands_say_when_the_node_is_off(console):
    """Без поднятого узла — объяснение, куда идти, а не пустой ответ."""
    for line in ("peers", "sync", "addpeer 127.0.0.1:5000"):
        with pytest.raises(ConsoleError, match="Сеть"):
            console.run(line)


def test_aliases_work(funded, console):
    """Короткие имена: `info`, `balance`, `block` — как привыкли в кошельках."""
    assert console.run("info") == console.run("getinfo")
    assert console.run("balance") == console.run("getbalance")
    assert "?" in COMMANDS and COMMANDS["?"] is COMMANDS["help"]


def test_validate_checks_the_chain(funded, console):
    assert "ВАЛИДНА" in console.run("validate")


def test_clear_is_handled_by_the_window(console):
    """Движок про экран не знает, но команда обязана существовать."""
    assert console.run("clear") == "\x00clear"


# --- Разделение слоёв ----------------------------------------------------------
def test_the_engine_does_not_import_tkinter():
    """⚠️ Движок обязан работать без графики — на этом держатся эти тесты.

    Потяни `console.py` за собой tkinter, и команды нельзя было бы проверить
    там, где графики нет, то есть в обычном прогоне.
    """
    import ast
    import sys

    # ⚠️ Проверяются НАСТОЯЩИЕ импорты, а не подстрока «tkinter»: в шапке
    # модуля это слово стоит в объяснении, почему его там нет, и поиск по
    # тексту ловил сам комментарий. Тот же промах уже был в проверке на
    # забытый ключ — сравнивать надо смысл, а не буквы.
    tree = ast.parse(open(console_module.__file__, encoding="utf-8").read())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imported.add(node.module.split(".")[0])
    assert "tkinter" not in imported, f"движок тянет графику: {sorted(imported)}"

    # И на деле тоже: импорт движка не должен подтягивать tkinter в процесс.
    for name in [n for n in sys.modules if n.startswith("tkinter")]:
        del sys.modules[name]
    import importlib

    importlib.reload(console_module)
    assert not any(n.startswith("tkinter") for n in sys.modules), \
        "импорт console.py притащил tkinter"


def _gui_source():
    import os

    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "b_hydra", "gui.py")
    return open(path, encoding="utf-8").read()


def test_the_window_only_calls_what_the_app_promises():
    """Консоль просит у приложения ровно два действия — они обязаны быть в GUI."""
    gui = _gui_source()
    for method in ("def save_state", "def broadcast_transaction"):
        assert method in gui, f"в gui.py нет {method} — терминал упадёт на нём"


def test_the_console_tab_is_actually_added_to_the_window():
    """⚠️ Вкладку мало написать — её надо ДОБАВИТЬ в Notebook.

    tkinter в контейнере нет, запустить окно нечем, поэтому проверяется хотя бы
    то, что метод постройки вызывается: иначе получилась бы вкладка, которую
    никто не создаёт, и заметил бы это только пользователь.
    """
    import ast

    tree = ast.parse(_gui_source())
    built = {node.name for node in ast.walk(tree)
             if isinstance(node, ast.FunctionDef)}
    assert "_build_console_tab" in built

    called = {ast.unparse(node.func).split(".")[-1]
              for node in ast.walk(tree) if isinstance(node, ast.Call)}
    assert "_build_console_tab" in called, \
        "вкладка терминала написана, но не добавлена в окно"


def test_the_console_survives_an_unexpected_error():
    """⚠️ Консоль не имеет права уронить приложение — там кошелёк с деньгами.

    Опечатка в команде или сбой внутри неё должны стать строчкой в окне, а не
    закрытым окном с потерянным несохранённым состоянием.
    """
    gui = _gui_source()
    submit = gui[gui.index("def _console_submit"):]
    submit = submit[:submit.index("\n    # ---")]
    assert "except Exception" in submit, \
        "в обработчике команды нет страховки от неожиданной ошибки"
    assert "ConsoleError" in submit
