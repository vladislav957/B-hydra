"""Смарт-контракты B-hydra: счёт-контракт и эскроу-обмен."""

from __future__ import annotations

from typing import Any


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
