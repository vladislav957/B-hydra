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


def run_demo(difficulty: int = 3):
    print("=" * 60)
    print(" B-hydra — одноранговая электронная денежная система (P2P) ")
    print("=" * 60)

    node = BHydraNode(difficulty=difficulty)
    alice = generate_wallet()
    bob = generate_wallet()

    print(f"\nКошелёк Alice: {alice.address}")
    print(f"Кошелёк Bob  : {bob.address}")

    # 1. Alice майнит первый блок и получает награду.
    print("\n[1] Alice майнит блок (получает награду)…")
    node.mine_pending(alice.address)
    print(f"    Баланс Alice: {node.get_balance(alice.address)} BHY")

    # 2. Alice отправляет Bob 10 BHY (UTXO-транзакция: вход + выходы со сдачей).
    print("\n[2] Alice отправляет Bob 10 BHY…")
    tx = node.create_transaction(alice, bob.address, amount=10, fee=0.5)
    accepted = node.add_transaction(tx)
    print(f"    Транзакция принята в мемпул: {accepted}")
    print(f"    txid: {tx.txid[:32]}…")
    print(f"    входов: {len(tx.vin)} | выходов: {len(tx.vout)} (получатель + сдача)")

    # 3. Bob майнит блок с этой транзакцией (награда + комиссия).
    print("\n[3] Bob майнит блок с транзакцией…")
    node.mine_pending(bob.address)

    # 4. Итоги.
    print("\n[4] Итоговые балансы:")
    print(f"    Alice: {node.get_balance(alice.address):.2f} BHY")
    print(f"    Bob  : {node.get_balance(bob.address):.2f} BHY")

    print("\nЦепочка:")
    for block in node.blockchain.chain:
        n_tx = len(block.data) if isinstance(block.data, list) else 1
        print(f"    Блок #{block.index} | txs={n_tx} | hash={block.hash[:24]}…")

    print(f"\nВысота цепочки : {len(node.blockchain.chain)}")
    print(f"Цепочка валидна: {node.is_valid()}")
    print("=" * 60)


if __name__ == "__main__":
    run_demo()
