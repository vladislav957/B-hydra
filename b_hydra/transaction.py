"""
Transactinons.py — транзакции B-hydra по модели UTXO (входы и выходы).

Как в Bitcoin, транзакция состоит из:
  * входов (vin)  — ссылок на непотраченные выходы прошлых транзакций (UTXO);
  * выходов (vout) — новых сумм, заблокированных на адрес получателя.

Каждый вход подписывается владельцем расходуемого выхода. Сумма входов должна
быть не меньше суммы выходов; разница — комиссия майнеру. Награда за блок
оформляется специальной coinbase-транзакцией без реальных входов.

Имя файла исторически содержит опечатку (Transactinons) — оно сохранено,
поскольку на него ссылаются другие модули проекта.
"""

import json
import time

if __name__ == "__main__" and __package__ in (None, ""):
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    __package__ = "b_hydra"

from . import hashing
from .blockchain import CHAIN_ID

# Псевдо-идентификатор «ниоткуда» для входа coinbase-транзакции.
NULL_TXID = "0" * 128


class TxOutput:
    """Выход транзакции: сумма, заблокированная на адрес получателя."""

    def __init__(self, amount, address):
        self.amount = float(amount)
        self.address = address

    def to_dict(self):
        return {"amount": self.amount, "address": self.address}

    @classmethod
    def from_dict(cls, data):
        return cls(amount=data["amount"], address=data["address"])

    def __repr__(self):
        return f"<TxOut {self.amount} BHY → {self.address[:12]}…>"


class TxInput:
    """Вход транзакции: ссылка на конкретный выход (txid, index) + подпись."""

    def __init__(self, txid, index, public_key=None, signature=None,
                 pq_public_key=None, pq_signature=None):
        self.txid = txid            # id транзакции, чей выход расходуется
        self.index = index          # номер выхода в той транзакции (vout)
        self.public_key = public_key  # hex ECDSA-публичного ключа владельца
        self.signature = signature    # hex ECDSA-подписи входа
        # Пост-квантовая часть (только для гибридных входов):
        self.pq_public_key = pq_public_key   # XMSS-корень (hex) владельца
        self.pq_signature = pq_signature     # XMSS-подпись (dict) или None

    @property
    def outpoint(self):
        """Уникальная ссылка на расходуемый выход."""
        return (self.txid, self.index)

    def to_dict(self):
        d = {
            "txid": self.txid,
            "index": self.index,
            "public_key": self.public_key,
            "signature": self.signature,
        }
        # PQ-поля пишем только у гибридных входов — обычные tx не меняются
        # (txid и совместимость сериализации сохранены).
        if self.pq_public_key is not None or self.pq_signature is not None:
            d["pq_public_key"] = self.pq_public_key
            d["pq_signature"] = self.pq_signature
        return d

    @classmethod
    def from_dict(cls, data):
        return cls(
            txid=data["txid"],
            index=data["index"],
            public_key=data.get("public_key"),
            signature=data.get("signature"),
            pq_public_key=data.get("pq_public_key"),
            pq_signature=data.get("pq_signature"),
        )

    def __repr__(self):
        return f"<TxIn {self.txid[:12]}…:{self.index}>"


class Transaction:
    """Транзакция UTXO: набор входов (vin) и выходов (vout)."""

    def __init__(self, vin=None, vout=None, timestamp=None):
        self.vin = vin or []        # список TxInput
        self.vout = vout or []      # список TxOutput
        self.timestamp = timestamp if timestamp is not None else time.time()

    # --- Идентификация и сериализация ------------------------------------
    def signing_payload(self) -> bytes:
        """
        Канонические байты для подписи и txid: идентификатор сети (chain_id,
        защита от replay), outpoints входов и выходы (без подписей и публичных
        ключей), плюс временная метка.
        """
        payload = {
            "chain_id": CHAIN_ID,
            "vin": [{"txid": i.txid, "index": i.index} for i in self.vin],
            "vout": [o.to_dict() for o in self.vout],
            "timestamp": self.timestamp,
        }
        return json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")

    @property
    def txid(self) -> str:
        """Идентификатор транзакции = SHA-512 от её содержимого."""
        return hashing.sha512(self.signing_payload())

    def to_dict(self):
        return {
            "txid": self.txid,
            "vin": [i.to_dict() for i in self.vin],
            "vout": [o.to_dict() for o in self.vout],
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data):
        return cls(
            vin=[TxInput.from_dict(i) for i in data.get("vin", [])],
            vout=[TxOutput.from_dict(o) for o in data.get("vout", [])],
            timestamp=data.get("timestamp"),
        )

    # --- Суммы -----------------------------------------------------------
    @property
    def total_output(self) -> float:
        return sum(o.amount for o in self.vout)

    # --- Подпись ---------------------------------------------------------
    def sign(self, wallet):
        """Подписывает все входы транзакции кошельком-владельцем (ECDSA)."""
        payload = self.signing_payload()
        sig = wallet.sign(payload)
        for inp in self.vin:
            inp.public_key = wallet.public_key_hex
            inp.signature = sig
        return self

    def sign_hybrid(self, hybrid_wallet):
        """Подписывает входы ОБЕИМИ схемами (ECDSA + XMSS) — для трат с
        гибридного адреса. Каждый вход тратит один одноразовый XMSS-ключ,
        поэтому на входы уходят РАЗНЫЕ подписи (индексы не переиспользуются)."""
        payload = self.signing_payload()
        for inp in self.vin:
            ecdsa_sig, pq_sig = hybrid_wallet.sign(payload)
            inp.public_key = hybrid_wallet.ecdsa_public_key_hex
            inp.signature = ecdsa_sig
            inp.pq_public_key = hybrid_wallet.pq_public_key
            inp.pq_signature = pq_sig
        return self

    @property
    def is_coinbase(self) -> bool:
        return len(self.vin) == 1 and self.vin[0].txid == NULL_TXID

    def __repr__(self):
        kind = "coinbase" if self.is_coinbase else "tx"
        return f"<{kind} {self.txid[:12]}… in={len(self.vin)} out={len(self.vout)}>"


def coinbase(recipient, reward, fee_total=0.0, height=0, message="B-hydra"):
    """
    Создаёт coinbase-транзакцию (награда майнеру + собранные комиссии).

    Вход — фиктивный (NULL_TXID); `height` в поле index делает txid уникальным
    для каждого блока. Подпись не требуется.
    """
    vin = [TxInput(txid=NULL_TXID, index=height, public_key=message)]
    vout = [TxOutput(amount=reward + fee_total, address=recipient)]
    return Transaction(vin=vin, vout=vout)


class TransactionPool:
    """Мемпул неподтверждённых транзакций.

    max_size ограничивает вместимость (анти-DoS): по умолчанию пул держит до
    MAX_MEMPOOL_TRANSACTIONS транзакций. Поиск дублей идёт по множеству txid,
    поэтому add() — O(1), и мемпул спокойно вмещает десятки тысяч транзакций.
    """

    def __init__(self, max_size=50000):
        self.max_size = max_size
        self._transactions = []
        self._txids = set()

    @property
    def transactions(self):
        return self._transactions

    @transactions.setter
    def transactions(self, txs):
        # Прямое присваивание (например, из _prune_mempool) держит индекс
        # txid в синхроне, чтобы дедуп в add() оставался корректным.
        self._transactions = list(txs)
        self._txids = {t.txid for t in self._transactions}

    def add(self, transaction: Transaction) -> bool:
        if transaction.txid in self._txids:
            return False  # дубликат
        if self.max_size is not None and len(self._transactions) >= self.max_size:
            return False  # мемпул переполнен
        self._transactions.append(transaction)
        self._txids.add(transaction.txid)
        return True

    def spent_outpoints(self):
        """Все outpoints, уже расходуемые транзакциями в мемпуле."""
        spent = set()
        for tx in self.transactions:
            for inp in tx.vin:
                spent.add(inp.outpoint)
        return spent

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
    cb = coinbase("BHYminer", 50, fee_total=0.5, height=1)
    print("coinbase:", cb)
    print("  is_coinbase:", cb.is_coinbase)
    print("  txid:", cb.txid[:32], "…")
    print("  выход:", cb.vout[0])
