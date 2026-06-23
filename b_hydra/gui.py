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

import threading
import tkinter as tk
from tkinter import messagebox, ttk

from . import hashing
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

        # --- Состояние ---
        import os
        self.node = (BHydraNode.load(STATE_FILE)
                     if os.path.exists(STATE_FILE) else BHydraNode(difficulty=3))
        self.wallet: Wallet | None = None
        self.p2p: P2PNode | None = None
        self._mining = False
        if os.path.exists(WALLET_FILE):
            try:
                self.wallet = Wallet.from_private_hex(
                    open(WALLET_FILE).read().strip())
            except (ValueError, OSError):
                self.wallet = None

        self._build_ui()
        self._refresh_status()

    # --- Интерфейс -------------------------------------------------------
    def _build_ui(self) -> None:
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

    def _build_wallet_tab(self, nb: ttk.Notebook) -> None:
        tab = ttk.Frame(nb, padding=12)
        nb.add(tab, text="💼 Кошелёк")

        btns = ttk.Frame(tab)
        btns.pack(fill="x")
        ttk.Button(btns, text="Создать кошелёк",
                   command=self._new_wallet).pack(side="left")
        ttk.Button(btns, text="Обновить баланс",
                   command=self._refresh_status).pack(side="left", padx=6)

        self.addr_var = tk.StringVar()
        self.priv_var = tk.StringVar()
        self.bal_var = tk.StringVar()
        for label, var in (("Адрес:", self.addr_var),
                           ("Приватный ключ:", self.priv_var),
                           ("Баланс:", self.bal_var)):
            row = ttk.Frame(tab)
            row.pack(fill="x", pady=4)
            ttk.Label(row, text=label, width=16).pack(side="left")
            ttk.Entry(row, textvariable=var, state="readonly").pack(
                side="left", fill="x", expand=True)

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

        self.mine_log = tk.Text(tab, height=16, state="disabled")
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

    # --- Логика: кошелёк -------------------------------------------------
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
        threading.Thread(target=self._mine_worker, args=(count,),
                         daemon=True).start()

    def _mine_worker(self, count: int) -> None:
        for _ in range(count):
            if self.p2p and self.p2p._running:
                block = self.p2p.mine(self.wallet.address)   # майнит + рассылает
            else:
                block = self.node.mine_pending(self.wallet.address)
            self.after(0, self._log, self.mine_log,
                       f"⛏ блок #{block.index} | перебрано "
                       f"{block.mining_attempts} хешей | nonce {block.nonce}")
        self.node.save(STATE_FILE)
        self.after(0, self._mine_done)

    def _mine_done(self) -> None:
        self._mining = False
        self.mine_btn.config(state="normal")
        self._refresh_status()

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
