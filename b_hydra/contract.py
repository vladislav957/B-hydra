"""Смарт-контракты B-hydra.

Два уровня:

1. Учебные in-memory классы (`SmartContract`, `SmartCheck`) — демонстрируют
   идею, но не двигают монеты по цепочке.
2. `ContractManager` — контрактный слой НА ЦЕПОЧКЕ: средства реально
   блокируются на адресе контракта обычными UTXO-транзакциями, выплаты и
   возвраты — тоже обычные транзакции. Консенсус не меняется: любой узел
   проверяет эти переводы как самые обычные.

   * Эскроу-сделка: покупатель вносит депозит, оба участника подтверждают —
     контракт платит продавцу; отмена или дедлайн — возврат покупателю.
   * Смарт-чек: плательщик блокирует сумму и получает секрет; предъявитель
     секрета обналичивает чек на свой адрес до истечения срока, после —
     плательщик возвращает средства. Чек подписан ECDSA-ключом плательщика,
     подлинность проверяется без обращения к узлу (`verify_cheque`).
"""

from __future__ import annotations

import json
import secrets
import time
from typing import Any

from . import hashing
from .blockchain import CHAIN_ID
from .wallet import Wallet, generate_wallet, is_valid_address


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


# ---------------------------------------------------------------------------
# Контрактный слой на цепочке: эскроу и смарт-чеки с реальными UTXO.
# ---------------------------------------------------------------------------

def cheque_payload(cheque: dict) -> bytes:
    """Канонические байты чека для подписи плательщика (и её проверки)."""
    payload = {
        "chain_id": CHAIN_ID,
        "cheque_id": cheque["cheque_id"],
        "payer": cheque["payer"],
        "amount": cheque["amount"],
        "secret_hash": cheque["secret_hash"],
        "recipient": cheque["recipient"],
        "expires_at": cheque["expires_at"],
    }
    return json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")


def verify_cheque(cheque: dict) -> bool:
    """Подлинность чека: подпись плательщика верна и соответствует его адресу.

    Работает офлайн — получатель может проверить чек до похода к узлу.
    Подделка любого поля (сумма, срок, получатель…) ломает подпись.
    """
    try:
        pub = cheque["payer_public_key"]
        if Wallet.address_from_public_key(pub) != cheque["payer"]:
            return False
        return Wallet.verify(pub, cheque_payload(cheque), cheque["signature"])
    except (KeyError, TypeError, ValueError):
        return False


class ContractManager:
    """Смарт-контракты поверх узла: средства реально блокируются на цепочке.

    У менеджера собственный контрактный кошелёк. Депозит — обычная транзакция
    на его адрес, выплата/возврат — обычная транзакция с него, поэтому весь
    жизненный цикл контракта виден в обозревателе блоков и проверяется всеми
    узлами без изменения консенсуса.

    Конвенция комиссий: на контракт вносится amount + fee (fee — комиссия
    будущей выплаты), и сама депозитная транзакция платит майнеру ещё fee.
    Итого плательщик тратит amount + 2·fee, получатель получает ровно amount.
    """

    def __init__(self, node, wallet: Wallet | None = None) -> None:
        self.node = node
        self.wallet = wallet or generate_wallet()
        self.escrows: dict[str, dict] = {}
        self.cheques: dict[str, dict] = {}

    @property
    def address(self) -> str:
        """Адрес контрактного кошелька (сюда вносятся депозиты)."""
        return self.wallet.address

    # --- Общие операции с узлом -------------------------------------------
    def _deposit(self, payer: Wallet, amount: float, fee: float):
        """Блокирует amount + fee на адресе контракта транзакцией плательщика."""
        if amount <= 0:
            raise ValueError("сумма должна быть больше нуля")
        if fee < 0:
            raise ValueError("комиссия не может быть отрицательной")
        tx = self.node.create_transaction(payer, self.address, amount + fee, fee)
        if tx is None or not self.node.add_transaction(tx):
            raise ValueError(
                f"недостаточно средств: нужно {amount + 2 * fee:.4f} BHY "
                f"(сумма + комиссии депозита и выплаты)")
        return tx

    def _payout(self, to: str, amount: float, fee: float):
        """Выплата с контрактного кошелька (учитывает неподтверждённый депозит)."""
        tx = self.node.create_transaction(self.wallet, to, amount, fee)
        if tx is None or not self.node.add_transaction(tx):
            raise ValueError("контракт не смог собрать выплату (депозит потерян?)")
        return tx

    # --- Эскроу-сделка ------------------------------------------------------
    def open_escrow(self, buyer: Wallet, seller: str, amount: float,
                    fee: float = 0.0, deadline: float | None = None) -> dict:
        """Открывает сделку: депозит покупателя блокируется до подтверждений.

        deadline (epoch, сек) — необязательный срок: после него сделку может
        отменить кто угодно, и депозит вернётся покупателю.
        """
        if not is_valid_address(seller):
            raise ValueError("неверный адрес продавца (BHY…)")
        if seller == buyer.address:
            raise ValueError("покупатель и продавец не могут совпадать")
        deposit = self._deposit(buyer, amount, fee)
        escrow = {
            "escrow_id": deposit.txid,
            "type": "escrow",
            "buyer": buyer.address,
            "seller": seller,
            "amount": float(amount),
            "fee": float(fee),
            "deadline": deadline,
            "status": "open",                # open → completed | cancelled
            "confirmed": {buyer.address: False, seller: False},
            "deposit_txid": deposit.txid,
            "payout_txid": None,
            "refund_txid": None,
            "created_at": time.time(),
        }
        self.escrows[escrow["escrow_id"]] = escrow
        return escrow

    def confirm_escrow(self, escrow_id: str, party: str) -> dict:
        """Подтверждение участника; после подтверждения ОБОИХ — выплата продавцу."""
        escrow = self.escrows.get(escrow_id)
        if escrow is None:
            raise ValueError("эскроу не найден")
        if escrow["status"] != "open":
            raise ValueError(f"эскроу уже закрыт (статус: {escrow['status']})")
        if party not in escrow["confirmed"]:
            raise ValueError("подтверждать могут только участники сделки")
        escrow["confirmed"][party] = True
        if all(escrow["confirmed"].values()):
            tx = self._payout(escrow["seller"], escrow["amount"], escrow["fee"])
            escrow["status"] = "completed"
            escrow["payout_txid"] = tx.txid
        return escrow

    def cancel_escrow(self, escrow_id: str, party: str) -> dict:
        """Отмена сделки с возвратом депозита покупателю.

        До дедлайна отменить может только участник; после дедлайна — кто
        угодно (защита от «зависшего» депозита при пропавшем контрагенте).
        """
        escrow = self.escrows.get(escrow_id)
        if escrow is None:
            raise ValueError("эскроу не найден")
        if escrow["status"] != "open":
            raise ValueError(f"эскроу уже закрыт (статус: {escrow['status']})")
        expired = (escrow["deadline"] is not None
                   and time.time() > escrow["deadline"])
        if not expired and party not in escrow["confirmed"]:
            raise ValueError("отменить может только участник сделки "
                             "(любой — после дедлайна)")
        tx = self._payout(escrow["buyer"], escrow["amount"], escrow["fee"])
        escrow["status"] = "cancelled"
        escrow["refund_txid"] = tx.txid
        return escrow

    # --- Смарт-чек ----------------------------------------------------------
    def write_cheque(self, payer: Wallet, amount: float, fee: float = 0.0,
                     expires_in: float = 86400.0,
                     recipient: str | None = None) -> tuple[dict, str]:
        """Выписывает чек: блокирует средства и возвращает (чек, секрет).

        Секрет выдаётся ОДИН раз и не хранится (в чеке — только его SHA-512):
        плательщик передаёт получателю пару (cheque_id, секрет) любым каналом.
        recipient=None — чек на предъявителя (обналичит владелец секрета на
        любой адрес); адрес — именной чек (только на этот адрес).
        """
        if recipient is not None and not is_valid_address(recipient):
            raise ValueError("неверный адрес получателя чека (BHY…)")
        if expires_in <= 0:
            raise ValueError("срок действия чека должен быть больше нуля")
        secret = secrets.token_hex(16)
        deposit = self._deposit(payer, amount, fee)
        cheque = {
            "cheque_id": deposit.txid,
            "type": "cheque",
            "payer": payer.address,
            "payer_public_key": payer.public_key_hex,
            "amount": float(amount),
            "fee": float(fee),
            "secret_hash": hashing.sha512(secret),
            "recipient": recipient,
            "expires_at": time.time() + float(expires_in),
            "status": "active",              # active → cashed | refunded
            "deposit_txid": deposit.txid,
            "cashed_txid": None,
            "cashed_to": None,
            "refund_txid": None,
            "created_at": time.time(),
        }
        cheque["signature"] = payer.sign(cheque_payload(cheque))
        self.cheques[cheque["cheque_id"]] = cheque
        return cheque, secret

    def cash_cheque(self, cheque_id: str, secret: str, to: str) -> dict:
        """Обналичивает чек: предъявитель секрета получает выплату на адрес to."""
        cheque = self.cheques.get(cheque_id)
        if cheque is None:
            raise ValueError("чек не найден")
        if cheque["status"] != "active":
            raise ValueError(f"чек уже погашен или отозван (статус: {cheque['status']})")
        if time.time() > cheque["expires_at"]:
            raise ValueError("срок действия чека истёк — "
                             "плательщик может вернуть средства")
        if hashing.sha512(secret or "") != cheque["secret_hash"]:
            raise ValueError("неверный секрет чека")
        if not is_valid_address(to):
            raise ValueError("неверный адрес для зачисления (BHY…)")
        if cheque["recipient"] is not None and to != cheque["recipient"]:
            raise ValueError("именной чек: обналичить можно только "
                             "на адрес получателя")
        tx = self._payout(to, cheque["amount"], cheque["fee"])
        cheque["status"] = "cashed"
        cheque["cashed_txid"] = tx.txid
        cheque["cashed_to"] = to
        return cheque

    def refund_cheque(self, cheque_id: str, payer: str) -> dict:
        """Возврат по непогашенному чеку — только плательщику и только после
        истечения срока (пока чек действует, средства обещаны предъявителю)."""
        cheque = self.cheques.get(cheque_id)
        if cheque is None:
            raise ValueError("чек не найден")
        if cheque["status"] != "active":
            raise ValueError(f"чек уже погашен или отозван (статус: {cheque['status']})")
        if payer != cheque["payer"]:
            raise ValueError("вернуть средства может только плательщик")
        if time.time() <= cheque["expires_at"]:
            raise ValueError("чек ещё действует — возврат возможен "
                             "после истечения срока")
        tx = self._payout(cheque["payer"], cheque["amount"], cheque["fee"])
        cheque["status"] = "refunded"
        cheque["refund_txid"] = tx.txid
        return cheque

    # --- Сохранение / загрузка ----------------------------------------------
    def to_dict(self) -> dict:
        """Состояние менеджера для персистентности (ключ контракта + записи)."""
        return {"private_key": self.wallet.private_key_hex,
                "escrows": self.escrows,
                "cheques": self.cheques}

    @classmethod
    def from_dict(cls, node, data: dict) -> "ContractManager":
        manager = cls(node, Wallet.from_private_hex(data["private_key"]))
        manager.escrows = dict(data.get("escrows", {}))
        manager.cheques = dict(data.get("cheques", {}))
        return manager


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
