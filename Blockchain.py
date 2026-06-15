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
DEFAULT_DIFFICULTY = 4         # Базовое кол-во ведущих нулей в хеше блока (PoW)

# Динамическая сложность: чем больше участников (майнеров) в сети, тем выше
# сложность. Каждые PARTICIPANTS_PER_LEVEL разных майнеров добавляют +1 нуль.
PARTICIPANTS_PER_LEVEL = 2     # сколько новых майнеров повышают сложность на 1
MAX_DIFFICULTY = 8             # верхний предел сложности (чтобы не «застрять»)

# Фиксированная метка времени генезис-блока. Благодаря ей все узлы с одинаковой
# базовой сложностью получают ИДЕНТИЧНЫЙ генезис — иначе сеть не сможет
# синхронизироваться (у каждого узла была бы своя «нулевая точка»).
GENESIS_TIMESTAMP = 0.0


def difficulty_for_participants(participants, base=DEFAULT_DIFFICULTY,
                                per_level=PARTICIPANTS_PER_LEVEL,
                                cap=MAX_DIFFICULTY):
    """
    Требуемая сложность в зависимости от числа участников сети.

    Чем больше разных майнеров — тем труднее найти nonce:
        0–1 майнер  → base
        2–3 майнера → base + 1
        4–5         → base + 2 … и так до cap.
    """
    level = max(0, participants) // per_level
    return min(base + level, cap)


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

    def __init__(self, index, previous_hash, data, timestamp=None, nonce=0,
                 difficulty=DEFAULT_DIFFICULTY):
        self.index = index
        self.previous_hash = previous_hash
        self.data = data
        self.timestamp = timestamp if timestamp is not None else time.time()
        self.nonce = nonce
        self.difficulty = difficulty            # сложность, на которой добыт блок
        self.merkle_root = self._calculate_merkle_root()
        self.hash = self.calculate_hash()

    def _calculate_merkle_root(self):
        """Строит корень Меркла по транзакциям блока."""
        transactions = self.data if isinstance(self.data, (list, tuple)) else [self.data]
        leaves = [sha512d(str(tx).encode("utf-8")) for tx in transactions]
        return merkle_root(leaves)

    def calculate_hash(self):
        """SHA-512 от содержимого заголовка блока (включая сложность)."""
        header = (
            f"{self.index}{self.previous_hash}{self.merkle_root}"
            f"{self.timestamp}{self.difficulty}{self.nonce}"
        )
        return hashlib.sha512(header.encode("utf-8")).hexdigest()

    def mine_block(self):
        """Proof-of-Work: подбираем nonce, пока хеш не начнётся с N нулей."""
        target = "0" * self.difficulty
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
            "difficulty": self.difficulty,
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
        block.difficulty = data.get("difficulty", DEFAULT_DIFFICULTY)
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
        """Детерминированный генезис-блок сети B-hydra (одинаков для всех узлов)."""
        genesis = Block(0, "0" * 128, "B-hydra Genesis Block",
                        timestamp=GENESIS_TIMESTAMP, difficulty=self.difficulty)
        genesis.mine_block()
        return genesis

    @property
    def last_block(self):
        return self.chain[-1]

    # --- Динамическая сложность от числа участников ----------------------
    @staticmethod
    def _miner_of(block):
        """Адрес майнера блока (получатель coinbase) или None."""
        data = block.data
        if isinstance(data, list) and data and isinstance(data[0], dict):
            vout = data[0].get("vout")
            if vout and isinstance(vout[0], dict):
                return vout[0].get("address")
        return None

    def distinct_miners(self, upto=None):
        """Множество разных майнеров в цепочке (до блока upto, не включая)."""
        blocks = self.chain[:upto] if upto is not None else self.chain
        miners = set()
        for block in blocks:
            miner = self._miner_of(block)
            if miner:
                miners.add(miner)
        return miners

    def expected_difficulty(self, height):
        """Сложность, требуемая для блока на данной высоте (детерминирована)."""
        participants = len(self.distinct_miners(upto=height))
        return difficulty_for_participants(participants, base=self.difficulty)

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
        """Создаёт, майнит и добавляет новый блок в цепочку.

        Сложность выбирается динамически: чем больше разных майнеров уже
        участвовало в сети, тем выше требуемая сложность.
        """
        height = len(self.chain)
        new_block = Block(
            index=height,
            previous_hash=self.last_block.hash,
            data=data,
            difficulty=self.expected_difficulty(height),
        )
        new_block.mine_block()
        self.chain.append(new_block)
        return new_block

    def is_chain_valid(self):
        """Проверяет целостность цепочки, PoW и корректность сложности."""
        for i in range(1, len(self.chain)):
            current = self.chain[i]
            previous = self.chain[i - 1]

            # Корень Меркла должен соответствовать данным блока: иначе
            # подмена транзакций осталась бы незамеченной.
            if current.merkle_root != current._calculate_merkle_root():
                return False
            if current.hash != current.calculate_hash():
                return False
            if current.previous_hash != previous.hash:
                return False
            # Сложность блока должна совпадать с детерминированно ожидаемой…
            if current.difficulty != self.expected_difficulty(i):
                return False
            # …и хеш — реально удовлетворять этой сложности (PoW).
            if not current.hash.startswith("0" * current.difficulty):
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
