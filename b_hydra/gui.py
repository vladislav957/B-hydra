"""
gui.py — десктоп-приложение B-hydra (tkinter).

Единое окно, объединяющее три части сети в одном ПО:
  * Кошелёк — создать/загрузить ключ, увидеть адрес и баланс, отправить перевод;
  * Майнинг — добывать блоки на свой адрес (в фоне, окно не зависает);
  * Сеть    — запустить P2P-узел, подключиться к пиру, синхронизироваться.

Запуск:
    python bhydra_gui.py

Требуется tkinter (входит в стандартный Python на Windows/macOS; на Linux —
пакет python3-tk). Ядро (кошелёк/майнинг/сеть) — модули пакета b_hydra.
"""

from __future__ import annotations

import os
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk


def _asset(name: str) -> str:
    """Путь к ресурсу (работает и из исходников, и из сборки PyInstaller)."""
    base = getattr(sys, "_MEIPASS",
                   os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, "assets", name)

if __name__ == "__main__" and __package__ in (None, ""):
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    __package__ = "b_hydra"

from . import __version__, hashing
from .blockchain import DEFAULT_FEE, TARGET_BLOCK_TIME
from .node import BHydraNode
from .p2p import P2PNode
from .wallet import Wallet, generate_wallet, is_valid_address

STATE_FILE = "bhydra_chain.json"
WALLET_FILE = "wallet.key"
SEEDS_FILE = "bhydra_seeds.txt"     # список seed-узлов (host:port), как в Bitcoin


class BHydraApp(tk.Tk):
    """Главное окно B-hydra Core — эталонного клиента сети (полный узел)."""

    def __init__(self) -> None:
        super().__init__()
        self.title("B-hydra Core — кошелёк · майнинг · сеть")
        self.geometry("680x560")
        self._set_icon()
        self._text_widgets: list[tk.Text] = []
        self._dark = tk.BooleanVar(value=False)

        # --- Состояние ---
        import os
        self.node = (BHydraNode.load(STATE_FILE)
                     if os.path.exists(STATE_FILE) else BHydraNode(difficulty=3))
        self.wallet: Wallet | None = None
        self.p2p: P2PNode | None = None
        self._mining = False             # PoW прямо сейчас идёт в фоне
        self._mining_on = False          # майнер включён (кнопкой)
        self._auto_after_id = None       # id таймера темпа сети (раз в секунду)
        self._net_sync_id = None         # id периодической авто-синхронизации
        self.autosync_var = tk.BooleanVar(value=True)   # автосинк включён по умолчанию
        self.discover_var = tk.BooleanVar(value=True)   # авто-поиск в сети (WiFi/LAN)
        self.seeds_var = tk.StringVar(value=self._load_seeds())  # seed-узлы
        # Очередь «фоновый поток → главный поток»: tkinter нельзя трогать из
        # чужого потока, поэтому воркер кладёт события сюда, а главный поток
        # забирает их в _poll_queue.
        self._queue: queue.Queue = queue.Queue()
        if os.path.exists(WALLET_FILE):
            try:
                self.wallet = Wallet.from_private_hex(
                    open(WALLET_FILE).read().strip())
            except (ValueError, OSError):
                self.wallet = None

        self._build_ui()
        self._refresh_status()
        self._refresh_blocks()
        self.after(150, self._poll_queue)        # опрос событий из воркера
        self.after(3000, self._auto_refresh)     # авто-обновление баланса/статуса

    # --- Интерфейс -------------------------------------------------------
    def _build_ui(self) -> None:
        # Тема оформления (clam — аккуратнее и одинаково на всех ОС).
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        # Меню «Вид» (тёмная тема) и «Справка».
        menubar = tk.Menu(self)
        viewm = tk.Menu(menubar, tearoff=0)
        viewm.add_checkbutton(label="Тёмная тема", variable=self._dark,
                              command=self._toggle_theme)
        menubar.add_cascade(label="Вид", menu=viewm)
        helpm = tk.Menu(menubar, tearoff=0)
        helpm.add_command(label="О программе", command=self._about)
        menubar.add_cascade(label="Справка", menu=helpm)
        self.config(menu=menubar)

        # Верхняя строка статуса.
        self.status = tk.StringVar()
        bar = ttk.Label(self, textvariable=self.status, relief="sunken",
                        anchor="w", padding=6)
        bar.pack(side="bottom", fill="x")

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=8, pady=8)
        self._nb = nb
        self._build_wallet_tab(nb)
        self._build_mining_tab(nb)
        self._build_network_tab(nb)
        self._build_mempool_tab(nb)
        self._build_blocks_tab(nb)
        self._text_widgets = [self.mine_log, self.net_log, self.block_details]

    def _build_wallet_tab(self, nb: ttk.Notebook) -> None:
        tab = ttk.Frame(nb, padding=12)
        nb.add(tab, text="💼 Кошелёк")

        btns = ttk.Frame(tab)
        btns.pack(fill="x")
        ttk.Button(btns, text="Создать кошелёк",
                   command=self._new_wallet).pack(side="left")
        ttk.Button(btns, text="Обновить баланс",
                   command=self._refresh_status).pack(side="left", padx=6)
        ttk.Button(btns, text="Сохранить в файл…",
                   command=self._save_wallet_as).pack(side="left")
        ttk.Button(btns, text="Загрузить из файла…",
                   command=self._load_wallet_from).pack(side="left", padx=6)

        self.addr_var = tk.StringVar()
        self.priv_var = tk.StringVar()
        self.bal_var = tk.StringVar()
        for label, var, copyable, qr in (("Адрес:", self.addr_var, True, True),
                                         ("Приватный ключ:", self.priv_var, True, False),
                                         ("Баланс:", self.bal_var, False, False)):
            row = ttk.Frame(tab)
            row.pack(fill="x", pady=4)
            ttk.Label(row, text=label, width=16).pack(side="left")
            ttk.Entry(row, textvariable=var, state="readonly").pack(
                side="left", fill="x", expand=True)
            if copyable:
                ttk.Button(row, text="Копировать", width=12,
                           command=lambda v=var, n=label: self._copy(v, n)).pack(
                    side="left", padx=(6, 0))
            if qr:
                ttk.Button(row, text="QR", width=4,
                           command=self._show_qr).pack(side="left", padx=(6, 0))

        # Импорт ключа.
        imp = ttk.LabelFrame(tab, text="Импорт по приватному ключу", padding=8)
        imp.pack(fill="x", pady=(10, 0))
        self.import_var = tk.StringVar()
        ttk.Entry(imp, textvariable=self.import_var).pack(
            side="left", fill="x", expand=True)
        ttk.Button(imp, text="Импорт", command=self._import_wallet).pack(
            side="left", padx=6)

        # Перевод.
        send = ttk.LabelFrame(tab, text="Отправить перевод", padding=8)
        send.pack(fill="x", pady=(10, 0))
        send.columnconfigure(1, weight=1)
        self.to_var = tk.StringVar()
        self.amount_var = tk.StringVar(value="10")
        self.fee_var = tk.StringVar(value=f"{DEFAULT_FEE:g}")
        ttk.Label(send, text="Кому (адрес):").grid(row=0, column=0, sticky="w")
        to_entry = ttk.Entry(send, textvariable=self.to_var, width=44)
        to_entry.grid(row=0, column=1, sticky="we", padx=4)
        self._add_paste_menu(to_entry)          # ПКМ → «Вставить» (на любой раскладке)
        ttk.Button(send, text="Вставить", width=10,
                   command=self._paste_recipient).grid(row=0, column=2, padx=2)
        ttk.Label(send, text="Сумма:").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Entry(send, textvariable=self.amount_var, width=12).grid(
            row=1, column=1, sticky="w", pady=4)
        fee_row = ttk.Frame(send)
        fee_row.grid(row=2, column=0, columnspan=3, sticky="w")
        ttk.Label(fee_row, text="Комиссия майнеру:").pack(side="left")
        ttk.Entry(fee_row, textvariable=self.fee_var, width=12).pack(
            side="left", padx=4)
        ttk.Label(fee_row, text="BHY (низкая, по стандарту крипты)").pack(side="left")
        ttk.Button(send, text="Отправить", command=self._send).grid(
            row=1, column=2, sticky="w", padx=2)

        # История операций: пополнения и отправки (от кого / куда, когда).
        hist = ttk.LabelFrame(tab, text="История операций", padding=8)
        hist.pack(fill="both", expand=True, pady=(10, 0))
        ttk.Label(hist, text="(двойной клик — копировать txid, правый клик — меню)",
                  foreground="gray").pack(side="bottom", anchor="w")
        tree_wrap = ttk.Frame(hist)
        tree_wrap.pack(side="top", fill="both", expand=True)
        cols = ("Дата", "Тип", "Сумма", "От кого / Кому", "Блок")
        self.history_tree = ttk.Treeview(tree_wrap, columns=cols, show="headings",
                                         height=7)
        widths = (130, 110, 120, 320, 55)
        for col, w in zip(cols, widths):
            self.history_tree.heading(col, text=col)
            self.history_tree.column(col, width=w,
                                     anchor="w" if col == "От кого / Кому"
                                     else "center")
        vsb = ttk.Scrollbar(tree_wrap, orient="vertical",
                            command=self.history_tree.yview)
        self.history_tree.configure(yscrollcommand=vsb.set)
        self.history_tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        # Клик по строке: меню «Копировать txid / адрес контрагента».
        self._history_meta = {}                 # iid → (txid, counterparty)
        self._history_menu = tk.Menu(self.history_tree, tearoff=0)
        self._history_menu.add_command(label="Копировать txid",
                                       command=lambda: self._copy_history("txid"))
        self._history_menu.add_command(label="Копировать адрес контрагента",
                                       command=lambda: self._copy_history("party"))
        self.history_tree.bind("<Button-3>", self._history_popup)
        self.history_tree.bind("<Double-1>", self._open_history_in_explorer)

    def _build_mining_tab(self, nb: ttk.Notebook) -> None:
        tab = ttk.Frame(nb, padding=12)
        nb.add(tab, text="⛏ Майнинг")

        # Никаких ручных настроек: майнер либо работает, либо нет. Темп задаёт
        # СЕТЬ (TARGET_BLOCK_TIME ≈ 48.6 мин на блок, как ретаргет сложности),
        # а не пользователь — иначе ломается расписание эмиссии.
        top = ttk.Frame(tab)
        top.pack(fill="x")
        self.mine_btn = ttk.Button(top, text="▶ Начать майнинг",
                                   command=self._toggle_mining)
        self.mine_btn.pack(side="left")
        ttk.Label(top,
                  text=f"Темп сети: один блок раз в "
                       f"~{TARGET_BLOCK_TIME / 60:.1f} мин.").pack(
            side="left", padx=10)

        self.mine_info = tk.StringVar()
        ttk.Label(tab, textvariable=self.mine_info).pack(anchor="w", pady=6)

        self.mine_status = tk.StringVar()
        ttk.Label(tab, textvariable=self.mine_status).pack(anchor="w")
        self.progress = ttk.Progressbar(tab, mode="determinate")
        self.progress.pack(fill="x", pady=4)

        self.mine_log = tk.Text(tab, height=14, state="disabled")
        self.mine_log.pack(fill="both", expand=True)

    def _build_network_tab(self, nb: ttk.Notebook) -> None:
        tab = ttk.Frame(nb, padding=12)
        nb.add(tab, text="🌐 Сеть")

        row = ttk.Frame(tab)
        row.pack(fill="x")
        ttk.Label(row, text="Хост:").pack(side="left")
        self.host_var = tk.StringVar(value="127.0.0.1")
        ttk.Entry(row, textvariable=self.host_var, width=14).pack(side="left", padx=4)
        ttk.Label(row, text="Порт:").pack(side="left")
        self.port_var = tk.StringVar(value="5000")
        ttk.Entry(row, textvariable=self.port_var, width=7).pack(side="left", padx=4)
        self.node_btn = ttk.Button(row, text="Запустить узел",
                                   command=self._toggle_node)
        self.node_btn.pack(side="left", padx=6)
        ttk.Button(row, text="Мой IP", command=self._show_my_ip).pack(
            side="left", padx=4)

        prow = ttk.Frame(tab)
        prow.pack(fill="x", pady=8)
        ttk.Label(prow, text="Пир (host:port):").pack(side="left")
        self.peer_var = tk.StringVar()
        ttk.Entry(prow, textvariable=self.peer_var, width=20).pack(side="left", padx=4)
        ttk.Button(prow, text="Подключиться", command=self._connect).pack(side="left")
        ttk.Button(prow, text="Проверить связь", command=self._test_peer).pack(
            side="left", padx=4)
        ttk.Button(prow, text="Синхронизировать", command=self._sync).pack(
            side="left", padx=6)
        ttk.Checkbutton(prow, text="Авто-синхронизация (каждые 5 с)",
                        variable=self.autosync_var).pack(side="left", padx=6)
        ttk.Checkbutton(prow, text="Авто-поиск в сети (WiFi)",
                        variable=self.discover_var).pack(side="left", padx=6)

        # Seed-узлы: адреса «для входа в сеть», к которым узел подключается сам
        # при старте (как DNS-сиды/вшитые узлы Bitcoin). Несколько — через запятую.
        seedrow = ttk.Frame(tab)
        seedrow.pack(fill="x", pady=(0, 6))
        ttk.Label(seedrow, text="Seed-узлы (host:port, через запятую):").pack(
            side="left")
        ttk.Entry(seedrow, textvariable=self.seeds_var).pack(
            side="left", fill="x", expand=True, padx=4)
        ttk.Button(seedrow, text="Сохранить", command=self._save_seeds).pack(
            side="left")

        self.net_log = tk.Text(tab, height=14, state="disabled")
        self.net_log.pack(fill="both", expand=True)

    def _build_mempool_tab(self, nb: ttk.Notebook) -> None:
        tab = ttk.Frame(nb, padding=12)
        nb.add(tab, text="🕓 Мемпул")

        top = ttk.Frame(tab)
        top.pack(fill="x")
        ttk.Button(top, text="Обновить", command=self._refresh_mempool).pack(
            side="left")
        self.mempool_info = tk.StringVar()
        ttk.Label(top, textvariable=self.mempool_info).pack(side="left", padx=10)

        ttk.Label(tab, foreground="gray",
                  text="Неподтверждённые переводы ждут майнинга. «Целевой блок» — "
                       "номер блока, в который они войдут при следующем майнинге.").pack(
            anchor="w", pady=(4, 6))

        cols = ("txid", "Сумма", "Комиссия", "Кому", "Целевой блок")
        self.mempool_tree = ttk.Treeview(tab, columns=cols, show="headings",
                                         height=12)
        for col, w in zip(cols, (250, 110, 100, 250, 110)):
            self.mempool_tree.heading(col, text=col)
            self.mempool_tree.column(col, width=w,
                                     anchor="center" if col in ("Сумма", "Комиссия",
                                                                "Целевой блок") else "w")
        self.mempool_tree.pack(fill="both", expand=True, pady=6)
        # Клик по строке: детали (двойной) и копирование txid (правый клик).
        self._mempool_meta = {}            # iid → txid (полный)
        self._mempool_menu = tk.Menu(self.mempool_tree, tearoff=0)
        self._mempool_menu.add_command(label="Показать детали",
                                       command=self._show_mempool_tx)
        self._mempool_menu.add_command(label="Копировать txid",
                                       command=self._copy_mempool_txid)
        self.mempool_tree.bind("<Double-1>", lambda e: self._show_mempool_tx())
        self.mempool_tree.bind("<Button-3>", self._mempool_popup)
        ttk.Label(tab, foreground="gray",
                  text="(двойной клик — детали транзакции, правый клик — меню)").pack(
            anchor="w")

    def _refresh_mempool(self) -> None:
        """Обновить таблицу мемпула (по умолчанию — при каждом refresh)."""
        tree = getattr(self, "mempool_tree", None)
        if tree is None:
            return
        tree.delete(*tree.get_children())
        self._mempool_meta = {}
        info = self.node.mempool_info()
        n = info["pending"]
        target = info["target_block"]
        if n:
            self.mempool_info.set(
                f"В очереди: {n} транзакц. → войдут в блок #{target}")
        else:
            self.mempool_info.set(
                f"Мемпул пуст. Следующий блок будет #{target}.")
        for t in info["transactions"]:
            fee = "—" if t["fee"] is None else f"{t['fee']:.4f}"
            party = t["recipients"][0] if t["recipients"] else "— себе —"
            party = party if len(party) <= 30 else party[:24] + "…"
            iid = tree.insert("", "end", values=(
                t["txid"][:28] + "…",
                f"{t['amount']:.4f} BHY",
                fee,
                party,
                f"#{t['target_block']}",
            ))
            self._mempool_meta[iid] = t["txid"]        # полный txid для копии/деталей

    def _mempool_popup(self, event) -> None:
        row = self.mempool_tree.identify_row(event.y)
        if row:
            self.mempool_tree.selection_set(row)
            self._mempool_menu.tk_popup(event.x_root, event.y_root)

    def _selected_mempool_tx(self):
        sel = self.mempool_tree.selection()
        if not sel:
            return None, None
        txid = self._mempool_meta.get(sel[0])
        for tx in self.node.mempool.transactions:
            if tx.txid == txid:
                return txid, tx
        return txid, None

    def _copy_mempool_txid(self) -> None:
        txid, _ = self._selected_mempool_tx()
        if not txid:
            return
        self.clipboard_clear()
        self.clipboard_append(txid)
        self.update()
        self.status.set(f"Скопирован txid: {txid[:24]}…")

    def _show_mempool_tx(self) -> None:
        """Окно с деталями неподтверждённой транзакции (входы/выходы)."""
        txid, tx = self._selected_mempool_tx()
        if tx is None:
            return
        d = tx.to_dict()
        target = len(self.node.blockchain.chain)
        lines = [f"Транзакция (в мемпуле, ждёт блок #{target})",
                 f"txid: {txid}", "",
                 f"Входов: {len(d.get('vin', []))}"]
        for inp in d.get("vin", []):
            lines.append(f"  ← {inp.get('txid', '')[:24]}…:{inp.get('index')}")
        lines.append(f"Выходов: {len(d.get('vout', []))}")
        for o in d.get("vout", []):
            lines.append(f"  → {o['amount']} BHY  {o['address']}")

        win = tk.Toplevel(self)
        win.title("Детали транзакции (мемпул)")
        win.transient(self)
        txt = tk.Text(win, width=82, height=min(20, 6 + len(d.get("vin", []))
                                                + len(d.get("vout", []))), wrap="none")
        txt.pack(fill="both", expand=True, padx=10, pady=10)
        txt.insert("end", "\n".join(lines))
        txt.config(state="disabled")
        ttk.Button(win, text="Копировать txid",
                   command=self._copy_mempool_txid).pack(pady=(0, 6))
        ttk.Button(win, text="Закрыть", command=win.destroy).pack(pady=(0, 10))

    def _build_blocks_tab(self, nb: ttk.Notebook) -> None:
        tab = ttk.Frame(nb, padding=12)
        nb.add(tab, text="🔍 Блоки")
        self._blocks_tab = tab

        top = ttk.Frame(tab)
        top.pack(fill="x")
        ttk.Button(top, text="Обновить", command=self._refresh_blocks).pack(side="left")
        ttk.Button(top, text="Экспорт цепочки…",
                   command=self._export_chain).pack(side="left", padx=6)
        self.blocks_info = tk.StringVar()
        ttk.Label(top, textvariable=self.blocks_info).pack(side="left", padx=10)

        # Поиск по высоте / адресу (BHY…) / txid — как в кошельке Bitcoin.
        srow = ttk.Frame(tab)
        srow.pack(fill="x", pady=(4, 0))
        ttk.Label(srow, text="Поиск:").pack(side="left")
        self.search_var = tk.StringVar()
        ent = ttk.Entry(srow, textvariable=self.search_var)
        ent.pack(side="left", fill="x", expand=True, padx=4)
        ent.bind("<Return>", lambda _e: self._search())
        ttk.Button(srow, text="Найти", command=self._search).pack(side="left")

        cols = ("idx", "tx", "miner", "hash")
        self.blocks_tree = ttk.Treeview(tab, columns=cols, show="headings", height=12)
        for col, title, width in (("idx", "№", 50), ("tx", "tx", 40),
                                  ("miner", "майнер", 220), ("hash", "hash", 280)):
            self.blocks_tree.heading(col, text=title)
            self.blocks_tree.column(col, width=width, anchor="w")
        self.blocks_tree.pack(fill="both", expand=True, pady=6)
        self.blocks_tree.bind("<<TreeviewSelect>>", self._show_block_details)

        self.block_details = tk.Text(tab, height=10, state="disabled")
        self.block_details.pack(fill="x")
        self.block_details.tag_configure("hl", background="#fff3a0",
                                         foreground="#000000")

    def _refresh_blocks(self) -> None:
        self.blocks_tree.delete(*self.blocks_tree.get_children())
        for block in reversed(self.node.blockchain.chain):
            miner = self.node.blockchain._miner_of(block) or "— генезис —"
            n_tx = len(block.data) if isinstance(block.data, list) else 1
            self.blocks_tree.insert(
                "", "end", iid=str(block.index),
                values=(block.index, n_tx,
                        miner[:18] + "…" if len(miner) > 19 else miner,
                        block.hash[:40] + "…"))
        self.blocks_info.set(f"всего блоков: {self.node.height} | "
                             f"эмиссия: {self.node.blockchain.total_supply:.0f} BHY")

    def _search(self) -> None:
        q = self.search_var.get().strip()
        if not q:
            return
        if q.isdigit():                                   # высота блока
            iid = str(int(q))
            if self.blocks_tree.exists(iid):
                self.blocks_tree.selection_set(iid)
                self.blocks_tree.see(iid)
                self._show_block_details()
            else:
                messagebox.showinfo("Поиск", f"Блока #{q} нет в цепочке.")
        elif q.startswith("BHY"):                         # адрес
            bal = self.node.get_balance(q)
            hist = self.node.address_history(q)
            messagebox.showinfo(
                "Адрес", f"{q}\n\nБаланс: {bal:.4f} BHY\n"
                         f"Операций в истории: {len(hist)}")
        else:                                             # txid
            found = self.node.find_transaction(q)
            if found:
                tx = found["transaction"]
                outs = "\n".join(f"  {o['amount']} BHY → {o['address'][:24]}…"
                                 for o in tx["vout"])
                messagebox.showinfo(
                    "Транзакция", f"txid {q[:24]}…\nв блоке #{found['block_index']}"
                                  f"\n\nвыходы:\n{outs}")
            else:
                messagebox.showinfo("Поиск", "Транзакция с таким txid не найдена.")

    def _show_block_details(self, _event=None) -> None:
        sel = self.blocks_tree.selection()
        if not sel:
            return
        block = self.node.blockchain.chain[int(sel[0])]
        d = block.to_dict()
        lines = [
            f"Блок #{d['index']}",
            f"  hash         : {d['hash']}",
            f"  previous_hash: {d['previous_hash']}",
            f"  merkle_root  : {d['merkle_root']}",
            f"  target       : {d['target'][:32]}…",
            f"  nonce        : {d['nonce']}   | сложность: {d['difficulty']} | "
            f"работа: {d['work']}",
            f"  транзакций   : {len(d['data']) if isinstance(d['data'], list) else 1}",
        ]
        self.block_details.config(state="normal")
        self.block_details.delete("1.0", "end")
        self.block_details.insert("end", "\n".join(lines))
        self.block_details.config(state="disabled")

    # --- Логика: кошелёк -------------------------------------------------
    def _save_wallet_as(self) -> None:
        if self.wallet is None:
            return messagebox.showwarning("Кошелёк", "Сначала создайте кошелёк.")
        path = filedialog.asksaveasfilename(
            defaultextension=".key", initialfile="bhydra_wallet.key",
            filetypes=[("Ключ B-hydra", "*.key"), ("Все файлы", "*.*")])
        if path:
            try:
                with open(path, "w") as fh:
                    fh.write(self.wallet.private_key_hex)
                messagebox.showinfo("Кошелёк", f"Сохранён в {path}")
            except OSError as exc:
                messagebox.showerror("Ошибка", str(exc))

    def _load_wallet_from(self) -> None:
        path = filedialog.askopenfilename(
            filetypes=[("Ключ B-hydra", "*.key"), ("Все файлы", "*.*")])
        if not path:
            return
        try:
            self.wallet = Wallet.from_private_hex(open(path).read().strip())
            with open(WALLET_FILE, "w") as fh:
                fh.write(self.wallet.private_key_hex)
            self._refresh_status()
            messagebox.showinfo("Кошелёк", f"Загружен: {self.wallet.address}")
        except (ValueError, OSError):
            messagebox.showerror("Ошибка", "Не удалось загрузить кошелёк из файла.")

    def _copy(self, var: tk.StringVar, label: str) -> None:
        """Скопировать значение поля в буфер обмена."""
        value = var.get().strip()
        if not value:
            return messagebox.showwarning("Кошелёк", "Сначала создайте кошелёк.")
        self.clipboard_clear()
        self.clipboard_append(value)
        self.update()  # удержать буфер после закрытия окна
        self.status.set(f"{label.rstrip(':')} скопирован в буфер обмена.")

    def _paste_recipient(self) -> None:
        """Вставить адрес из буфера обмена в поле получателя."""
        try:
            text = self.clipboard_get()
        except tk.TclError:
            return messagebox.showinfo("Вставить", "Буфер обмена пуст.")
        self.to_var.set(text.strip())

    def _add_paste_menu(self, entry: tk.Widget) -> None:
        """Контекстное меню по правой кнопке: Вставить/Копировать/Вырезать.

        Нужно, потому что на русской раскладке Ctrl+V иногда не срабатывает —
        а правый клик работает всегда.
        """
        menu = tk.Menu(entry, tearoff=0)
        menu.add_command(label="Вставить",
                         command=lambda: entry.event_generate("<<Paste>>"))
        menu.add_command(label="Копировать",
                         command=lambda: entry.event_generate("<<Copy>>"))
        menu.add_command(label="Вырезать",
                         command=lambda: entry.event_generate("<<Cut>>"))
        menu.add_separator()
        menu.add_command(label="Выделить всё",
                         command=lambda: entry.select_range(0, "end"))

        def popup(event):
            menu.tk_popup(event.x_root, event.y_root)

        entry.bind("<Button-3>", popup)          # ПКМ (Windows/Linux)
        entry.bind("<Button-2>", popup)          # средняя кнопка (на всякий)

    def _show_qr(self) -> None:
        """Показать QR-код адреса в отдельном окне (для сканирования телефоном)."""
        if self.wallet is None:
            return messagebox.showwarning("Кошелёк", "Сначала создайте кошелёк.")
        from .qrcode_gen import qr_matrix
        addr = self.wallet.address
        rows = qr_matrix(addr)
        n = len(rows)
        scale, quiet = 8, 4
        side = (n + 2 * quiet) * scale

        win = tk.Toplevel(self)
        win.title("QR-код адреса")
        win.resizable(False, False)
        win.transient(self)
        canvas = tk.Canvas(win, width=side, height=side,
                           bg="white", highlightthickness=0)
        canvas.pack(padx=12, pady=12)
        for r in range(n):
            for c in range(n):
                if rows[r][c] == "1":
                    x = (c + quiet) * scale
                    y = (r + quiet) * scale
                    canvas.create_rectangle(x, y, x + scale, y + scale,
                                            fill="black", outline="")
        ttk.Label(win, text=addr, font=("TkDefaultFont", 9)).pack(pady=(0, 4))
        ttk.Button(win, text="Закрыть", command=win.destroy).pack(pady=(0, 10))

    def _new_wallet(self) -> None:
        self.wallet = generate_wallet()
        try:
            with open(WALLET_FILE, "w") as fh:
                fh.write(self.wallet.private_key_hex)
        except OSError:
            pass
        self._refresh_status()

    def _import_wallet(self) -> None:
        raw = self.import_var.get().strip()
        if not raw:
            return messagebox.showwarning(
                "Импорт", "Вставьте приватный ключ (64 hex-символа) в поле.")
        try:
            self.wallet = Wallet.from_private_hex(raw)
        except ValueError as exc:
            return messagebox.showerror("Неверный приватный ключ", str(exc))
        try:
            with open(WALLET_FILE, "w") as fh:
                fh.write(self.wallet.private_key_hex)
        except OSError:
            pass
        self.import_var.set("")                 # очистить поле после импорта
        self._refresh_status()
        self.status.set("Кошелёк импортирован.")
        messagebox.showinfo("Импорт", f"Кошелёк загружен:\n{self.wallet.address}")

    def _send(self) -> None:
        if self.wallet is None:
            return messagebox.showwarning("Кошелёк", "Сначала создайте кошелёк.")
        to = self.to_var.get().strip()
        if not to:
            return messagebox.showwarning(
                "Перевод", "Введите адрес получателя (начинается с BHY…).")
        if not is_valid_address(to):
            return messagebox.showerror(
                "Неверный адрес",
                "Адрес получателя некорректен.\nОн должен начинаться с «BHY» и "
                "быть скопирован полностью (без …, без пробелов).")
        try:
            amount = float(self.amount_var.get().replace(",", "."))
        except ValueError:
            return messagebox.showerror("Ошибка", "Сумма должна быть числом.")
        if amount <= 0:
            return messagebox.showerror("Ошибка", "Сумма должна быть больше нуля.")
        try:
            fee = float(self.fee_var.get().replace(",", "."))
        except ValueError:
            return messagebox.showerror("Ошибка", "Комиссия должна быть числом.")
        if fee < 0:
            return messagebox.showerror("Ошибка", "Комиссия не может быть отрицательной.")

        balance = self.node.get_balance(self.wallet.address)
        if amount + fee > balance:
            return messagebox.showwarning(
                "Недостаточно средств",
                f"На балансе {balance:.4f} BHY, а нужно {amount + fee:.4f} BHY "
                f"({amount:.4f} перевод + {fee:g} комиссия).\n\n"
                "Сначала намайните монеты на свой адрес во вкладке ⛏ Майнинг.")

        tx = self.node.create_transaction(self.wallet, to, amount, fee=fee)
        if tx is None or not self.node.add_transaction(tx):
            return messagebox.showwarning(
                "Перевод", "Не удалось создать перевод (средства уже зарезервированы "
                "в мемпуле?). Попробуйте после майнинга.")
        if self.p2p and self.p2p._running:
            self.p2p.broadcast({"type": "transaction", "transaction": tx.to_dict(),
                                "from": [self.p2p.host, self.p2p.port]})
        self.node.save(STATE_FILE)
        self.to_var.set("")                     # очистить поле адреса после отправки
        self.status.set("Перевод отправлен в мемпул.")
        messagebox.showinfo("Перевод отправлен",
                            f"{amount:.4f} BHY → {to[:20]}…\n"
                            f"комиссия майнеру: {fee:g} BHY\n\n"
                            f"Транзакция в мемпуле: {tx.txid[:24]}…\n"
                            "Будет подтверждена при майнинге следующего блока.")
        self._refresh_status()

    # --- Логика: майнинг в темпе сети ------------------------------------
    # Блок добывается, когда наступает его срок: время вершины цепочки +
    # TARGET_BLOCK_TIME (≈48.6 мин). Отстала цепочка — блок добывается сразу,
    # дальше — строго по расписанию. Никакого «добыть N блоков» вручную:
    # темп эмиссии контролирует сеть, а не пользователь.
    def _toggle_mining(self) -> None:
        if self._mining_on:
            self._mining_on = False
            if self._auto_after_id is not None:
                self.after_cancel(self._auto_after_id)
                self._auto_after_id = None
            self.mine_btn.config(text="▶ Начать майнинг")
            self.progress.config(value=0)
            self.mine_status.set("Майнинг остановлен.")
            self._log(self.mine_log, "⏹ Майнинг остановлен.")
            return
        if self.wallet is None:
            return messagebox.showwarning("Кошелёк", "Сначала создайте кошелёк.")
        self._mining_on = True
        self.mine_btn.config(text="⏹ Остановить майнинг")
        self._log(self.mine_log,
                  f"▶ Майнинг запущен. Темп сети: блок раз в "
                  f"~{TARGET_BLOCK_TIME / 60:.1f} мин.")
        self._mining_tick()

    def _next_block_due(self) -> float:
        """Когда пора добывать следующий блок: вершина + целевой темп сети."""
        return self.node.blockchain.last_block.timestamp + TARGET_BLOCK_TIME

    def _mining_tick(self) -> None:
        """Раз в секунду: обновляет обратный отсчёт; в срок — добывает блок."""
        if not self._mining_on:
            return
        if not self._mining:
            wait = self._next_block_due() - time.time()
            if wait <= 0:
                self._begin_mining()
            else:
                self.mine_status.set(
                    f"Следующий блок через {int(wait // 60)}:"
                    f"{int(wait % 60):02d} (темп сети)")
                self.progress.config(maximum=TARGET_BLOCK_TIME,
                                     value=TARGET_BLOCK_TIME - wait)
        self._auto_after_id = self.after(1000, self._mining_tick)

    def _begin_mining(self) -> bool:
        """Фоновая добыча одного блока (PoW в отдельном потоке)."""
        if self.wallet is None or self._mining:
            return False
        self._mining = True
        self.progress.config(maximum=100, value=100)
        self.mine_status.set("Майнинг: перебор nonce…")
        threading.Thread(target=self._mine_worker, daemon=True).start()
        return True

    def _mine_worker(self) -> None:
        # Только майнинг и запись в очередь — НИКАКИХ обращений к tkinter.
        if self.p2p and self.p2p._running:
            # Сначала встаём на самую длинную цепочку сети, потом майним
            # поверх неё — иначе свой блок «улетит» в форк и не примется.
            try:
                self.p2p.sync()
            except Exception:
                pass
            block = self.p2p.mine(self.wallet.address)   # майнит + рассылает
        else:
            block = self.node.mine_pending(self.wallet.address)
        self.node.save(STATE_FILE)
        self._queue.put(("mined", block.index,
                         block.mining_attempts, block.nonce))

    def _poll_queue(self) -> None:
        """Главный поток: забирает события воркера и обновляет интерфейс."""
        try:
            while True:
                msg = self._queue.get_nowait()
                if msg[0] == "mined":
                    _, idx, attempts, nonce = msg
                    self._mining = False
                    self.progress.config(value=0)
                    self._log(self.mine_log, f"⛏ блок #{idx} | перебрано "
                                             f"{attempts} хешей | nonce {nonce}")
                    self.mine_status.set(f"Блок #{idx} добыт ✓ — следующий "
                                         "по темпу сети.")
                    self._refresh_status()
                    self._refresh_blocks()
                elif msg[0] == "synced":
                    self._log(self.net_log,
                              f"🔄 Авто-синхронизация: подтянута цепочка, "
                              f"высота {msg[1]}")
                    self._refresh_status()
                    self._refresh_blocks()
                elif msg[0] == "bootstrap":
                    _, ok, total, height = msg
                    self._log(self.net_log,
                              f"🌱 Seed-узлы: подключено {ok} из {total} | "
                              f"пиров: {len(self.p2p.peers) if self.p2p else 0} | "
                              f"высота: {height}")
                    self._refresh_status()
                    self._refresh_blocks()
                elif msg[0] == "discovered":
                    _, host, port = msg
                    self._log(self.net_log,
                              f"🛰 Найден узел в сети: {host}:{port} — подключаюсь.")
                    self._refresh_status()
                    self._refresh_blocks()
                elif msg[0] == "conntest":
                    _, host, port, ok, err = msg
                    if ok:
                        self._log(self.net_log,
                                  f"✅ {host}:{port} доступен — узел там есть, "
                                  "можно «Подключиться».")
                    else:
                        self._log(self.net_log,
                                  f"❌ {host}:{port} не отвечает. Причины: узел там "
                                  "не запущен / другая сеть / брандмауэр / неверный IP.")
        except queue.Empty:
            pass
        self.after(150, self._poll_queue)

    # --- Логика: сеть ----------------------------------------------------
    @staticmethod
    def _local_ip() -> str:
        """IP компьютера в локальной сети (для связи с другими машинами)."""
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))      # коннект не шлёт пакетов, лишь даёт IP
            return s.getsockname()[0]
        except OSError:
            return "127.0.0.1"
        finally:
            s.close()

    @staticmethod
    def _load_seeds() -> str:
        try:
            with open(SEEDS_FILE, encoding="utf-8") as fh:
                return fh.read().strip()
        except OSError:
            return ""

    def _save_seeds(self) -> None:
        try:
            with open(SEEDS_FILE, "w", encoding="utf-8") as fh:
                fh.write(self.seeds_var.get().strip())
            self.status.set("Seed-узлы сохранены.")
        except OSError:
            pass

    def _parse_seeds(self):
        """Разбирает поле seed-узлов в список (host, port)."""
        seeds = []
        for token in self.seeds_var.get().replace(";", ",").split(","):
            token = token.strip()
            if ":" not in token:
                continue
            host, _, port = token.rpartition(":")
            host = host.strip()
            try:
                seeds.append((host, int(port.strip())))
            except ValueError:
                continue
        return seeds

    def _bootstrap_seeds(self) -> None:
        """Подключиться ко всем seed-узлам в фоне (как bootstrap у Bitcoin)."""
        seeds = self._parse_seeds()
        if not seeds:
            return
        self._save_seeds()
        threading.Thread(target=self._do_bootstrap, args=(seeds,),
                         daemon=True).start()

    def _do_bootstrap(self, seeds) -> None:
        me = (self.host_var.get().strip(), int(self.port_var.get()))
        ok = 0
        for host, port in seeds:
            if (host, port) == me:
                continue
            try:
                self.p2p.connect(host, port)
                ok += 1
            except OSError:
                continue
        self._queue.put(("bootstrap", ok, len(seeds), self.node.height))

    def _show_my_ip(self) -> None:
        ip = self._local_ip()
        self.host_var.set(ip)               # сразу подставим в «Хост»
        messagebox.showinfo(
            "Мой IP в сети",
            f"Ваш адрес в локальной сети: {ip}\n\n"
            f"Чтобы вас нашёл другой участник, дайте ему: {ip}:{self.port_var.get()}\n\n"
            "127.0.0.1 — это «только мой компьютер». Для связи между РАЗНЫМИ "
            "компьютерами используйте этот IP, а не 127.0.0.1.\n"
            "(Оба должны быть в одной сети Wi-Fi/роутере; брандмауэр Windows "
            "может спросить разрешение — нажмите «Разрешить».)")

    def _toggle_node(self) -> None:
        if self.p2p and self.p2p._running:
            self.p2p.stop()
            if self._net_sync_id is not None:
                self.after_cancel(self._net_sync_id)
                self._net_sync_id = None
            self.node_btn.config(text="Запустить узел")
            self._log(self.net_log, "Узел остановлен.")
        else:
            host, port = self.host_var.get(), int(self.port_var.get())
            self.p2p = P2PNode(host, port, node=self.node)
            self.p2p.start()
            self.node_btn.config(text="Остановить узел")
            self._log(self.net_log, f"Узел запущен на {host}:{port}")
            if host in ("127.0.0.1", "localhost"):
                self._log(self.net_log,
                          "ℹ Хост 127.0.0.1 — виден только на этом компьютере. "
                          "Для связи с другой машиной нажмите «Мой IP».")
            else:
                self._log(self.net_log,
                          f"➡ Дайте другому участнику адрес: {host}:{port}")
            if self._parse_seeds():
                self._log(self.net_log, "🌱 Подключаюсь к seed-узлам…")
                self._bootstrap_seeds()      # авто-вход в сеть через seed-узлы
            if self.discover_var.get():
                # Колбэк из потока поиска → в очередь главного потока.
                self.p2p.on_discover = lambda h, p: self._queue.put(
                    ("discovered", h, p))
                self.p2p.start_discovery()   # авто-поиск узлов по WiFi/LAN (UDP)
                self._log(self.net_log,
                          "🛰 Авто-поиск в сети включён — узлы найдутся сами.")
            self._net_autosync()             # начать периодически подтягивать цепочку
        self._refresh_status()

    def _net_autosync(self) -> None:
        """Каждые 5 с тихо подтягивает у пиров самую длинную/тяжёлую цепочку.

        Сетевые вызовы блокирующие, поэтому идут в фоновом потоке, а результат
        возвращается в главный поток через очередь (_poll_queue)."""
        if not (self.p2p and self.p2p._running):
            self._net_sync_id = None
            return
        # Тикаем, только пока галочка включена; во время майнинга цепочку не
        # трогаем (иначе гонка с воркером).
        if self.autosync_var.get() and not self._mining and self.p2p.peers:
            threading.Thread(target=self._do_autosync, daemon=True).start()
        self._net_sync_id = self.after(5000, self._net_autosync)

    def _do_autosync(self) -> None:
        try:
            self.p2p.discover_peers()        # сплетни: узнать новых пиров
            changed = self.p2p.sync()
        except Exception:                    # сеть могла отвалиться — не падаем
            changed = False
        if changed:
            self._queue.put(("synced", self.node.height))

    def _connect(self) -> None:
        if not (self.p2p and self.p2p._running):
            return messagebox.showwarning("Сеть", "Сначала запустите узел.")
        raw = self.peer_var.get().strip()
        if ":" not in raw:
            return messagebox.showerror(
                "Сеть", "Адрес пира в формате host:port, например 127.0.0.1:5001")
        host, port_s = raw.rsplit(":", 1)
        try:
            port = int(port_s)
        except ValueError:
            return messagebox.showerror("Сеть", "Порт должен быть числом (например 5001).")
        if (host, port) == (self.host_var.get().strip(), int(self.port_var.get())):
            return messagebox.showwarning(
                "Сеть", "Это адрес ЭТОГО же узла. Нужен адрес ДРУГОГО узла.")
        try:
            self.p2p.connect(host, port)
            self._refresh_blocks()
            self._log(self.net_log, f"Подключено к {host}:{port} | "
                                    f"пиров: {len(self.p2p.peers)} | "
                                    f"высота: {self.node.height}")
            self._refresh_status()
        except ConnectionRefusedError:
            messagebox.showerror(
                "Узел не найден",
                f"По адресу {host}:{port} никто не отвечает.\n\n"
                "Похоже, второй узел там не запущен. Чтобы появилась сеть, нужен "
                "ВТОРОЙ узел:\n"
                "1) Запусти второй экземпляр B-hydra в ДРУГОЙ папке;\n"
                f"2) на нём укажи Порт {port} и нажми «Запустить узел»;\n"
                "3) только потом здесь нажми «Подключиться».")
        except (ValueError, OSError) as exc:
            messagebox.showerror("Сеть", f"Не удалось подключиться: {exc}")

    def _test_peer(self) -> None:
        """Проверить, доступен ли узел по адресу из поля «Пир» (диагностика)."""
        raw = self.peer_var.get().strip()
        if ":" not in raw:
            return messagebox.showerror(
                "Проверка связи", "Адрес в формате host:port, например 192.168.1.50:5000")
        host, _, port_s = raw.rpartition(":")
        try:
            port = int(port_s)
        except ValueError:
            return messagebox.showerror("Проверка связи", "Порт должен быть числом.")
        self._log(self.net_log, f"🔎 Проверяю связь с {host}:{port}…")
        threading.Thread(target=self._do_test_peer, args=(host.strip(), port),
                         daemon=True).start()

    def _do_test_peer(self, host: str, port: int) -> None:
        import socket
        try:
            with socket.create_connection((host, port), timeout=5):
                self._queue.put(("conntest", host, port, True, ""))
        except (OSError, ValueError) as exc:
            self._queue.put(("conntest", host, port, False, str(exc)))

    def _sync(self) -> None:
        if not (self.p2p and self.p2p._running):
            return messagebox.showwarning("Сеть", "Сначала запустите узел.")
        replaced = self.p2p.sync()
        self._log(self.net_log,
                  f"Синхронизация: {'обновлено' if replaced else 'уже актуально'} "
                  f"| высота: {self.node.height}")
        self._refresh_status()

    # --- Вспомогательное -------------------------------------------------
    def _log(self, widget: tk.Text, text: str) -> None:
        widget.config(state="normal")
        widget.insert("end", text + "\n")
        widget.see("end")
        widget.config(state="disabled")

    def _set_icon(self) -> None:
        """Ставит иконку окна (best-effort: если ресурса нет — пропускаем)."""
        try:
            self._icon_img = tk.PhotoImage(file=_asset("bhydra.png"))
            self.iconphoto(True, self._icon_img)
        except tk.TclError:
            pass

    def _toggle_theme(self) -> None:
        self._apply_theme(self._dark.get())

    def _apply_theme(self, dark: bool) -> None:
        style = ttk.Style(self)
        if dark:
            bg, fg, field = "#1e1e1e", "#e6edf3", "#2a2a2a"
        else:
            bg, fg, field = "#f0f0f0", "#000000", "#ffffff"
        self.configure(bg=bg)
        for elem in (".", "TFrame", "TLabel", "TNotebook",
                     "TLabelframe", "TLabelframe.Label"):
            style.configure(elem, background=bg, foreground=fg)
        style.configure("TButton", background=field, foreground=fg)
        style.configure("TNotebook.Tab", background=field, foreground=fg)
        style.configure("TEntry", fieldbackground=field, foreground=fg)
        style.configure("Treeview", background=field, foreground=fg,
                        fieldbackground=field)
        style.configure("Treeview.Heading", background=bg, foreground=fg)
        for widget in self._text_widgets:
            widget.configure(bg=field, fg=fg, insertbackground=fg)

    def _about(self) -> None:
        messagebox.showinfo(
            "О программе",
            f"B-hydra Core v{__version__}\n\n"
            "Эталонный клиент сети B-hydra (полный узел).\n"
            "Одноранговая электронная денежная система (P2P).\n"
            "Кошелёк · майнинг · сеть в одном приложении.\n\n"
            "Криптография: SHA-2 (SHA-256/512) собственной реализации,\n"
            "подписи ECDSA secp256k1, модель UTXO, Proof-of-Work.")

    def _auto_refresh(self) -> None:
        """Периодически обновляет баланс и статус (раз в 3 сек), кроме майнинга."""
        if not self._mining:
            self._refresh_status()
        self.after(3000, self._auto_refresh)

    def _export_chain(self) -> None:
        path = filedialog.asksaveasfilename(
            defaultextension=".json", initialfile="bhydra_chain_export.json",
            filetypes=[("Цепочка B-hydra", "*.json"), ("Все файлы", "*.*")])
        if not path:
            return
        try:
            self.node.save(path)
            messagebox.showinfo("Экспорт",
                                f"Цепочка ({self.node.height} блоков) сохранена в:\n{path}")
        except OSError as exc:
            messagebox.showerror("Ошибка", str(exc))

    def _refresh_status(self) -> None:
        if self.wallet is not None:
            self.addr_var.set(self.wallet.address)
            self.priv_var.set(self.wallet.private_key_hex)
            self.bal_var.set(f"{self.node.get_balance(self.wallet.address):.4f} BHY")
        bal = (self.node.get_balance(self.wallet.address)
               if self.wallet else 0.0)
        peers = len(self.p2p.peers) if self.p2p else 0
        running = bool(self.p2p and self.p2p._running)
        self.mine_info.set(
            f"Высота: {self.node.height} | эмиссия: "
            f"{self.node.blockchain.total_supply:.0f} BHY")
        self.status.set(
            f"SHA: {hashing.backend()} | блоков: {self.node.height} | "
            f"баланс: {bal:.2f} BHY | узел: {'вкл' if running else 'выкл'} | "
            f"пиров: {peers}")
        self._refresh_history()
        self._refresh_mempool()

    @staticmethod
    def _fmt_time(ts) -> str:
        """Epoch-секунды → 'дд.мм.гггг чч:мм' (или '—' для генезиса/пустого)."""
        if not ts:
            return "—"
        from datetime import datetime
        try:
            return datetime.fromtimestamp(ts).strftime("%d.%m.%Y %H:%M")
        except (OverflowError, OSError, ValueError):
            return "—"

    def _refresh_history(self) -> None:
        """Обновить таблицу истории операций кошелька (по умолчанию)."""
        tree = getattr(self, "history_tree", None)
        if tree is None:
            return
        tree.delete(*tree.get_children())
        self._history_meta = {}
        if self.wallet is None:
            return
        icon = {"Пополнение": "🟢 Пополнение",
                "Отправка": "🔴 Отправка",
                "Себе": "🔁 Себе",
                "Майнинг": "⛏ Майнинг"}
        history = self.node.address_history(self.wallet.address)
        for h in reversed(history):            # свежие операции сверху
            party = h["counterparty"]
            shown = party if len(party) <= 40 else party[:30] + "…" + party[-6:]
            iid = tree.insert("", "end", values=(
                self._fmt_time(h.get("block_time")),
                icon.get(h["direction"], h["direction"]),
                f"{h['amount']:.4f} BHY",
                shown,
                f"#{h['block_index']}",
            ))
            self._history_meta[iid] = (h["txid"], party)   # полные данные для копии

    def _history_popup(self, event) -> None:
        row = self.history_tree.identify_row(event.y)
        if row:
            self.history_tree.selection_set(row)
            self._history_menu.tk_popup(event.x_root, event.y_root)

    def _copy_history(self, what: str) -> None:
        """Скопировать txid или адрес контрагента выделенной операции."""
        sel = self.history_tree.selection()
        if not sel:
            return
        txid, party = self._history_meta.get(sel[0], ("", ""))
        value = txid if what == "txid" else party
        if not value or value == "—":
            return self.status.set("Нечего копировать для этой операции.")
        self.clipboard_clear()
        self.clipboard_append(value)
        self.update()
        label = "txid" if what == "txid" else "адрес контрагента"
        self.status.set(f"Скопирован {label}: {value[:24]}…")

    def _open_history_in_explorer(self, _event=None) -> None:
        """Двойной клик по операции → вкладка «Блоки» с подсветкой транзакции."""
        sel = self.history_tree.selection()
        if not sel:
            return
        txid, _party = self._history_meta.get(sel[0], ("", ""))
        if txid:
            self._open_in_explorer(txid)

    def _open_in_explorer(self, txid: str) -> None:
        """Переключиться на «Блоки», выбрать блок транзакции и подсветить её."""
        found = self.node.find_transaction(txid)
        if not found:
            return messagebox.showinfo(
                "Обозреватель", "Транзакция ещё не в цепочке (возможно, в мемпуле "
                "и ждёт майнинга).")
        self._nb.select(self._blocks_tab)
        self._refresh_blocks()
        idx = str(found["block_index"])
        if self.blocks_tree.exists(idx):
            self.blocks_tree.selection_set(idx)
            self.blocks_tree.see(idx)
            self._show_block_details()
        self._append_tx_highlight(found, txid)
        self.search_var.set(txid)

    def _append_tx_highlight(self, found: dict, txid: str) -> None:
        """Дописать в детали блока выделенную информацию о транзакции."""
        tx = found["transaction"]
        is_cb = tx.get("vin") and tx["vin"][0].get("txid", "").count("0") == len(
            tx["vin"][0].get("txid", ""))
        lines = [f"\n▶ Транзакция {txid[:32]}…  (блок #{found['block_index']})"]
        lines.append("  тип   : " + ("награда за блок (coinbase)" if is_cb
                                     else "перевод"))
        for o in tx.get("vout", []):
            lines.append(f"  выход : {o['amount']} BHY → {o['address']}")
        text = "\n".join(lines)
        self.block_details.config(state="normal")
        start = self.block_details.index("end-1c")
        self.block_details.insert("end", "\n" + text)
        self.block_details.tag_add("hl", start, "end-1c")
        self.block_details.see("end")
        self.block_details.config(state="disabled")


def main() -> None:
    app = BHydraApp()
    app.mainloop()


if __name__ == "__main__":
    main()
