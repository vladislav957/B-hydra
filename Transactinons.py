"""
Transactinons.py — транзакции B-hydra и мемпул.

Транзакция переводит средства от отправителя (его публичный ключ) к получателю
(адрес) и подписывается приватным ключом отправителя. Подпись проверяется
публичным ключом, что доказывает право распоряжаться средствами.

Имя файла исторически содержит опечатку (Transactinons) — оно сохранено,
поскольку на него ссылаются другие модули проекта.
"""

import hashlib
import json
import time

# Условный адрес «эмиссии»: с него приходит награда за блок (coinbase).
COINBASE_SENDER = "B-HYDRA-COINBASE"


class Transaction:
    """Перевод средств в сети B-hydra."""

    def __init__(self, sender, recipient, amount, fee=0.0, timestamp=None,
                 public_key=None, signature=None):
        self.sender = sender            # адрес/идентификатор отправителя
        self.recipient = recipient      # адрес получателя
        self.amount = float(amount)
        self.fee = float(fee)
        self.timestamp = timestamp if timestamp is not None else time.time()
        self.public_key = public_key    # hex публичного ключа отправителя
        self.signature = signature      # hex подписи

    # --- Идентификация и сериализация ------------------------------------
    def signing_payload(self) -> bytes:
        """Канонические байты, которые подписываются и хешируются."""
        payload = {
            "sender": self.sender,
            "recipient": self.recipient,
            "amount": self.amount,
            "fee": self.fee,
            "timestamp": self.timestamp,
        }
        return json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")

    @property
    def txid(self) -> str:
        """Идентификатор транзакции = SHA-512 от её содержимого."""
        return hashlib.sha512(self.signing_payload()).hexdigest()

    def to_dict(self):
        return {
            "txid": self.txid,
            "sender": self.sender,
            "recipient": self.recipient,
            "amount": self.amount,
            "fee": self.fee,
            "timestamp": self.timestamp,
            "public_key": self.public_key,
            "signature": self.signature,
        }

    @classmethod
    def from_dict(cls, data):
        return cls(
            sender=data["sender"],
            recipient=data["recipient"],
            amount=data["amount"],
            fee=data.get("fee", 0.0),
            timestamp=data.get("timestamp"),
            public_key=data.get("public_key"),
            signature=data.get("signature"),
        )

    # --- Подпись и проверка ----------------------------------------------
    def sign(self, wallet):
        """Подписывает транзакцию кошельком-отправителем."""
        self.public_key = wallet.public_key_hex
        self.signature = wallet.sign(self.signing_payload())
        return self

    @property
    def is_coinbase(self) -> bool:
        return self.sender == COINBASE_SENDER

    def is_valid(self) -> bool:
        """Проверяет корректность транзакции и её подписи."""
        if self.amount <= 0 or self.fee < 0:
            return False
        if self.is_coinbase:
            # Награда за блок не имеет отправителя и подписи.
            return True
        if not self.public_key or not self.signature:
            return False
        # Импорт здесь, чтобы избежать циклической зависимости с wallet.
        from wallet import Wallet
        return Wallet.verify(self.public_key, self.signing_payload(), self.signature)

    def __repr__(self):
        return (f"<Tx {self.sender[:10]}…→{self.recipient[:10]}… "
                f"{self.amount} BHY (fee {self.fee})>")


def coinbase(recipient, reward, fee_total=0.0):
    """Создаёт coinbase-транзакцию (награда майнеру + собранные комиссии)."""
    return Transaction(
        sender=COINBASE_SENDER,
        recipient=recipient,
        amount=reward + fee_total,
        fee=0.0,
    )


class TransactionPool:
    """Мемпул неподтверждённых транзакций."""

    def __init__(self):
        self.transactions = []

    def add(self, transaction: Transaction) -> bool:
        if not transaction.is_valid():
            return False
        if any(t.txid == transaction.txid for t in self.transactions):
            return False  # дубликат
        self.transactions.append(transaction)
        return True

    def take_all(self):
        """Забирает все транзакции из пула (для включения в блок)."""
        pending = self.transactions
        self.transactions = []
        return pending

    def __len__(self):
        return len(self.transactions)


# Совместимость со старым кодом, который импортировал `Transactions`.
Transactions = Transaction


if __name__ == "__main__":
    tx = Transaction("Alice", "Bob", 10, fee=0.1)
    print("txid:", tx.txid[:32], "…")
    print("coinbase valid:", coinbase("Miner", 50).is_valid())
