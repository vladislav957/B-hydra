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
    """Выход транзакции: сумма и условие траты.

    По умолчанию — обычный P2PKH: тратит тот, чей адрес = `address`. Если задан
    `script` (dict), выход становится СКРИПТОВЫМ: условие траты описывает скрипт
    (например, HTLC — хеш-замок + время-замок). Скрипт детерминированно проверяют
    все узлы. У обычных выходов `script` отсутствует и в словаре, и в хеше — так
    старые транзакции остаются побитово теми же (txid не меняется).
    """

    def __init__(self, amount, address, script=None):
        self.amount = float(amount)
        self.address = address        # для P2PKH — владелец; для скрипта — справочно
        self.script = script          # None → P2PKH; dict → условие (HTLC и т.п.)

    def to_dict(self):
        d = {"amount": self.amount, "address": self.address}
        if self.script is not None:
            d["script"] = self.script
        return d

    @classmethod
    def from_dict(cls, data):
        return cls(amount=data["amount"], address=data["address"],
                   script=data.get("script"))

    @classmethod
    def htlc(cls, amount, secret_hash, recipient, refund, expiry):
        """Скриптовый выход HTLC: обналичить может получатель, зная секрет, до
        блока `expiry`; после — возврат плательщику (`refund`)."""
        return cls(amount, recipient, script={
            "type": "htlc",
            "hash": secret_hash,       # SHA-512(секрет) — хеш-замок
            "recipient": recipient,    # кто обналичивает секретом
            "refund": refund,          # кому возврат после срока
            "expiry": int(expiry),     # высота блока — время-замок
        })

    def __repr__(self):
        kind = f" [{self.script['type']}]" if self.script else ""
        return f"<TxOut {self.amount} BHY → {self.address[:12]}…{kind}>"


class TxInput:
    """Вход транзакции: ссылка на конкретный выход (txid, index) + подпись."""

    def __init__(self, txid, index, public_key=None, signature=None,
                 preimage=None):
        self.txid = txid            # id транзакции, чей выход расходуется
        self.index = index          # номер выхода в той транзакции (vout)
        self.public_key = public_key  # hex публичного ключа владельца
        self.signature = signature    # hex подписи входа
        # Секрет (preimage) для раскрытия хеш-замка HTLC. Это «свидетель», в
        # подпись/txid он не входит (как witness в Bitcoin): подделать нельзя —
        # неверный секрет не пройдёт хеш-проверку.
        self.preimage = preimage

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
        if self.preimage is not None:
            d["preimage"] = self.preimage
        return d

    @classmethod
    def from_dict(cls, data):
        return cls(
            txid=data["txid"],
            index=data["index"],
            public_key=data.get("public_key"),
            signature=data.get("signature"),
            preimage=data.get("preimage"),
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
        """Подписывает все входы транзакции кошельком-владельцем."""
        payload = self.signing_payload()
        sig = wallet.sign(payload)
        for inp in self.vin:
            inp.public_key = wallet.public_key_hex
            inp.signature = sig
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
    """Мемпул неподтверждённых транзакций."""

    def __init__(self):
        self.transactions = []

    def add(self, transaction: Transaction) -> bool:
        if any(t.txid == transaction.txid for t in self.transactions):
            return False  # дубликат
        self.transactions.append(transaction)
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
