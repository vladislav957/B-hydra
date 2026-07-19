"""Тесты обозревателя адресов: node.address_stats() (rich list)."""

import pytest

from b_hydra.node import BHydraNode
from b_hydra.wallet import generate_wallet


def test_address_stats_rich_list():
    node = BHydraNode(difficulty=1)
    alice, bob = generate_wallet(), generate_wallet()
    node.mine_pending(alice.address)                    # Алиса: +50 (coinbase)
    tx = node.create_transaction(alice, bob.address, 10, fee=0.5)
    assert node.add_transaction(tx)
    node.mine_pending(bob.address)                      # Боб: +10 и +50.5 (награда+fee)

    stats = node.address_stats()
    by = {s["address"]: s for s in stats}

    a, b = by[alice.address], by[bob.address]
    assert a["balance"] == pytest.approx(39.5)          # 50 − 10 − 0.5
    assert a["sent"] == pytest.approx(50)               # потратила весь UTXO
    assert a["received"] == pytest.approx(50 + 39.5)    # награда + сдача
    assert a["tx_count"] == 2                           # coinbase + перевод
    assert a["first_block"] == 1 and a["last_block"] == 2
    assert b["balance"] == pytest.approx(60.5)          # 10 + 50 + 0.5

    # Rich list: сортировка по балансу, вершина — Боб.
    assert stats[0]["address"] == bob.address
    assert all(stats[i]["balance"] >= stats[i + 1]["balance"]
               for i in range(len(stats) - 1))
    # Балансы сходятся с каноническим get_balance.
    for s in stats:
        assert s["balance"] == pytest.approx(node.get_balance(s["address"]))
    # limit обрезает вершину списка.
    assert node.address_stats(limit=1) == stats[:1]


def test_address_stats_empty_chain():
    assert BHydraNode(difficulty=1).address_stats() == []
