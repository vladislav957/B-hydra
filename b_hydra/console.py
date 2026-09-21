"""Терминал B-hydra Core — консоль команд внутри приложения.

Аналог консоли отладки в Bitcoin Core (Help → Debug window → Console): окно,
где вместо хождения по вкладкам набираешь команду и сразу видишь ответ узла.

    > getinfo
    > getbalance BHY…
    > getblock 42
    > mine BHY… --message "привет"

⚠️ ЭТО НЕ ОБОЛОЧКА ОС И НЕ PYTHON. Ни `eval`, ни `exec`, ни запуска программ
здесь нет и быть не может: набор команд ЗАКРЫТЫЙ, он весь в `COMMANDS`. Консоль
живёт в одном процессе с кошельком, поэтому «выполнить произвольный код» здесь
означало бы «отдать приватный ключ любому, кто продиктует строчку».

⚠️ И ЭТО НЕ ПАРАНОЙЯ, А ИЗВЕСТНОЕ МОШЕННИЧЕСТВО. Классика: новичку в чате
пишут «твой кошелёк повреждён, вставь эту команду в консоль, и всё починится»
— и монеты уходят. Bitcoin Core не зря держит над своей консолью красное
предупреждение. Поэтому здесь сделано три вещи:

  1. Нет НИ ОДНОЙ команды, печатающей приватный ключ. Совсем. Именно так
     ключи и уводят — «покажи dumpprivkey и пришли мне». Хочешь ключ — он на
     вкладке «Кошелёк», за осознанным действием, а не за строчкой, которую
     можно продиктовать по телефону.
  2. Трата денег требует ПОДТВЕРЖДЕНИЯ: `send` сначала показывает, сколько и
     кому, и просит повторить с `--yes`. Продиктованная строчка не уносит
     деньги с первого раза.
  3. Предупреждение (`WARNING`) показывается над консолью всегда.

Движок НЕ ЗНАЕТ про tkinter: на вход строка, на выход строка. Так его можно
проверять тестами, а окно остаётся тонкой оболочкой поверх — то же разделение,
что у `transport.py` и сети.
"""

import shlex

from .version import VERSION

__all__ = ["Console", "ConsoleError", "COMMANDS", "WARNING"]

WARNING = (
    "⚠️ Не вставляйте сюда команды, которые вам кто-то продиктовал. "
    "Мошенники просят «починить кошелёк командой из консоли» — и деньги "
    "уходят. Приватный ключ эта консоль не печатает никогда."
)

BANNER = (f"B-hydra Core {VERSION} — терминал. `help` — список команд, "
          f"`help <команда>` — подробности.")


class ConsoleError(Exception):
    """Команда не выполнена. Текст показывается пользователю как есть."""


def _fmt_amount(value) -> str:
    return f"{float(value):.4f}"


#: Длиннее этого значение уезжает на свою строку с отступом.
#: ⚠️ Нужно из-за хешей: они по 128 символов, и втиснутые в колонку они
#: переносились по ширине окна, разваливая всю таблицу. Замечено на живом
#: снимке окна, а не в рассуждениях.
_WIDE_VALUE = 48


def _table(rows, headers):
    """Простая таблица с выравниванием — консоль читают глазами.

    ⚠️ Выравнивание держится на МОНОШИРИННОМ шрифте окна: на пропорциональном
    пробелы разной ширины превращают колонки в кашу.
    """
    if not rows:
        return "(пусто)"
    cells = [[str(c) for c in row] for row in rows]
    widths = [len(str(h)) for h in headers]
    for row in cells:
        for i, cell in enumerate(row):
            if len(row) == 2 and i == 1 and len(cell) > _WIDE_VALUE:
                continue                 # длинные значения в ширину не входят
            widths[i] = max(widths[i], len(cell))

    out = ["  ".join(str(h).ljust(widths[i]) for i, h in enumerate(headers)),
           "  ".join("─" * w for w in widths)]
    for row in cells:
        if len(row) == 2 and len(row[1]) > _WIDE_VALUE:
            # Полное значение сохраняем целиком: из `getblock` хеш копируют,
            # чтобы тут же скормить его `gettx`. Обрезать было бы удобно
            # глазу и бесполезно на деле.
            # ⚠️ Отступ РОВНО два пробела, а не по ширине колонки: хеш в 128
            # символов и так занимает почти всю строку, и отступ под колонку
            # выталкивал его за край — окно переносило строку, и «аккуратный»
            # вывод выглядел хуже исходного.
            out.append(row[0])
            out.append("  " + row[1])
        else:
            out.append("  ".join(c.ljust(widths[i]) for i, c in enumerate(row)))
    return "\n".join(out)


class Console:
    """Разбор и выполнение команд терминала.

    `app` — источник состояния: узел, кошелёк, P2P. Передаётся объектом, а не
    кучей аргументов, потому что кошелёк в приложении МЕНЯЕТСЯ (его создают,
    импортируют, шифруют), и консоль обязана видеть текущий, а не тот, что был
    при её создании.
    """

    def __init__(self, app):
        self.app = app

    # --- Доступ к состоянию ----------------------------------------------------
    @property
    def node(self):
        node = getattr(self.app, "node", None)
        if node is None:
            raise ConsoleError("узел не готов")
        return node

    @property
    def wallet(self):
        wallet = getattr(self.app, "wallet", None)
        if wallet is None:
            raise ConsoleError(
                "кошелёк не загружен — создайте или импортируйте его на "
                "вкладке «Кошелёк»")
        return wallet

    # --- Разбор строки ---------------------------------------------------------
    def run(self, line: str) -> str:
        """Выполнить одну строку. Возвращает текст ответа."""
        text = (line or "").strip()
        if not text:
            return ""
        try:
            parts = shlex.split(text)
        except ValueError as error:
            # Незакрытая кавычка — обычная опечатка, а не повод для трассировки.
            raise ConsoleError(f"не разобрать строку: {error}") from error
        if not parts:
            return ""

        name, args = parts[0].lower(), parts[1:]
        handler = COMMANDS.get(name)
        if handler is None:
            near = ", ".join(sorted(n for n in COMMANDS if n.startswith(name[:2])))
            hint = f" Похожие: {near}." if near else ""
            raise ConsoleError(f"нет такой команды: {name}.{hint} "
                               f"Наберите `help`.")
        return handler.run(self, args)


class Command:
    def __init__(self, name, usage, summary, handler, details=""):
        self.name = name
        self.usage = usage
        self.summary = summary
        self.handler = handler
        self.details = details

    def run(self, console, args):
        return self.handler(console, args)


def _flag(args, name):
    """Вынимает флаг `--name` из списка. Возвращает (остаток, был ли флаг)."""
    rest = [a for a in args if a != name]
    return rest, len(rest) != len(args)


def _option(args, name):
    """Вынимает `--name значение`. Возвращает (остаток, значение или None)."""
    if name not in args:
        return args, None
    index = args.index(name)
    if index + 1 >= len(args):
        raise ConsoleError(f"у {name} не указано значение")
    return args[:index] + args[index + 2:], args[index + 1]


# --- Команды -------------------------------------------------------------------
def cmd_help(console, args):
    if args:
        command = COMMANDS.get(args[0].lower())
        if command is None:
            raise ConsoleError(f"нет такой команды: {args[0]}")
        text = f"{command.usage}\n  {command.summary}"
        return f"{text}\n\n{command.details}" if command.details else text
    rows = [(c.usage, c.summary) for c in
            sorted(set(COMMANDS.values()), key=lambda c: c.name)]
    return _table(rows, ["команда", "что делает"])


def cmd_version(console, args):
    from . import hashing
    return (f"B-hydra Core {VERSION}\n"
            f"движок хешей: {hashing.backend()}\n"
            f"RIPEMD-160  : {hashing.ripemd_backend()}")


def cmd_getinfo(console, args):
    node = console.node
    chain = node.blockchain
    wallet = getattr(console.app, "wallet", None)
    p2p = getattr(console.app, "p2p", None)
    rows = [
        ("версия", VERSION),
        ("высота", len(chain.chain)),
        ("сложность", chain.last_block.difficulty),
        ("работа цепочки", chain.total_work),
        ("генезис", chain.chain[0].hash[:32] + "…"),
        ("в мемпуле", len(node.mempool)),
        ("цепочка валидна", node.is_valid()),
    ]
    if wallet is not None:
        rows.append(("свой адрес", wallet.address))
        rows.append(("свой баланс", _fmt_amount(node.get_balance(wallet.address))))
    rows.append(("узел сети", "запущен" if p2p is not None else "не запущен"))
    if p2p is not None:
        rows.append(("соседей", len(p2p.peer_list())))
    return _table([(k, v) for k, v in rows], ["параметр", "значение"])


def cmd_getbalance(console, args):
    address = args[0] if args else getattr(console.wallet, "address", None)
    from .wallet import is_valid_address
    if not is_valid_address(address):
        raise ConsoleError(f"неверный адрес: {address}")
    return f"{address}\n  {_fmt_amount(console.node.get_balance(address))} BHY"


def cmd_getblock(console, args):
    if not args:
        raise ConsoleError("укажите номер блока: getblock 42")
    node = console.node
    try:
        index = int(args[0])
    except ValueError:
        raise ConsoleError(f"номер блока — целое число, а не {args[0]!r}") from None
    if index < 0:
        index = len(node.blockchain.chain) + index      # getblock -1 — последний
    # ⚠️ `get_block` отдаёт СЛОВАРЬ (`to_dict`), а не объект Block: обращение
    # через точку падало бы AttributeError на каждой команде.
    block = node.get_block(index)
    if block is None:
        raise ConsoleError(f"блока #{args[0]} нет "
                           f"(высота {len(node.blockchain.chain)})")
    body = block.get("data")
    data = body if isinstance(body, list) else [body]
    rows = [
        ("индекс", block.get("index")),
        ("hash", block.get("hash")),
        ("пред. блок", block.get("previous_hash")),
        ("merkle root", block.get("merkle_root")),
        ("метка времени", block.get("timestamp")),
        ("сложность", block.get("difficulty")),
        ("nonce", block.get("nonce")),
        ("транзакций", len(data)),
    ]
    message = node.block_message(index)
    if message:
        author = node.block_message_author(index)
        rows.append(("заметка майнера", message))
        rows.append(("автор заметки", author or "(без подписи)"))
    return _table(rows, ["поле", "значение"])


def cmd_gettx(console, args):
    if not args:
        raise ConsoleError("укажите txid: gettx <txid>")
    node = console.node
    found = node.find_transaction(args[0])
    block_index = None
    if found:
        # `find_transaction` отдаёт {"transaction": …, "block_index": …}.
        tx, block_index = found["transaction"], found["block_index"]
    else:
        # В цепочке нет — может лежать в мемпуле, неподтверждённой.
        tx = node.mempool.get(args[0]) if hasattr(node.mempool, "get") else None
        if tx is None:
            raise ConsoleError(f"транзакции {args[0][:16]}… нет ни в цепочке, "
                               f"ни в мемпуле")
    body = tx if isinstance(tx, dict) else tx.to_dict()
    lines = [f"txid        : {body.get('txid', '')}"]
    if block_index is not None:
        lines.append(f"блок        : #{block_index}")
    lines.append(f"входов      : {len(body.get('vin') or [])}")
    for item in body.get("vin") or []:
        lines.append(f"  ← {str(item.get('txid'))[:24]}…:{item.get('index')}")
    lines.append(f"выходов     : {len(body.get('vout') or [])}")
    for item in body.get("vout") or []:
        lines.append(f"  → {item.get('address')}  {_fmt_amount(item.get('amount', 0))} BHY")
    return "\n".join(lines)


def cmd_getaddress(console, args):
    address = args[0] if args else getattr(console.wallet, "address", None)
    from .wallet import is_valid_address
    if not is_valid_address(address):
        raise ConsoleError(f"неверный адрес: {address}")
    node = console.node
    utxos = node.find_spendable(address)
    history = node.address_history(address)
    received = sum(h["received"] for h in history)
    sent = sum(h["sent"] for h in history)
    lines = [
        f"адрес       : {address}",
        f"баланс      : {_fmt_amount(node.get_balance(address))} BHY",
        f"непотрачено : {len(utxos)} UTXO",
        f"история     : {len(history)} операций "
        f"(получено {_fmt_amount(received)}, потрачено {_fmt_amount(sent)})",
    ]
    for item in list(reversed(history))[:10]:
        mark = []
        if item["received"]:
            mark.append(f"+{_fmt_amount(item['received'])}")
        if item["sent"]:
            mark.append(f"-{_fmt_amount(item['sent'])}")
        lines.append(f"  блок #{item['block_index']:<6} {' '.join(mark):>20}  "
                     f"tx {item['txid'][:16]}…")
    return "\n".join(lines)


def cmd_mempool(console, args):
    node = console.node
    info = node.mempool_info()
    rows = [(k, v) for k, v in info.items()]
    text = _table(rows, ["параметр", "значение"])
    listing = []
    for tx in list(node.mempool.transactions)[:15]:
        body = tx if isinstance(tx, dict) else tx.to_dict()
        total = sum(float(o.get("amount", 0)) for o in body.get("vout") or [])
        listing.append((body.get("txid", "")[:20] + "…",
                        len(body.get("vin") or []),
                        len(body.get("vout") or []),
                        _fmt_amount(total)))
    if listing:
        text += "\n\n" + _table(listing, ["txid", "вх", "вых", "сумма"])
    return text


def cmd_chain(console, args):
    limit = 10
    if args:
        try:
            limit = int(args[0])
        except ValueError:
            raise ConsoleError(f"ожидалось число, а не {args[0]!r}") from None
    chain = console.node.blockchain.chain
    rows = []
    for block in chain[-max(1, limit):][::-1]:
        data = block.data if isinstance(block.data, list) else [block.data]
        rows.append((f"#{block.index}", block.hash[:24] + "…", len(data),
                     block.difficulty))
    return _table(rows, ["блок", "hash", "tx", "сложн."])


def cmd_mine(console, args):
    args, message = _option(args, "--message")
    address = args[0] if args else getattr(console.wallet, "address", None)
    from .wallet import is_valid_address
    if not is_valid_address(address):
        raise ConsoleError(f"неверный адрес майнера: {address}")
    node = console.node
    block = node.mine_pending(address, message=message)
    if block is None:
        raise ConsoleError("блок не добыт (майнинг прерван)")
    console.app.save_state()
    data = block.data if isinstance(block.data, list) else [block.data]
    return (f"Добыт блок #{block.index}\n"
            f"  транзакций : {len(data)}\n"
            f"  nonce      : {block.nonce}\n"
            f"  перебрано  : {block.mining_attempts:,} хешей\n"
            f"  hash       : {block.hash[:40]}…\n"
            f"  баланс     : {_fmt_amount(node.get_balance(address))} BHY")


def cmd_send(console, args):
    """⚠️ Единственная команда, которая ТРАТИТ ДЕНЬГИ — и потому с подтверждением."""
    args, confirmed = _flag(args, "--yes")
    args, fee_text = _option(args, "--fee")
    if len(args) < 2:
        raise ConsoleError("send <адрес> <сумма> [--fee N] --yes")

    from .blockchain import DEFAULT_FEE
    from .wallet import is_valid_address

    recipient = args[0]
    if not is_valid_address(recipient):
        raise ConsoleError(f"неверный адрес получателя: {recipient}")
    try:
        amount = float(args[1])
        fee = float(fee_text) if fee_text is not None else DEFAULT_FEE
    except ValueError:
        raise ConsoleError("сумма и комиссия — числа") from None
    if amount <= 0:
        raise ConsoleError("сумма должна быть больше нуля")
    if fee < 0:
        raise ConsoleError("комиссия не может быть отрицательной")

    wallet = console.wallet
    node = console.node
    balance = node.get_balance(wallet.address)
    if amount + fee > balance + 1e-9:
        raise ConsoleError(f"недостаточно средств: нужно "
                           f"{_fmt_amount(amount + fee)} BHY, "
                           f"доступно {_fmt_amount(balance)} BHY")

    if not confirmed:
        # ⚠️ Вот эта ветка и защищает от продиктованной строчки: с первого раза
        # деньги не уходят, а человек видит СУММУ и АДРЕС словами.
        return (f"ПОДТВЕРДИТЕ ПЕРЕВОД — деньги уйдут безвозвратно:\n"
                f"  кому    : {recipient}\n"
                f"  сумма   : {_fmt_amount(amount)} BHY\n"
                f"  комиссия: {_fmt_amount(fee)} BHY\n"
                f"  итого   : {_fmt_amount(amount + fee)} BHY\n"
                f"  останется: {_fmt_amount(balance - amount - fee)} BHY\n\n"
                f"Если это действительно вы и вы понимаете, что делаете, "
                f"повторите команду с --yes в конце.\n"
                f"Если команду вам кто-то продиктовал — НЕ ДЕЛАЙТЕ ЭТОГО.")

    tx = node.create_transaction(wallet, recipient, amount=amount, fee=fee)
    if tx is None:
        raise ConsoleError("не удалось собрать транзакцию из доступных UTXO")
    if not node.add_transaction(tx):
        raise ConsoleError("транзакция отклонена узлом "
                           "(неверная подпись или двойная трата)")
    console.app.save_state()
    console.app.broadcast_transaction(tx)
    return (f"Транзакция принята в мемпул\n"
            f"  txid : {tx.txid}\n"
            f"  {_fmt_amount(amount)} BHY → {recipient}\n"
            f"  комиссия {_fmt_amount(fee)} BHY\n"
            f"Ожидает попадания в блок.")


def cmd_peers(console, args):
    p2p = getattr(console.app, "p2p", None)
    if p2p is None:
        raise ConsoleError("узел сети не запущен — вкладка «Сеть»")
    rows = [(host, port) for host, port in p2p.peer_list()]
    return _table(rows, ["хост", "порт"]) if rows else "соседей нет"


def cmd_addpeer(console, args):
    if not args:
        raise ConsoleError("addpeer <хост:порт>")
    p2p = getattr(console.app, "p2p", None)
    if p2p is None:
        raise ConsoleError("узел сети не запущен — вкладка «Сеть»")
    from .transport import split_host_port
    try:
        host, port = split_host_port(args[0])
    except ValueError as error:
        raise ConsoleError(f"не разобрать адрес: {error}") from error
    p2p.add_peer(host, port)
    return f"добавлен сосед {host}:{port} (всего {len(p2p.peer_list())})"


def cmd_sync(console, args):
    p2p = getattr(console.app, "p2p", None)
    if p2p is None:
        raise ConsoleError("узел сети не запущен — вкладка «Сеть»")
    before = len(console.node.blockchain.chain)
    p2p.sync()
    after = len(console.node.blockchain.chain)
    if after == before:
        return f"синхронизация завершена, высота прежняя: {after}"
    return f"синхронизация завершена: {before} → {after} (+{after - before})"


def cmd_validate(console, args):
    ok = console.node.is_valid()
    return ("цепочка ВАЛИДНА" if ok else
            "цепочка НЕВАЛИДНА — это серьёзно, сообщите разработчику")


def cmd_update(console, args):
    from . import updater
    try:
        release, message = updater.update(dry_run=True)
    except updater.UpdateError as error:
        raise ConsoleError(str(error)) from error
    return message


def cmd_clear(console, args):
    # Обрабатывает окно; движку тут делать нечего, но команда обязана
    # существовать, иначе `clear` ругался бы «нет такой команды».
    return "\x00clear"


COMMANDS = {}


def _register(name, usage, summary, handler, details="", aliases=()):
    command = Command(name, usage, summary, handler, details)
    COMMANDS[name] = command
    for alias in aliases:
        COMMANDS[alias] = command
    return command


_register("help", "help [команда]", "список команд или справка по одной", cmd_help,
          aliases=("?",))
_register("version", "version", "версия и какие движки хешей работают", cmd_version)
_register("getinfo", "getinfo", "сводка: высота, работа, мемпул, баланс", cmd_getinfo,
          aliases=("info",))
_register("getbalance", "getbalance [адрес]", "баланс адреса (без адреса — свой)",
          cmd_getbalance, aliases=("balance",))
_register("getaddress", "getaddress [адрес]", "баланс, UTXO и история адреса",
          cmd_getaddress, aliases=("address",))
_register("getblock", "getblock <номер>", "содержимое блока (-1 — последний)",
          cmd_getblock, aliases=("block",))
_register("gettx", "gettx <txid>", "транзакция по идентификатору", cmd_gettx,
          aliases=("tx",))
_register("chain", "chain [сколько]", "последние блоки списком", cmd_chain)
_register("mempool", "mempool", "что ждёт подтверждения", cmd_mempool)
_register("mine", "mine [адрес] [--message текст]", "добыть блок", cmd_mine)
_register(
    "send", "send <адрес> <сумма> [--fee N] --yes", "перевести средства", cmd_send,
    details=(
        "⚠️ Команда тратит деньги, поэтому без --yes она лишь ПОКАЗЫВАЕТ, что\n"
        "произойдёт. Это защита от давней уловки: человеку диктуют строчку\n"
        "«для починки кошелька», он вставляет её в консоль — и средств нет.\n"
        "Если команду прислали вам со стороны, не выполняйте её вовсе."))
_register("peers", "peers", "список соседей узла", cmd_peers)
_register("addpeer", "addpeer <хост:порт>", "добавить соседа вручную", cmd_addpeer)
_register("sync", "sync", "догнать цепочку у соседей", cmd_sync)
_register("validate", "validate", "проверить цепочку целиком", cmd_validate)
_register("update", "update", "вышла ли новая версия B-hydra Core", cmd_update)
_register("clear", "clear", "очистить окно терминала", cmd_clear, aliases=("cls",))
