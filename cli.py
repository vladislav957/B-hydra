"""
cli.py — командная строка B-hydra.

Управление блокчейном из терминала: создание кошельков, инициализация цепочки,
перевод средств, майнинг блоков, проверка балансов и просмотр цепочки.
Состояние хранится в JSON-файле (по умолчанию bhydra_chain.json).

Примеры:
    python cli.py wallet
    python cli.py init
    python cli.py mine BHY<адрес-майнера>
    python cli.py send <приватный-ключ> <адрес-получателя> 10 --fee 0.5
    python cli.py mine BHY<адрес-майнера>
    python cli.py balance BHY<адрес>
    python cli.py chain
"""

import argparse
import os

from Node import BHydraNode
from wallet import Wallet, generate_wallet

DEFAULT_FILE = "bhydra_chain.json"
DEFAULT_DIFFICULTY = 3


def _load_or_init(path):
    if os.path.exists(path):
        return BHydraNode.load(path)
    return BHydraNode(difficulty=DEFAULT_DIFFICULTY)


def cmd_wallet(args):
    wallet = generate_wallet()
    print("Новый кошелёк B-hydra")
    print(f"  Адрес        : {wallet.address}")
    print(f"  Приватный ключ: {wallet.private_key_hex}")
    print("  ⚠ Сохраните приватный ключ — он нужен для отправки средств.")


def cmd_init(args):
    if os.path.exists(args.file) and not args.force:
        print(f"Файл {args.file} уже существует. Используйте --force для пересоздания.")
        return
    node = BHydraNode(difficulty=args.difficulty)
    node.save(args.file)
    print(f"Цепочка инициализирована (сложность {args.difficulty}) → {args.file}")


def cmd_mine(args):
    node = _load_or_init(args.file)
    block = node.mine_pending(args.address)
    node.save(args.file)
    n_tx = len(block.data) if isinstance(block.data, list) else 1
    print(f"Блок #{block.index} добыт (транзакций: {n_tx})")
    print(f"  hash : {block.hash[:32]}…")
    print(f"  Баланс {args.address[:16]}…: {node.get_balance(args.address):.4f} BHY")


def cmd_send(args):
    node = _load_or_init(args.file)
    sender = Wallet.from_private_hex(args.private_key)
    tx = node.create_transaction(sender, args.to, amount=args.amount, fee=args.fee)
    if tx is None:
        print("Недостаточно средств (UTXO) для отправки.")
        return
    if node.add_transaction(tx):
        node.save(args.file)
        print("Транзакция добавлена в мемпул (ожидает майнинга).")
        print(f"  txid: {tx.txid[:32]}…")
        print(f"  входов: {len(tx.vin)} | выходов: {len(tx.vout)} (включая сдачу)")
        print(f"  {sender.address[:16]}… → {args.to[:16]}…  {args.amount} BHY (fee {args.fee})")
    else:
        print("Транзакция отклонена (неверная подпись или двойная трата).")


def cmd_balance(args):
    node = _load_or_init(args.file)
    print(f"Баланс {args.address}: {node.get_balance(args.address):.4f} BHY")


def cmd_chain(args):
    node = _load_or_init(args.file)
    for block in node.blockchain.chain:
        n_tx = len(block.data) if isinstance(block.data, list) else 1
        print(f"Блок #{block.index} | txs={n_tx} | hash={block.hash[:32]}…")
    print(f"\nВысота: {len(node.blockchain.chain)} | "
          f"в мемпуле: {len(node.mempool)} | "
          f"валидна: {node.is_valid()}")


def build_parser():
    parser = argparse.ArgumentParser(
        prog="b-hydra", description="B-hydra — P2P электронная денежная система"
    )
    parser.add_argument("--file", default=DEFAULT_FILE, help="файл состояния цепочки")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("wallet", help="создать новый кошелёк").set_defaults(func=cmd_wallet)

    p_init = sub.add_parser("init", help="инициализировать цепочку")
    p_init.add_argument("--difficulty", type=int, default=DEFAULT_DIFFICULTY)
    p_init.add_argument("--force", action="store_true")
    p_init.set_defaults(func=cmd_init)

    p_mine = sub.add_parser("mine", help="добыть блок (награда майнеру)")
    p_mine.add_argument("address", help="адрес майнера")
    p_mine.set_defaults(func=cmd_mine)

    p_send = sub.add_parser("send", help="отправить средства")
    p_send.add_argument("private_key", help="приватный ключ отправителя (hex)")
    p_send.add_argument("to", help="адрес получателя")
    p_send.add_argument("amount", type=float, help="сумма BHY")
    p_send.add_argument("--fee", type=float, default=0.0, help="комиссия")
    p_send.set_defaults(func=cmd_send)

    p_bal = sub.add_parser("balance", help="баланс адреса")
    p_bal.add_argument("address")
    p_bal.set_defaults(func=cmd_balance)

    sub.add_parser("chain", help="показать цепочку").set_defaults(func=cmd_chain)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
