"""
manig.py — главный модуль B-hydra (точка входа).

Связывает воедино кошельки, транзакции, ноду и блокчейн и показывает полный
жизненный цикл: создание кошельков → майнинг награды → подписанный перевод →
включение транзакции в блок → проверка балансов и целостности цепочки.

Запуск:
    python manig.py
"""

from Node import BHydraNode
from wallet import generate_wallet


def short(address: str) -> str:
    """Сокращает адрес B-hydra для читаемого вывода."""
    return address if len(address) <= 18 else f"{address[:10]}…{address[-6:]}"


def run_demo(difficulty: int = 3):
    print("=" * 64)
    print(" B-hydra — одноранговая электронная денежная система (P2P) ")
    print("=" * 64)

    node = BHydraNode(difficulty=difficulty)
    sender = generate_wallet()
    recipient = generate_wallet()

    print("\nАдреса участников (в блоках хранятся только адреса, без имён):")
    print(f"  {sender.address}")
    print(f"  {recipient.address}")

    # 1. Первый участник майнит блок и получает награду на свой адрес.
    print(f"\n[1] {short(sender.address)} майнит блок (получает награду)…")
    node.mine_pending(sender.address)
    print(f"    Баланс {short(sender.address)}: {node.get_balance(sender.address)} BHY")

    # 2. Перевод на другой адрес (UTXO: вход + выходы со сдачей).
    print(f"\n[2] {short(sender.address)} → {short(recipient.address)} : 10 BHY…")
    tx = node.create_transaction(sender, recipient.address, amount=10, fee=0.5)
    accepted = node.add_transaction(tx)
    print(f"    Транзакция принята в мемпул: {accepted}")
    print(f"    txid: {tx.txid[:32]}…")
    print(f"    входов: {len(tx.vin)} | выходов: {len(tx.vout)} (получатель + сдача)")

    # 3. Другой участник майнит блок с этой транзакцией (награда + комиссия).
    print(f"\n[3] {short(recipient.address)} майнит блок с транзакцией…")
    node.mine_pending(recipient.address)

    # 4. Итоги — по адресам.
    print("\n[4] Итоговые балансы по адресам:")
    print(f"    {short(sender.address)}: {node.get_balance(sender.address):.2f} BHY")
    print(f"    {short(recipient.address)}: {node.get_balance(recipient.address):.2f} BHY")

    print("\nЦепочка (кто намайнил блок):")
    for block in node.blockchain.chain:
        n_tx = len(block.data) if isinstance(block.data, list) else 1
        miner = node.blockchain._miner_of(block)
        miner_str = short(miner) if miner else "— генезис —"
        print(f"    Блок #{block.index} | txs={n_tx} | майнер: {miner_str}")

    print(f"\nВысота цепочки : {len(node.blockchain.chain)}")
    print(f"Цепочка валидна: {node.is_valid()}")
    print("=" * 64)


if __name__ == "__main__":
    run_demo()
