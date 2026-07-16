"""Тесты смарт-контракта и эскроу."""

from b_hydra.contract import SmartContract
from b_hydra.contract import SmartCheck


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


# --- Смарт-чек (HTLC: подпись + хеш-замок + время-замок) --------------------
from b_hydra.contract import SmartCheque, honor_cheque
from b_hydra.node import BHydraNode
from b_hydra.wallet import generate_wallet


def _funded_node():
    node = BHydraNode(difficulty=2)
    node.blockchain.retarget_interval = 4
    payer = generate_wallet()
    node.mine_pending(payer.address)          # 50 BHY плательщику
    return node, payer


def test_cheque_is_signed_and_verifies():
    node, payer = _funded_node()
    ch = SmartCheque.issue(payer, generate_wallet().address, 10, "s3cret",
                           expiry=node.height + 50, fee=0.0001)
    assert ch.verify() is True


def test_cheque_rejects_tampering():
    node, payer = _funded_node()
    ch = SmartCheque.issue(payer, generate_wallet().address, 10, "s3cret",
                           expiry=node.height + 50)
    ch.amount = 1000                          # подмена суммы ломает подпись
    assert ch.verify() is False


def test_cheque_hash_lock():
    node, payer = _funded_node()
    ch = SmartCheque.issue(payer, generate_wallet().address, 5, "opensesame",
                           expiry=node.height + 50)
    assert ch.can_redeem("opensesame", node.height) is True
    assert ch.can_redeem("wrong-secret", node.height) is False


def test_cheque_time_lock_and_refund():
    node, payer = _funded_node()
    ch = SmartCheque.issue(payer, generate_wallet().address, 5, "k",
                           expiry=node.height)         # истекает сразу
    assert ch.can_redeem("k", node.height + 1) is False   # срок вышел
    assert ch.can_refund(node.height + 1) is True
    assert ch.can_refund(node.height) is False            # ещё не вышел


def test_honor_cheque_moves_real_coins():
    node, payer = _funded_node()
    payee = generate_wallet()
    ch = SmartCheque.issue(payer, payee.address, 12, "sec",
                           expiry=node.height + 50, fee=0.0001)
    tx = honor_cheque(ch, "sec", payer, node)
    assert tx is not None
    node.mine_pending(payer.address)
    assert node.get_balance(payee.address) == 12
    # чужим секретом оплатить нельзя
    assert honor_cheque(ch, "nope", payer, node) is None


def test_cheque_roundtrip_dict():
    node, payer = _funded_node()
    ch = SmartCheque.issue(payer, generate_wallet().address, 7, "x",
                           expiry=node.height + 9, fee=0.0002)
    back = SmartCheque.from_dict(ch.to_dict())
    assert back.verify() and back.amount == 7 and back.secret_matches("x")
