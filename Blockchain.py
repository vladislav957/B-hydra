"""
B-hydra — одноранговая электронная денежная система (P2P electronic cash).

Эталонная реализация блокчейна согласно белой книге B-hydra:

  * Хеширование               : SHA-512 (двойной SHA-512 в дереве Меркла);
  * Консенсус                 : Proof-of-Work (ведущие нули в хеше блока);
  * Время блока               : ~2 минуты;
  * Начальная награда         : 50 BHY;
  * Интервал халвинга         : 310 000 блоков;
  * Максимальная эмиссия      : 31 000 000 BHY
                                (310 000 * 50 * 2 = 31 000 000).

Пример использования (как в README):

    from Blockchain import Blockchain

    blockchain = Blockchain()
    blockchain.add_block(data="Пример транзакции")
    print(blockchain.chain)
"""

import hashlib
import json
import time

# --- Параметры сети B-hydra (белая книга) -------------------------------------
INITIAL_REWARD = 50            # Начальная награда за блок, BHY
HALVING_INTERVAL = 310_000     # Каждые 310 000 блоков награда делится пополам
MAX_SUPPLY = 31_000_000        # Максимальная эмиссия монет
BLOCK_TIME_SECONDS = 120       # Целевое время генерации блока (2 минуты)
DEFAULT_DIFFICULTY = 4         # Кол-во ведущих нулей в хеше блока (PoW)


def sha512d(data: bytes) -> bytes:
    """Двойной SHA-512 — основной хеш B-hydra."""
    return hashlib.sha512(hashlib.sha512(data).digest()).digest()


def merkle_root(hashes):
    """Вычисляет корень дерева Меркла из списка хешей (bytes)."""
    if not hashes:
        # Корень пустого набора транзакций — хеш пустой строки.
        return sha512d(b"").hex()

    layer = list(hashes)
    while len(layer) > 1:
        # Если число элементов нечётное — дублируем последний.
        if len(layer) % 2 == 1:
            layer.append(layer[-1])
        layer = [
            sha512d(layer[i] + layer[i + 1])
            for i in range(0, len(layer), 2)
        ]
    return layer[0].hex() if isinstance(layer[0], bytes) else layer[0]


class Block:
    """Блок цепочки B-hydra."""

    def __init__(self, index, previous_hash, data, timestamp=None, nonce=0):
        self.index = index
        self.previous_hash = previous_hash
        self.data = data
        self.timestamp = timestamp if timestamp is not None else time.time()
        self.nonce = nonce
        self.merkle_root = self._calculate_merkle_root()
        self.hash = self.calculate_hash()

    def _calculate_merkle_root(self):
        """Строит корень Меркла по транзакциям блока."""
        transactions = self.data if isinstance(self.data, (list, tuple)) else [self.data]
        leaves = [sha512d(str(tx).encode("utf-8")) for tx in transactions]
        return merkle_root(leaves)

    def calculate_hash(self):
        """SHA-512 от содержимого заголовка блока."""
        header = (
            f"{self.index}{self.previous_hash}{self.merkle_root}"
            f"{self.timestamp}{self.nonce}"
        )
        return hashlib.sha512(header.encode("utf-8")).hexdigest()

    def mine_block(self, difficulty):
        """Proof-of-Work: подбираем nonce, пока хеш не начнётся с N нулей."""
        target = "0" * difficulty
        while not self.hash.startswith(target):
            self.nonce += 1
            self.hash = self.calculate_hash()
        return self.hash

    def to_dict(self):
        return {
            "index": self.index,
            "previous_hash": self.previous_hash,
            "data": self.data,
            "timestamp": self.timestamp,
            "nonce": self.nonce,
            "merkle_root": self.merkle_root,
            "hash": self.hash,
        }

    @classmethod
    def from_dict(cls, data):
        """Восстанавливает блок из словаря без повторного майнинга."""
        block = cls.__new__(cls)
        block.index = data["index"]
        block.previous_hash = data["previous_hash"]
        block.data = data["data"]
        block.timestamp = data["timestamp"]
        block.nonce = data["nonce"]
        block.merkle_root = data["merkle_root"]
        block.hash = data["hash"]
        return block

    def __repr__(self):
        return f"<Block #{self.index} hash={self.hash[:16]}…>"


class Blockchain:
    """Цепочка блоков B-hydra с PoW-консенсусом и халвингом награды."""

    def __init__(self, difficulty=DEFAULT_DIFFICULTY):
        self.difficulty = difficulty
        self.chain = [self.create_genesis_block()]

    def create_genesis_block(self):
        """Генезис-блок сети B-hydra."""
        genesis = Block(0, "0" * 128, "B-hydra Genesis Block")
        genesis.mine_block(self.difficulty)
        return genesis

    @property
    def last_block(self):
        return self.chain[-1]

    def block_reward(self, height):
        """Награда за блок с учётом халвинга (Bitcoin-подобная схема)."""
        halvings = height // HALVING_INTERVAL
        if halvings >= 64:          # После 64 халвингов награда обнуляется.
            return 0
        return INITIAL_REWARD / (2 ** halvings)

    @property
    def total_supply(self):
        """Текущая эмиссия = сумма наград за добытые блоки."""
        supply = sum(self.block_reward(b.index) for b in self.chain[1:])
        return min(supply, MAX_SUPPLY)

    def add_block(self, data):
        """Создаёт, майнит и добавляет новый блок в цепочку."""
        new_block = Block(
            index=len(self.chain),
            previous_hash=self.last_block.hash,
            data=data,
        )
        new_block.mine_block(self.difficulty)
        self.chain.append(new_block)
        return new_block

    def is_chain_valid(self):
        """Проверяет целостность цепочки и корректность PoW."""
        target = "0" * self.difficulty
        for i in range(1, len(self.chain)):
            current = self.chain[i]
            previous = self.chain[i - 1]

            if current.hash != current.calculate_hash():
                return False
            if current.previous_hash != previous.hash:
                return False
            if not current.hash.startswith(target):
                return False
        return True

    def to_dicts(self):
        return [b.to_dict() for b in self.chain]

    def to_json(self):
        return json.dumps(self.to_dicts(), ensure_ascii=False, indent=2)

    @classmethod
    def from_dicts(cls, chain_dicts, difficulty=DEFAULT_DIFFICULTY):
        """Восстанавливает блокчейн из списка словарей (например, из файла)."""
        blockchain = cls.__new__(cls)
        blockchain.difficulty = difficulty
        blockchain.chain = [Block.from_dict(d) for d in chain_dicts]
        return blockchain


if __name__ == "__main__":
    # Демонстрация работы сети B-hydra.
    blockchain = Blockchain(difficulty=3)

    print("Майнинг блока #1 …")
    blockchain.add_block(data="Alice -> Bob: 10 BHY")

    print("Майнинг блока #2 …")
    blockchain.add_block(data=["Bob -> Carol: 5 BHY", "Carol -> Dave: 2 BHY"])

    for block in blockchain.chain:
        print(f"Блок #{block.index} | nonce={block.nonce} | hash={block.hash[:24]}…")

    print(f"\nДлина цепочки   : {len(blockchain.chain)}")
    print(f"Эмиссия (BHY)   : {blockchain.total_supply}")
    print(f"Награда блока #1 : {blockchain.block_reward(1)} BHY")
    print(f"Цепочка валидна : {blockchain.is_chain_valid()}")
