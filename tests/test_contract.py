"""Тесты смарт-контракта и эскроу."""

from Contract import SmartContract
from SmartCheck import SmartCheck


def test_deposit_and_withdraw():
    c = SmartContract("alice")
    c.deposit(100)
    assert c.balance == 100
    c.withdraw(30, "alice")
    assert c.balance == 70


def test_only_owner_can_withdraw():
    c = SmartContract("alice")
    c.deposit(100)
    c.withdraw(50, "bob")          # не владелец
    assert c.balance == 100        # баланс не изменился


def test_storage():
    c = SmartContract("alice")
    c.set_data("note", "hello", "alice")
    assert c.get_data("note") == "hello"
    assert c.get_data("missing") == "Key not found."


def test_escrow_completes_when_all_confirm():
    s = SmartCheck()
    s.add_party("A", "x")
    s.add_party("B", "y")
    s.confirm_asset("A")
    assert s.status == "Pending"
    s.confirm_asset("B")
    assert s.status == "Completed"
