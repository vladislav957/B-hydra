"""B-hydra Core terminal — a command console inside the application.

The counterpart of Bitcoin Core's debug console (Help -> Debug window ->
Console): a window where you type a command and see the node's answer right
away, instead of clicking through tabs.

    > getinfo
    > getbalance BHY…
    > getblock 42
    > mine BHY… --message "hello"

⚠️ THIS IS NOT AN OS SHELL AND NOT PYTHON. There is no `eval`, no `exec` and
no launching of programs here, and there must never be: the command set is
CLOSED and lives entirely in `COMMANDS`. The console shares a process with the
wallet, so "run arbitrary code" would mean "hand the private key to anyone who
can dictate a line of text".

⚠️ AND THIS IS NOT PARANOIA, IT IS A WELL-KNOWN SCAM. The classic version:
somebody messages a newcomer "your wallet is corrupted, paste this command
into the console and it will be fixed" — and the coins are gone. Bitcoin Core
keeps a red warning above its console for a reason. So three things are done
here:

  1. There is NOT ONE command that prints the private key. None at all. That
     is exactly how keys get stolen — "run dumpprivkey and send me the
     output". If you want the key it is on the "Wallet" tab, behind a
     deliberate action, not behind a line someone can dictate over the phone.
  2. Spending money requires CONFIRMATION: `send` first shows how much and to
     whom, then asks you to repeat the command with `--yes`. A dictated line
     does not move money on the first try.
  3. The warning (`WARNING`) is always displayed above the console.

The engine KNOWS NOTHING about tkinter: a string goes in, a string comes out.
That is what makes it testable, and it keeps the window a thin shell on top —
the same separation `transport.py` has from the network.
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
    """The command failed. The text is shown to the user verbatim."""


def _fmt_amount(value) -> str:
    return f"{float(value):.4f}"


#: A value longer than this moves onto its own indented line.
#: ⚠️ Needed because of hashes: they are 128 characters long, and squeezed
#: into a column they wrapped at the window width and tore the whole table
#: apart. Spotted on a real screenshot of the window, not by reasoning.
_WIDE_VALUE = 48


def _table(rows, headers):
    """A simple aligned table — the console is read by eye.

    ⚠️ The alignment depends on the window's MONOSPACED font: with a
    proportional one, spaces of differing width turn the columns to mush.
    """
    if not rows:
        return "(пусто)"
    cells = [[str(c) for c in row] for row in rows]
    widths = [len(str(h)) for h in headers]
    for row in cells:
        for i, cell in enumerate(row):
            if len(row) == 2 and i == 1 and len(cell) > _WIDE_VALUE:
                continue                 # long values are exempt from width
            widths[i] = max(widths[i], len(cell))

    out = ["  ".join(str(h).ljust(widths[i]) for i, h in enumerate(headers)),
           "  ".join("─" * w for w in widths)]
    for row in cells:
        if len(row) == 2 and len(row[1]) > _WIDE_VALUE:
            # Keep the full value intact: people copy a hash out of
            # `getblock` to feed it straight into `gettx`. Truncating would
            # be easy on the eye and useless in practice.
            # ⚠️ The indent is EXACTLY two spaces, not the column width: a
            # 128-character hash already fills almost the whole line, and an
            # indent matching the column pushed it past the edge — the window
            # wrapped the line, and the "tidy" output looked worse than the
            # original.
            out.append(row[0])
            out.append("  " + row[1])
        else:
            out.append("  ".join(c.ljust(widths[i]) for i, c in enumerate(row)))
    return "\n".join(out)


class Console:
    """Parsing and execution of terminal commands.

    `app` is the source of state: the node, the wallet, P2P. It is passed as
    one object rather than a pile of arguments because the wallet inside the
    application CHANGES (it gets created, imported, encrypted), and the
    console must see the current one, not the one that existed when it was
    constructed.
    """

    def __init__(self, app):
        self.app = app

    # --- Access to state -------------------------------------------------------
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

    # --- Line parsing ----------------------------------------------------------
    def run(self, line: str) -> str:
        """Run a single line. Returns the response text."""
        text = (line or "").strip()
        if not text:
            return ""
        try:
            parts = shlex.split(text)
        except ValueError as error:
            # An unclosed quote is an ordinary typo, not a reason for a traceback.
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
    """Pulls the `--name` flag out of the list. Returns (rest, flag present)."""
    rest = [a for a in args if a != name]
    return rest, len(rest) != len(args)


def _option(args, name):
    """Pulls out `--name value`. Returns (rest, value or None)."""
    if name not in args:
        return args, None
    index = args.index(name)
    if index + 1 >= len(args):
        raise ConsoleError(f"у {name} не указано значение")
    return args[:index] + args[index + 2:], args[index + 1]


# --- Commands ------------------------------------------------------------------
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
        index = len(node.blockchain.chain) + index      # getblock -1 = the last
    # ⚠️ `get_block` returns a DICT (`to_dict`), not a Block object: attribute
    # access would raise AttributeError on every single command.
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
        # `find_transaction` returns {"transaction": …, "block_index": …}.
        tx, block_index = found["transaction"], found["block_index"]
    else:
        # Not in the chain — it may be sitting unconfirmed in the mempool.
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
    """⚠️ The only command that SPENDS MONEY — hence the confirmation step."""
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
        # ⚠️ This branch is what protects against a dictated line: money does
        # not move on the first try, and the person sees the AMOUNT and the
        # ADDRESS spelled out.
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
    # The window handles this; the engine has nothing to do here, but the
    # command must exist or `clear` would complain "no such command".
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
