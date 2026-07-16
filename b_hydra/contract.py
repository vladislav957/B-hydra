"""Смарт-контракты B-hydra: счёт-контракт, эскроу-обмен и смарт-чек (HTLC).

Есть два уровня:
  * SmartContract / SmartCheck — простые учебные модели (в памяти);
  * SmartCheque — НАСТОЯЩИЙ смарт-чек на нашей крипте: подпись ECDSA
    плательщика + хеш-замок (секрет) + время-замок (срок в блоках), как
    hash-time-locked contract (HTLC) в Bitcoin. Проверяется любым узлом.
"""

from __future__ import annotations

import json
from typing import Any

if __name__ == "__main__" and __package__ in (None, ""):
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    __package__ = "b_hydra"


class SmartContract:
    """Минимальный смарт-контракт с балансом и хранилищем «ключ-значение».

    Снимать средства и менять данные может только владелец.
    """

    def __init__(self, owner: str, balance: float = 0) -> None:
        self.owner = owner
        self.balance = balance
        self.storage: dict[Any, Any] = {}

    def deposit(self, amount: float) -> str:
        if amount <= 0:
            return "Invalid deposit amount."
        self.balance += amount
        return f"Deposited {amount} units. New balance: {self.balance}"

    def withdraw(self, amount: float, requester: str) -> str:
        if requester != self.owner:
            return "Only the owner can withdraw funds."
        if amount <= 0 or amount > self.balance:
            return "Invalid withdrawal amount."
        self.balance -= amount
        return f"Withdrawn {amount} units. Remaining balance: {self.balance}"

    def set_data(self, key: Any, value: Any, requester: str) -> str:
        if requester != self.owner:
            return "Only the owner can set data."
        self.storage[key] = value
        return f"Data set: {key} = {value}"

    def get_data(self, key: Any) -> Any:
        if key not in self.storage:
            return "Key not found."
        return self.storage[key]


# Историческое имя класса с опечаткой — сохранено для совместимости.
SmartContrat = SmartContract


class SmartCheck:
    """Эскроу: обмен активами между несколькими сторонами.

    Обмен считается завершённым только когда все участники подтвердили активы.
    """

    def __init__(self) -> None:
        self.parties: dict[str, bool] = {}
        self.assets: dict[str, Any] = {}
        self.status = "Pending"

    def add_party(self, party_id: str, asset: Any) -> str:
        if party_id in self.parties:
            return f"Party {party_id} is already part of the exchange."
        self.parties[party_id] = False
        self.assets[party_id] = asset
        return f"Party {party_id} added with asset: {asset}"

    def confirm_asset(self, party_id: str) -> str:
        if party_id not in self.parties:
            return f"Party {party_id} is not part of the exchange."
        self.parties[party_id] = True
        self.check_exchange_status()
        return f"Party {party_id} has confirmed the asset."

    def check_exchange_status(self) -> str:
        if self.parties and all(self.parties.values()):
            self.status = "Completed"
            return "Exchange completed successfully!"
        return "Exchange is still pending. Awaiting confirmation."

    def get_status(self) -> str:
        return f"Exchange status: {self.status}."


class SmartCheque:
    """Смарт-чек B-hydra — подписанное платёжное обязательство с условиями.

    Модель как у hash-time-locked contract (HTLC) в Bitcoin:
      * плательщик подписывает чек своим ключом ECDSA (подлинность);
      * ХЕШ-ЗАМОК: обналичить можно, только зная секрет, чей SHA-512 указан
        в чеке (`secret_hash`);
      * ВРЕМЯ-ЗАМОК: чек действителен до блока `expiry`; после — возврат
        плательщику.

    Чек самодостаточен и проверяется любым узлом: `verify()` сверяет подпись,
    `can_redeem()`/`can_refund()` — условия. Оплата (перевод монет) выполняется
    функцией `honor_cheque()` — плательщик исполняет чек настоящей транзакцией
    UTXO, когда секрет раскрыт и срок не вышел.
    """

    def __init__(self, payer, payee, amount, secret_hash, expiry,
                 fee=0.0, payer_pubkey="", signature="", chain_id=None):
        self.payer = payer                 # адрес плательщика (BHY…)
        self.payee = payee                 # адрес получателя (BHY…)
        self.amount = float(amount)
        self.fee = float(fee)
        self.secret_hash = secret_hash     # SHA-512(секрет), hex
        self.expiry = int(expiry)          # высота блока, до которой чек живёт
        self.payer_pubkey = payer_pubkey   # публичный ключ плательщика, hex
        self.signature = signature         # подпись ECDSA плательщика, hex
        if chain_id is None:
            from .blockchain import CHAIN_ID
            chain_id = CHAIN_ID
        self.chain_id = chain_id           # защита от replay между сетями

    # --- Построение и подпись -------------------------------------------
    @staticmethod
    def hash_secret(secret) -> str:
        """SHA-512 от секрета (нашей реализацией) — это и есть хеш-замок."""
        from . import hashing
        if isinstance(secret, str):
            secret = secret.encode("utf-8")
        return hashing.sha512(secret)

    def _signing_payload(self) -> bytes:
        """Канонические байты чека для подписи (без самой подписи)."""
        data = {
            "type": "b-hydra-cheque",
            "chain_id": self.chain_id,
            "payer": self.payer,
            "payee": self.payee,
            "amount": self.amount,
            "fee": self.fee,
            "secret_hash": self.secret_hash,
            "expiry": self.expiry,
            "payer_pubkey": self.payer_pubkey,
        }
        return json.dumps(data, sort_keys=True,
                          separators=(",", ":")).encode("utf-8")

    @classmethod
    def issue(cls, payer_wallet, payee: str, amount: float, secret,
              expiry: int, fee: float = 0.0) -> "SmartCheque":
        """Плательщик выписывает и подписывает чек на `payee`.

        `secret` держит у себя и передаёт получателю приватно; в чек кладётся
        только его хеш. Получатель обналичит чек, раскрыв секрет до блока
        `expiry`.
        """
        cheque = cls(
            payer=payer_wallet.address, payee=payee, amount=amount,
            secret_hash=cls.hash_secret(secret), expiry=expiry, fee=fee,
            payer_pubkey=payer_wallet.public_key_hex,
        )
        cheque.signature = payer_wallet.sign(cheque._signing_payload())
        return cheque

    # --- Проверки (детерминированы, доступны любому узлу) ----------------
    def verify(self) -> bool:
        """Подпись подлинна, адреса корректны, суммы разумны."""
        from .wallet import Wallet, is_valid_address
        if not (is_valid_address(self.payer) and is_valid_address(self.payee)):
            return False
        if self.amount <= 0 or self.fee < 0:
            return False
        if not self.signature or not self.payer_pubkey:
            return False
        # Публичный ключ обязан соответствовать адресу плательщика.
        if Wallet.address_from_public_key(self.payer_pubkey) != self.payer:
            return False
        return Wallet.verify(self.payer_pubkey, self._signing_payload(),
                             self.signature)

    def secret_matches(self, secret) -> bool:
        """Секрет подходит к хеш-замку чека."""
        return self.hash_secret(secret) == self.secret_hash

    def can_redeem(self, secret, current_height: int) -> bool:
        """Получатель может обналичить: подпись верна, секрет верный, срок жив."""
        return (self.verify() and self.secret_matches(secret)
                and current_height <= self.expiry)

    def can_refund(self, current_height: int) -> bool:
        """Плательщик может вернуть средства: чек подлинный и срок вышел."""
        return self.verify() and current_height > self.expiry

    # --- Сериализация ----------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "type": "b-hydra-cheque",
            "chain_id": self.chain_id,
            "payer": self.payer,
            "payee": self.payee,
            "amount": self.amount,
            "fee": self.fee,
            "secret_hash": self.secret_hash,
            "expiry": self.expiry,
            "payer_pubkey": self.payer_pubkey,
            "signature": self.signature,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SmartCheque":
        return cls(
            payer=d["payer"], payee=d["payee"], amount=d["amount"],
            secret_hash=d["secret_hash"], expiry=d["expiry"],
            fee=d.get("fee", 0.0), payer_pubkey=d.get("payer_pubkey", ""),
            signature=d.get("signature", ""), chain_id=d.get("chain_id"),
        )

    def __repr__(self):
        return (f"<SmartCheque {self.amount} BHY {self.payer[:10]}…→"
                f"{self.payee[:10]}… expiry#{self.expiry}>")


def honor_cheque(cheque: "SmartCheque", secret, payer_wallet, node):
    """Плательщик исполняет чек: секрет раскрыт и срок не вышел → создаёт и
    отправляет НАСТОЯЩУЮ транзакцию UTXO получателю. Возвращает Transaction
    или None (условия не выполнены / нет средств / чужой кошелёк).

    Замечание: полностью безоверительная блокировка средств «на самом чеке»
    требует скриптовых выходов UTXO (как OP_HASHLOCK в Bitcoin) — это возможное
    развитие. Здесь чек криптографически удостоверяет условия, а перевод
    выполняет плательщик, честно исполняя обязательство.
    """
    if payer_wallet.address != cheque.payer:
        return None
    if not cheque.can_redeem(secret, node.height):
        return None
    tx = node.create_transaction(payer_wallet, cheque.payee,
                                 cheque.amount, fee=cheque.fee)
    if tx is None or not node.add_transaction(tx):
        return None
    return tx


if __name__ == "__main__":
    contract = SmartContract(owner="alice")
    print(contract.deposit(100))
    print(contract.withdraw(30, "alice"))

    check = SmartCheck()
    print(check.add_party("Alice", "10 BHY"))
    print(check.add_party("Bob", "Token-X"))
    print(check.confirm_asset("Alice"))
    print(check.confirm_asset("Bob"))
    print(check.get_status())

    # --- Демонстрация смарт-чека (HTLC) ---
    print("\n=== Смарт-чек (hash-time-lock) ===")
    from .node import BHydraNode
    from .wallet import generate_wallet

    node = BHydraNode(difficulty=2)
    node.blockchain.retarget_interval = 4
    payer = generate_wallet()
    payee = generate_wallet()
    node.mine_pending(payer.address)          # у плательщика появились монеты

    secret = "b-hydra-42"                     # секрет знает только плательщик и…
    cheque = SmartCheque.issue(payer, payee.address, amount=10,
                               secret=secret, expiry=node.height + 100, fee=0.0001)
    print("Чек выписан      :", cheque)
    print("Подпись подлинна :", cheque.verify())
    print("Чужой секрет     :", cheque.can_redeem("wrong", node.height))
    print("Верный секрет    :", cheque.can_redeem(secret, node.height))

    tx = honor_cheque(cheque, secret, payer, node)
    node.mine_pending(payer.address)
    print("Чек оплачен, txid:", tx.txid[:24], "…")
    print("Баланс получателя:", node.get_balance(payee.address), "BHY")

