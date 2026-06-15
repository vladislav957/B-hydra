"""
Node.py — узел сети B-hydra (модель UTXO).

Узел хранит блокчейн и мемпул, ведёт набор непотраченных выходов (UTXO),
принимает и проверяет транзакции (ссылки на UTXO + подписи входов), майнит
блоки с coinbase-наградой и считает балансы как сумму UTXO адреса.

Сетевую (сокетную) часть см. в P2P.py.
"""

import json

from Blockchain import Block, Blockchain, DEFAULT_DIFFICULTY
from Transactinons import (
    Transaction, TxInput, TxOutput, TransactionPool, coinbase,
)
from wallet import Wallet


class BHydraNode:
    """Логический узел B-hydra (блокчейн + мемпул + UTXO)."""

    def __init__(self, difficulty=DEFAULT_DIFFICULTY):
        self.blockchain = Blockchain(difficulty=difficulty)
        self.mempool = TransactionPool()

    # --- Набор UTXO ------------------------------------------------------
    def utxo_set(self):
        """
        Строит набор непотраченных выходов по всей цепочке.

        Возвращает dict: (txid, index) -> {"amount", "address"}.
        """
        utxos = {}
        for block in self.blockchain.chain:
            for tx in self._block_transactions(block):
                txid = tx["txid"]
                # Сначала удаляем потраченные входами выходы.
                for inp in tx.get("vin", []):
                    utxos.pop((inp["txid"], inp["index"]), None)
                # Затем добавляем новые выходы.
                for index, out in enumerate(tx.get("vout", [])):
                    utxos[(txid, index)] = {
                        "amount": out["amount"], "address": out["address"]
                    }
        return utxos

    def get_balance(self, address: str) -> float:
        """Баланс адреса = сумма его непотраченных выходов (UTXO)."""
        return sum(u["amount"] for u in self.utxo_set().values()
                   if u["address"] == address)

    def find_spendable(self, address: str):
        """UTXO, принадлежащие адресу: список (outpoint, amount)."""
        return [(outpoint, u["amount"])
                for outpoint, u in self.utxo_set().items()
                if u["address"] == address]

    # --- Проверка транзакции ---------------------------------------------
    def validate_transaction(self, tx: Transaction, utxos=None,
                             reserved=None) -> bool:
        """
        Проверяет обычную (не coinbase) транзакцию:
          * входы ссылаются на существующие непотраченные выходы;
          * публичный ключ входа соответствует адресу расходуемого выхода;
          * подпись каждого входа верна;
          * сумма входов >= суммы выходов (разница — комиссия >= 0);
          * нет повторного расходования.
        """
        if tx.is_coinbase:
            return False  # coinbase создаёт только узел при майнинге
        if not tx.vin or not tx.vout:
            return False
        if any(o.amount <= 0 for o in tx.vout):
            return False

        utxos = utxos if utxos is not None else self.utxo_set()
        reserved = reserved if reserved is not None else set()
        payload = tx.signing_payload()

        total_in = 0.0
        seen = set()
        for inp in tx.vin:
            outpoint = inp.outpoint
            if outpoint in seen or outpoint in reserved:
                return False  # двойная трата
            seen.add(outpoint)
            utxo = utxos.get(outpoint)
            if utxo is None:
                return False  # вход ссылается на несуществующий/потраченный выход
            if not inp.public_key or not inp.signature:
                return False
            # Публичный ключ должен соответствовать адресу расходуемого выхода.
            if Wallet.address_from_public_key(inp.public_key) != utxo["address"]:
                return False
            if not Wallet.verify(inp.public_key, payload, inp.signature):
                return False
            total_in += utxo["amount"]

        return total_in >= tx.total_output

    # --- Транзакции ------------------------------------------------------
    def add_transaction(self, tx: Transaction) -> bool:
        """Добавляет транзакцию в мемпул после проверки UTXO и подписей."""
        if tx is None:
            return False
        reserved = self.mempool.spent_outpoints()
        if not self.validate_transaction(tx, reserved=reserved):
            return False
        return self.mempool.add(tx)

    def create_transaction(self, wallet: Wallet, recipient: str,
                           amount: float, fee: float = 0.0) -> Transaction:
        """
        Собирает подписанную транзакцию: выбирает UTXO отправителя на сумму
        amount + fee, формирует выход получателю и сдачу обратно отправителю.

        Возвращает Transaction или None, если средств недостаточно.
        """
        need = amount + fee
        reserved = self.mempool.spent_outpoints()
        chosen, gathered = [], 0.0
        for outpoint, value in self.find_spendable(wallet.address):
            if outpoint in reserved:
                continue
            chosen.append((outpoint, value))
            gathered += value
            if gathered >= need:
                break
        if gathered < need:
            return None  # недостаточно средств

        vin = [TxInput(txid=op[0], index=op[1]) for op, _ in chosen]
        vout = [TxOutput(amount=amount, address=recipient)]
        change = gathered - need
        if change > 0:
            vout.append(TxOutput(amount=change, address=wallet.address))

        tx = Transaction(vin=vin, vout=vout)
        tx.sign(wallet)
        return tx

    # --- Майнинг ---------------------------------------------------------
    def mine_pending(self, miner_address: str):
        """Собирает транзакции из мемпула в блок и майнит его."""
        utxos = self.utxo_set()
        reserved = set()
        valid = []
        fees = 0.0
        for tx in self.mempool.take_all():
            if self.validate_transaction(tx, utxos=utxos, reserved=reserved):
                fees += self._tx_fee(tx, utxos)
                for inp in tx.vin:
                    reserved.add(inp.outpoint)
                valid.append(tx)

        height = len(self.blockchain.chain)
        reward = self.blockchain.block_reward(height)
        reward_tx = coinbase(miner_address, reward, fees, height=height)

        data = [reward_tx.to_dict()] + [tx.to_dict() for tx in valid]
        return self.blockchain.add_block(data=data)

    def _tx_fee(self, tx: Transaction, utxos) -> float:
        total_in = sum(utxos[inp.outpoint]["amount"] for inp in tx.vin)
        return total_in - tx.total_output

    # --- Служебное -------------------------------------------------------
    @staticmethod
    def _block_transactions(block):
        data = block.data
        if isinstance(data, (list, tuple)):
            return [tx for tx in data if isinstance(tx, dict)]
        return []

    def is_valid(self) -> bool:
        return self.blockchain.is_chain_valid()

    # --- Синхронизация P2P ----------------------------------------------
    @property
    def height(self) -> int:
        return len(self.blockchain.chain)

    def _prune_mempool(self):
        """Убирает из мемпула транзакции, уже попавшие в цепочку или ставшие
        невалидными (например, их входы потрачены)."""
        in_chain = {tx["txid"] for block in self.blockchain.chain
                    for tx in self._block_transactions(block)}
        utxos = self.utxo_set()
        reserved, kept = set(), []
        for tx in self.mempool.transactions:
            if tx.txid in in_chain:
                continue
            if self.validate_transaction(tx, utxos=utxos, reserved=reserved):
                for inp in tx.vin:
                    reserved.add(inp.outpoint)
                kept.append(tx)
        self.mempool.transactions = kept

    def receive_block(self, block_dict) -> bool:
        """
        Принимает одиночный блок от пира. Добавляет его, только если он
        продолжает нашу цепочку (его prev = наш последний хеш) и валиден.
        Возвращает False, если блок не подходит (нужна полная синхронизация).
        """
        block = Block.from_dict(block_dict)
        last = self.blockchain.last_block
        if block.previous_hash != last.hash or block.index != self.height:
            return False
        if block.merkle_root != block._calculate_merkle_root():
            return False
        if block.hash != block.calculate_hash():
            return False
        if block.difficulty != self.blockchain.expected_difficulty(block.index):
            return False
        if not block.hash.startswith("0" * block.difficulty):
            return False
        self.blockchain.chain.append(block)
        self._prune_mempool()
        return True

    def replace_chain(self, chain_dicts) -> bool:
        """
        Правило консенсуса: принять чужую цепочку, если она длиннее нашей,
        валидна и имеет тот же генезис. Возвращает True, если заменили.
        """
        if len(chain_dicts) <= self.height:
            return False
        candidate = Blockchain.from_dicts(chain_dicts, self.blockchain.difficulty)
        if candidate.chain[0].hash != self.blockchain.chain[0].hash:
            return False  # другой генезис — это другая сеть
        if not candidate.is_chain_valid():
            return False
        self.blockchain = candidate
        self._prune_mempool()
        return True

    # --- Обозреватель блоков (read-only) --------------------------------
    def get_block(self, index: int):
        """Блок по высоте (dict) или None."""
        if 0 <= index < len(self.blockchain.chain):
            return self.blockchain.chain[index].to_dict()
        return None

    def _resolve_output(self, txid, index):
        """Находит выход (txid, index) в цепочке — для подписи входов."""
        for block in self.blockchain.chain:
            for tx in self._block_transactions(block):
                if tx["txid"] == txid:
                    vout = tx.get("vout", [])
                    if 0 <= index < len(vout):
                        return vout[index]
        return None

    def find_transaction(self, txid: str):
        """Транзакция по txid вместе с номером блока, или None."""
        for block in self.blockchain.chain:
            for tx in self._block_transactions(block):
                if tx["txid"] == txid:
                    return {"transaction": tx, "block_index": block.index}
        return None

    def address_history(self, address: str):
        """История транзакций адреса: получено/потрачено по каждой транзакции."""
        history = []
        for block in self.blockchain.chain:
            for tx in self._block_transactions(block):
                received = sum(o["amount"] for o in tx.get("vout", [])
                               if o["address"] == address)
                sent = 0.0
                for inp in tx.get("vin", []):
                    ref = self._resolve_output(inp.get("txid"), inp.get("index"))
                    if ref and ref["address"] == address:
                        sent += ref["amount"]
                if received or sent:
                    history.append({
                        "txid": tx["txid"],
                        "block_index": block.index,
                        "timestamp": tx.get("timestamp"),
                        "received": received,
                        "sent": sent,
                    })
        return history

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

    # Алиса добывает первый блок и получает награду (coinbase-выход).
    node.mine_pending(alice.address)
    print(f"Баланс Алисы после майнинга: {node.get_balance(alice.address)} BHY")

    # Алиса переводит 10 BHY Бобу — тратит свой UTXO, остаток уходит сдачей.
    tx = node.create_transaction(alice, bob.address, amount=10, fee=0.5)
    print("Транзакция создана:", tx is not None)
    print("  входов:", len(tx.vin), "| выходов:", len(tx.vout))
    print("Принята в мемпул:", node.add_transaction(tx))

    # Боб майнит блок с этой транзакцией (получает награду + комиссию).
    node.mine_pending(bob.address)
    print(f"Баланс Алисы: {node.get_balance(alice.address)} BHY")
    print(f"Баланс Боба : {node.get_balance(bob.address)} BHY")
    print("Цепочка валидна:", node.is_valid())
