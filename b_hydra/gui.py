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
from .node import BHydraNode
from .p2p import P2PNode
from .wallet import Wallet, generate_wallet, is_valid_address

STATE_FILE = "bhydra_chain.json"
WALLET_FILE = "wallet.key"


class BHydraApp(tk.Tk):
    """Главное окно клиента B-hydra."""

    def __init__(self) -> None:
        super().__init__()
        self.title("B-hydra — кошелёк · майнинг · сеть")
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
        self._mining = False
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
        self._build_wallet_tab(nb)
        self._build_mining_tab(nb)
        self._build_network_tab(nb)
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
        for label, var, copyable in (("Адрес:", self.addr_var, True),
                                     ("Приватный ключ:", self.priv_var, True),
                                     ("Баланс:", self.bal_var, False)):
            row = ttk.Frame(tab)
            row.pack(fill="x", pady=4)
            ttk.Label(row, text=label, width=16).pack(side="left")
            ttk.Entry(row, textvariable=var, state="readonly").pack(
                side="left", fill="x", expand=True)
            if copyable:
                ttk.Button(row, text="Копировать", width=12,
                           command=lambda v=var, n=label: self._copy(v, n)).pack(
                    side="left", padx=(6, 0))

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
        self.to_var = tk.StringVar()
        self.amount_var = tk.StringVar(value="10")
        ttk.Label(send, text="Кому (адрес):").grid(row=0, column=0, sticky="w")
        ttk.Entry(send, textvariable=self.to_var, width=44).grid(
            row=0, column=1, columnspan=3, sticky="we", padx=4)
        ttk.Label(send, text="Сумма:").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Entry(send, textvariable=self.amount_var, width=12).grid(
            row=1, column=1, sticky="w", pady=4)
        ttk.Button(send, text="Отправить", command=self._send).grid(
            row=1, column=2, padx=6)

    def _build_mining_tab(self, nb: ttk.Notebook) -> None:
        tab = ttk.Frame(nb, padding=12)
        nb.add(tab, text="⛏ Майнинг")

        top = ttk.Frame(tab)
        top.pack(fill="x")
        ttk.Label(top, text="Сколько блоков:").pack(side="left")
        self.mine_count = tk.StringVar(value="5")
        ttk.Entry(top, textvariable=self.mine_count, width=8).pack(
            side="left", padx=6)
        self.mine_btn = ttk.Button(top, text="Майнить",
                                   command=self._mine_clicked)
        self.mine_btn.pack(side="left")

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

        prow = ttk.Frame(tab)
        prow.pack(fill="x", pady=8)
        ttk.Label(prow, text="Пир (host:port):").pack(side="left")
        self.peer_var = tk.StringVar()
        ttk.Entry(prow, textvariable=self.peer_var, width=20).pack(side="left", padx=4)
        ttk.Button(prow, text="Подключиться", command=self._connect).pack(side="left")
        ttk.Button(prow, text="Синхронизировать", command=self._sync).pack(
            side="left", padx=6)

        self.net_log = tk.Text(tab, height=16, state="disabled")
        self.net_log.pack(fill="both", expand=True)

    def _build_blocks_tab(self, nb: ttk.Notebook) -> None:
        tab = ttk.Frame(nb, padding=12)
        nb.add(tab, text="🔍 Блоки")

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

        self.block_details = tk.Text(tab, height=8, state="disabled")
        self.block_details.pack(fill="x")

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

    def _new_wallet(self) -> None:
        self.wallet = generate_wallet()
        try:
            with open(WALLET_FILE, "w") as fh:
                fh.write(self.wallet.private_key_hex)
        except OSError:
            pass
        self._refresh_status()

    def _import_wallet(self) -> None:
        try:
            self.wallet = Wallet.from_private_hex(self.import_var.get().strip())
            with open(WALLET_FILE, "w") as fh:
                fh.write(self.wallet.private_key_hex)
            self._refresh_status()
        except (ValueError, OSError):
            messagebox.showerror("Ошибка", "Неверный приватный ключ.")

    def _send(self) -> None:
        if self.wallet is None:
            return messagebox.showwarning("Кошелёк", "Сначала создайте кошелёк.")
        to = self.to_var.get().strip()
        if not is_valid_address(to):
            return messagebox.showerror("Ошибка", "Неверный адрес получателя.")
        try:
            amount = float(self.amount_var.get())
        except ValueError:
            return messagebox.showerror("Ошибка", "Неверная сумма.")
        tx = self.node.create_transaction(self.wallet, to, amount, fee=0.0)
        if tx is None or not self.node.add_transaction(tx):
            return messagebox.showwarning("Перевод", "Недостаточно средств.")
        if self.p2p and self.p2p._running:
            self.p2p.broadcast({"type": "transaction", "transaction": tx.to_dict(),
                                "from": [self.p2p.host, self.p2p.port]})
        self.node.save(STATE_FILE)
        messagebox.showinfo("Перевод", f"Транзакция в мемпуле:\n{tx.txid[:24]}…\n"
                                       "Будет подтверждена при майнинге.")
        self._refresh_status()

    # --- Логика: майнинг -------------------------------------------------
    def _mine_clicked(self) -> None:
        if self.wallet is None:
            return messagebox.showwarning("Кошелёк", "Сначала создайте кошелёк.")
        if self._mining:
            return
        try:
            count = int(self.mine_count.get())
        except ValueError:
            return messagebox.showerror("Ошибка", "Неверное число блоков.")
        self._mining = True
        self.mine_btn.config(state="disabled")
        self.progress.config(maximum=count, value=0)
        self.mine_status.set(f"Майнинг… 0/{count}")
        threading.Thread(target=self._mine_worker, args=(count,),
                         daemon=True).start()

    def _mine_worker(self, count: int) -> None:
        # Только майнинг и запись в очередь — НИКАКИХ обращений к tkinter.
        for i in range(count):
            if self.p2p and self.p2p._running:
                block = self.p2p.mine(self.wallet.address)   # майнит + рассылает
            else:
                block = self.node.mine_pending(self.wallet.address)
            self._queue.put(("block", i + 1, count, block.index,
                             block.mining_attempts, block.nonce))
        self.node.save(STATE_FILE)
        self._queue.put(("done", count))

    def _poll_queue(self) -> None:
        """Главный поток: забирает события воркера и обновляет интерфейс."""
        try:
            while True:
                msg = self._queue.get_nowait()
                if msg[0] == "block":
                    _, done, total, idx, attempts, nonce = msg
                    self.progress.config(value=done)
                    self.mine_status.set(f"Майнинг… {done}/{total}")
                    self._log(self.mine_log, f"⛏ блок #{idx} | перебрано "
                                             f"{attempts} хешей | nonce {nonce}")
                elif msg[0] == "done":
                    self._mining = False
                    self.mine_btn.config(state="normal")
                    self.mine_status.set(f"Готово ✓ намайнено {msg[1]} блоков")
                    self._refresh_status()
                    self._refresh_blocks()
        except queue.Empty:
            pass
        self.after(150, self._poll_queue)

    # --- Логика: сеть ----------------------------------------------------
    def _toggle_node(self) -> None:
        if self.p2p and self.p2p._running:
            self.p2p.stop()
            self.node_btn.config(text="Запустить узел")
            self._log(self.net_log, "Узел остановлен.")
        else:
            host, port = self.host_var.get(), int(self.port_var.get())
            self.p2p = P2PNode(host, port, node=self.node)
            self.p2p.start()
            self.node_btn.config(text="Остановить узел")
            self._log(self.net_log, f"Узел запущен на {host}:{port}")
        self._refresh_status()

    def _connect(self) -> None:
        if not (self.p2p and self.p2p._running):
            return messagebox.showwarning("Сеть", "Сначала запустите узел.")
        try:
            host, port = self.peer_var.get().strip().split(":")
            self.p2p.connect(host, int(port))
            self._refresh_blocks()
            self._log(self.net_log, f"Подключено к {host}:{port} | "
                                    f"пиров: {len(self.p2p.peers)} | "
                                    f"высота: {self.node.height}")
            self._refresh_status()
        except (ValueError, OSError) as exc:
            messagebox.showerror("Сеть", f"Не удалось подключиться: {exc}")

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
            f"B-hydra v{__version__}\n\n"
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


def main() -> None:
    app = BHydraApp()
    app.mainloop()


if __name__ == "__main__":
    main()
