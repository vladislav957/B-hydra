"""Тесты узла: майнинг, балансы, переводы и защита от подделок (UTXO)."""

import pytest

from Node import BHydraNode
from Transactinons import Transaction, TxInput, TxOutput
from wallet import generate_wallet


@pytest.fixture
def funded():
    """Узел, где у Alice есть один UTXO на 50 BHY (награда за блок)."""
    node = BHydraNode(difficulty=2)
    alice = generate_wallet()
    node.mine_pending(alice.address)
    return node, alice


def _alice_outpoint(node, alice):
    return next(op for op, u in node.utxo_set().items()
               if u["address"] == alice.address)


def test_mining_gives_reward(funded):
    node, alice = funded
    assert node.get_balance(alice.address) == 50.0


def test_send_creates_change_and_updates_balances(funded):
    node, alice = funded
    bob = generate_wallet()
    tx = node.create_transaction(alice, bob.address, 10, fee=0.5)
    assert len(tx.vin) == 1 and len(tx.vout) == 2     # вход + (получатель, сдача)
    assert node.add_transaction(tx)
    node.mine_pending(bob.address)
    assert node.get_balance(alice.address) == 39.5
    assert node.get_balance(bob.address) == 60.5
    assert node.is_valid()


def test_insufficient_funds_returns_none(funded):
    node, alice = funded
    bob = generate_wallet()
    assert node.create_transaction(alice, bob.address, 1000) is None


def test_double_spend_rejected(funded):
    node, alice = funded
    bob = generate_wallet()
    op = _alice_outpoint(node, alice)
    assert node.add_transaction(node.create_transaction(alice, bob.address, 10))
    dbl = Transaction(vin=[TxInput(*op)], vout=[TxOutput(5, bob.address)]).sign(alice)
    assert not node.add_transaction(dbl)


def test_foreign_key_cannot_spend(funded):
    node, alice = funded
    eve = generate_wallet()
    op = _alice_outpoint(node, alice)
    forged = Transaction(vin=[TxInput(*op)], vout=[TxOutput(40, eve.address)]).sign(eve)
    assert not node.validate_transaction(forged)


def test_tampered_amount_rejected(funded):
    node, alice = funded
    bob = generate_wallet()
    tx = node.create_transaction(alice, bob.address, 1)
    tx.vout[0].amount = 49          # меняем сумму после подписи
    assert not node.validate_transaction(tx)


def test_overspend_rejected(funded):
    node, alice = funded
    bob = generate_wallet()
    op = _alice_outpoint(node, alice)
    over = Transaction(vin=[TxInput(*op)], vout=[TxOutput(999, bob.address)]).sign(alice)
    assert not node.validate_transaction(over)


def test_persistence_roundtrip(tmp_path, funded):
    node, alice = funded
    path = tmp_path / "chain.json"
    node.save(str(path))
    loaded = BHydraNode.load(str(path))
    assert loaded.get_balance(alice.address) == node.get_balance(alice.address)
    assert loaded.is_valid()
