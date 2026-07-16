"""Тесты смарт-контрактов: учебные классы + эскроу и смарт-чеки на цепочке."""

import time

import pytest

from b_hydra.contract import (
    ContractManager, SmartCheck, SmartContract, verify_cheque,
)
from b_hydra.node import BHydraNode
from b_hydra.wallet import generate_wallet


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


# ---------------------------------------------------------------------------
# Контракты на цепочке: средства реально блокируются и выплачиваются UTXO.
# ---------------------------------------------------------------------------

@pytest.fixture
def chain():
    """Узел с профинансированным покупателем (50 BHY) и менеджером контрактов."""
    node = BHydraNode(difficulty=1)
    buyer, seller = generate_wallet(), generate_wallet()
    node.mine_pending(buyer.address)          # награда 50 BHY
    return node, ContractManager(node), buyer, seller


def _settle(node):
    """Подтверждает мемпул блоком стороннего майнера."""
    node.mine_pending(generate_wallet().address)


# --- Эскроу ----------------------------------------------------------------

def test_escrow_onchain_happy_path(chain):
    node, mgr, buyer, seller = chain
    escrow = mgr.open_escrow(buyer, seller.address, 10, fee=0.5)
    assert escrow["status"] == "open"

    mgr.confirm_escrow(escrow["escrow_id"], buyer.address)
    assert escrow["status"] == "open"          # одного подтверждения мало
    mgr.confirm_escrow(escrow["escrow_id"], seller.address)
    assert escrow["status"] == "completed"
    assert escrow["payout_txid"]

    _settle(node)
    assert node.get_balance(seller.address) == pytest.approx(10)
    # Покупатель потратил amount + 2·fee (комиссии депозита и выплаты).
    assert node.get_balance(buyer.address) == pytest.approx(50 - 10 - 1.0)


def test_escrow_only_participants_confirm(chain):
    node, mgr, buyer, seller = chain
    escrow = mgr.open_escrow(buyer, seller.address, 5)
    with pytest.raises(ValueError):
        mgr.confirm_escrow(escrow["escrow_id"], generate_wallet().address)
    with pytest.raises(ValueError):
        mgr.confirm_escrow("нет такого", buyer.address)


def test_escrow_cancel_refunds_buyer(chain):
    node, mgr, buyer, seller = chain
    escrow = mgr.open_escrow(buyer, seller.address, 10)   # fee=0
    mgr.cancel_escrow(escrow["escrow_id"], seller.address)
    assert escrow["status"] == "cancelled"
    _settle(node)
    assert node.get_balance(buyer.address) == pytest.approx(50)
    # Закрытый эскроу больше не подтвердить и не отменить повторно.
    with pytest.raises(ValueError):
        mgr.confirm_escrow(escrow["escrow_id"], buyer.address)
    with pytest.raises(ValueError):
        mgr.cancel_escrow(escrow["escrow_id"], buyer.address)


def test_escrow_deadline_allows_anyone_to_cancel(chain):
    node, mgr, buyer, seller = chain
    escrow = mgr.open_escrow(buyer, seller.address, 10,
                             deadline=time.time() - 1)     # дедлайн прошёл
    stranger = generate_wallet().address
    mgr.cancel_escrow(escrow["escrow_id"], stranger)
    assert escrow["status"] == "cancelled"
    _settle(node)
    assert node.get_balance(buyer.address) == pytest.approx(50)


def test_escrow_stranger_cannot_cancel_before_deadline(chain):
    node, mgr, buyer, seller = chain
    escrow = mgr.open_escrow(buyer, seller.address, 10,
                             deadline=time.time() + 3600)
    with pytest.raises(ValueError):
        mgr.cancel_escrow(escrow["escrow_id"], generate_wallet().address)


def test_escrow_requires_funds_and_valid_seller(chain):
    node, mgr, buyer, seller = chain
    with pytest.raises(ValueError):
        mgr.open_escrow(buyer, "не адрес", 10)
    with pytest.raises(ValueError):
        mgr.open_escrow(buyer, seller.address, 10_000)     # нет средств
    poor = generate_wallet()
    with pytest.raises(ValueError):
        mgr.open_escrow(poor, seller.address, 1)


# --- Смарт-чек ---------------------------------------------------------------

def test_cheque_write_cash_and_offline_verify(chain):
    node, mgr, payer, _ = chain
    cheque, secret = mgr.write_cheque(payer, 7, fee=0.5)
    assert cheque["status"] == "active"
    assert secret not in str(cheque)           # секрет не хранится, только хеш
    assert verify_cheque(cheque)                # офлайн-проверка подписи
    fake = dict(cheque, amount=700.0)
    assert not verify_cheque(fake)              # подделка суммы ломает подпись

    holder = generate_wallet()
    mgr.cash_cheque(cheque["cheque_id"], secret, holder.address)
    assert cheque["status"] == "cashed"
    _settle(node)
    assert node.get_balance(holder.address) == pytest.approx(7)
    assert node.get_balance(payer.address) == pytest.approx(50 - 7 - 1.0)


def test_cheque_wrong_secret_and_double_cash(chain):
    node, mgr, payer, _ = chain
    cheque, secret = mgr.write_cheque(payer, 5)
    holder = generate_wallet().address
    with pytest.raises(ValueError):
        mgr.cash_cheque(cheque["cheque_id"], "не тот секрет", holder)
    mgr.cash_cheque(cheque["cheque_id"], secret, holder)
    with pytest.raises(ValueError):
        mgr.cash_cheque(cheque["cheque_id"], secret, holder)   # уже погашен


def test_named_cheque_only_for_recipient(chain):
    node, mgr, payer, seller = chain
    cheque, secret = mgr.write_cheque(payer, 5, recipient=seller.address)
    with pytest.raises(ValueError):
        mgr.cash_cheque(cheque["cheque_id"], secret,
                        generate_wallet().address)
    mgr.cash_cheque(cheque["cheque_id"], secret, seller.address)
    assert cheque["cashed_to"] == seller.address


def test_cheque_expiry_refund_rules(chain):
    node, mgr, payer, _ = chain
    cheque, secret = mgr.write_cheque(payer, 5, expires_in=0.05)
    with pytest.raises(ValueError):
        mgr.refund_cheque(cheque["cheque_id"], payer.address)  # ещё действует
    time.sleep(0.06)
    with pytest.raises(ValueError):
        mgr.cash_cheque(cheque["cheque_id"], secret,
                        generate_wallet().address)             # истёк
    with pytest.raises(ValueError):
        mgr.refund_cheque(cheque["cheque_id"], "чужой")        # не плательщик
    mgr.refund_cheque(cheque["cheque_id"], payer.address)
    assert cheque["status"] == "refunded"
    _settle(node)
    assert node.get_balance(payer.address) == pytest.approx(50)


def test_contract_manager_persistence_roundtrip(chain):
    node, mgr, buyer, seller = chain
    escrow = mgr.open_escrow(buyer, seller.address, 3)
    cheque, _ = mgr.write_cheque(buyer, 2)
    restored = ContractManager.from_dict(node, mgr.to_dict())
    assert restored.address == mgr.address     # тот же контрактный кошелёк
    assert escrow["escrow_id"] in restored.escrows
    assert cheque["cheque_id"] in restored.cheques
    # Восстановленный менеджер продолжает жизненный цикл записей.
    restored.confirm_escrow(escrow["escrow_id"], buyer.address)
    restored.confirm_escrow(escrow["escrow_id"], seller.address)
    assert restored.escrows[escrow["escrow_id"]]["status"] == "completed"
