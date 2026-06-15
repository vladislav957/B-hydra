"""
Contract.py — простой смарт-контракт B-hydra.

Контракт хранит баланс и произвольные данные. Снимать средства и менять
данные может только владелец. Все операции возвращают текстовый результат.
"""


class SmartContract:
    """Минимальный смарт-контракт с балансом и хранилищем ключ-значение."""

    def __init__(self, owner, balance=0):
        self.owner = owner          # владелец контракта (адрес)
        self.balance = balance      # баланс контракта
        self.storage = {}           # произвольные данные

    def deposit(self, amount):
        if amount <= 0:
            return "Invalid deposit amount."
        self.balance += amount
        return f"Deposited {amount} units. New balance: {self.balance}"

    def withdraw(self, amount, requester):
        if requester != self.owner:
            return "Only the owner can withdraw funds."
        if amount <= 0 or amount > self.balance:
            return "Invalid withdrawal amount."
        self.balance -= amount
        return f"Withdrawn {amount} units. Remaining balance: {self.balance}"

    def set_data(self, key, value, requester):
        if requester != self.owner:
            return "Only the owner can set data."
        self.storage[key] = value
        return f"Data set: {key} = {value}"

    def get_data(self, key):
        if key not in self.storage:
            return "Key not found."
        return self.storage[key]


# Историческое имя класса с опечаткой — сохранено для совместимости.
SmartContrat = SmartContract


if __name__ == "__main__":
    c = SmartContract(owner="alice")
    print(c.deposit(100))
    print(c.withdraw(30, "alice"))
    print(c.withdraw(30, "bob"))
    print(c.set_data("note", "hello", "alice"))
    print(c.get_data("note"))
