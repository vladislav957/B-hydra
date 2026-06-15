"""
Node.py — узел сети B-hydra.

Узел хранит копию блокчейна и мемпул, принимает транзакции, майнит блоки
(с coinbase-наградой) и считает балансы адресов. Это связующее звено между
кошельками, транзакциями и блокчейном.

Сетевую (сокетную) часть см. в P2P.py — здесь только логика узла, чтобы
модуль импортировался без побочных эффектов.
"""

import json

from Blockchain import Blockchain, DEFAULT_DIFFICULTY
from Transactinons import Transaction, TransactionPool, coinbase


class BHydraNode:
    """Логический узел B-hydra (блокчейн + мемпул)."""

    def __init__(self, difficulty=DEFAULT_DIFFICULTY):
        self.blockchain = Blockchain(difficulty=difficulty)
        self.mempool = TransactionPool()

    # --- Транзакции ------------------------------------------------------
    def add_transaction(self, transaction: Transaction) -> bool:
        """Добавляет транзакцию в мемпул после проверки баланса и подписи."""
        if not transaction.is_valid():
            return False
        if not transaction.is_coinbase:
            available = self.get_balance(transaction.sender)
            if available < transaction.amount + transaction.fee:
                return False  # недостаточно средств
        return self.mempool.add(transaction)

    # --- Майнинг ---------------------------------------------------------
    def mine_pending(self, miner_address: str):
        """Собирает транзакции из мемпула в блок и майнит его."""
        pending = self.mempool.take_all()
        fees = sum(tx.fee for tx in pending)
        reward = self.blockchain.block_reward(len(self.blockchain.chain))
        reward_tx = coinbase(miner_address, reward, fees)

        data = [reward_tx.to_dict()] + [tx.to_dict() for tx in pending]
        return self.blockchain.add_block(data=data)

    # --- Балансы ---------------------------------------------------------
    def get_balance(self, address: str) -> float:
        """Считает баланс адреса по всей цепочке."""
        balance = 0.0
        for block in self.blockchain.chain:
            for tx in self._block_transactions(block):
                if tx.get("recipient") == address:
                    balance += tx.get("amount", 0)
                if tx.get("sender") == address:
                    balance -= tx.get("amount", 0) + tx.get("fee", 0)
        return balance

    @staticmethod
    def _block_transactions(block):
        """Возвращает список dict-транзакций блока (или пустой список)."""
        data = block.data
        if isinstance(data, (list, tuple)):
            return [tx for tx in data if isinstance(tx, dict)]
        return []

    def is_valid(self) -> bool:
        return self.blockchain.is_chain_valid()

    # --- Сохранение / загрузка ------------------------------------------
    def save(self, path: str) -> None:
        """Сохраняет цепочку и мемпул в JSON-файл."""
        with open(path, "w", encoding="utf-8") as f:
            json.dump(
                {"difficulty": self.blockchain.difficulty,
                 "chain": self.blockchain.to_dicts(),
                 "mempool": [tx.to_dict() for tx in self.mempool.transactions]},
                f, ensure_ascii=False, indent=2,
            )

    @classmethod
    def load(cls, path: str) -> "BHydraNode":
        """Загружает узел из JSON-файла с цепочкой и мемпулом."""
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        node = cls.__new__(cls)
        node.blockchain = Blockchain.from_dicts(data["chain"], data["difficulty"])
        node.mempool = TransactionPool()
        for tx_dict in data.get("mempool", []):
            node.mempool.transactions.append(Transaction.from_dict(tx_dict))
        return node


if __name__ == "__main__":
    from wallet import generate_wallet

    node = BHydraNode(difficulty=3)
    alice = generate_wallet()
    bob = generate_wallet()

    # Алиса добывает первый блок и получает награду.
    node.mine_pending(alice.address)
    print(f"Баланс Алисы после майнинга: {node.get_balance(alice.address)} BHY")

    # Алиса переводит 10 BHY Бобу.
    tx = Transaction(alice.address, bob.address, 10, fee=0.5)
    tx.sign(alice)
    print("Транзакция принята:", node.add_transaction(tx))

    # Боб майнит блок с этой транзакцией.
    node.mine_pending(bob.address)
    print(f"Баланс Алисы: {node.get_balance(alice.address)} BHY")
    print(f"Баланс Боба : {node.get_balance(bob.address)} BHY")
    print("Цепочка валидна:", node.is_valid())
