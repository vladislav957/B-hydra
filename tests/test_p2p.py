"""Тесты P2P-синхронизации: общий генезис, рассылка блоков, sync, консенсус."""

import socket
import time

import pytest

from b_hydra.node import BHydraNode
from b_hydra.p2p import P2PNode
from b_hydra.wallet import generate_wallet


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def two_nodes():
    pa, pb = _free_port(), _free_port()
    a = P2PNode("127.0.0.1", pa, BHydraNode(difficulty=2))
    b = P2PNode("127.0.0.1", pb, BHydraNode(difficulty=2))
    a.start()
    b.start()
    time.sleep(0.2)
    a.add_peer("127.0.0.1", pb)
    b.add_peer("127.0.0.1", pa)
    yield a, b
    a.stop()
    b.stop()


def test_nodes_share_genesis(two_nodes):
    a, b = two_nodes
    assert a.node.blockchain.chain[0].hash == b.node.blockchain.chain[0].hash


def test_block_is_broadcast(two_nodes):
    a, b = two_nodes
    a.mine(generate_wallet().address)
    assert b.node.height == a.node.height
    assert b.node.blockchain.last_block.hash == a.node.blockchain.last_block.hash


def test_sync_catches_up_a_lagging_node(two_nodes):
    a, b = two_nodes
    miner = generate_wallet()
    for _ in range(3):                 # майним напрямую, без рассылки
        a.node.mine_pending(miner.address)
    assert b.node.height < a.node.height
    b.sync()
    assert b.node.height == a.node.height
    assert b.node.is_valid()


def test_transaction_propagates(two_nodes):
    a, b = two_nodes
    miner, bob = generate_wallet(), generate_wallet()
    a.mine(miner.address)              # рассылается → B тоже знает этот UTXO
    tx = a.node.create_transaction(miner, bob.address, 5, fee=0.1)
    assert a.submit_transaction(tx)
    assert any(t.txid == tx.txid for t in b.node.mempool.transactions)


def test_longest_valid_chain_wins(two_nodes):
    a, b = two_nodes
    a.node.mine_pending(generate_wallet().address)        # A: высота 2
    for _ in range(3):
        b.node.mine_pending(generate_wallet().address)    # B: высота 4
    a.sync()
    assert a.node.height == b.node.height == 4
    assert a.node.blockchain.last_block.hash == b.node.blockchain.last_block.hash


def test_shorter_chain_is_not_adopted(two_nodes):
    a, b = two_nodes
    for _ in range(3):
        a.node.mine_pending(generate_wallet().address)    # A: высота 4
    b.node.mine_pending(generate_wallet().address)        # B: высота 2
    a.sync()                                              # B короче — не принимаем
    assert a.node.height == 4


def _wait_until(cond, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return cond()


@pytest.fixture
def line_nodes():
    """Линейная топология A — B — C (A и C НЕ соседи)."""
    pa, pb, pc = _free_port(), _free_port(), _free_port()
    a = P2PNode("127.0.0.1", pa, BHydraNode(difficulty=1))
    b = P2PNode("127.0.0.1", pb, BHydraNode(difficulty=1))
    c = P2PNode("127.0.0.1", pc, BHydraNode(difficulty=1))
    for n in (a, b, c):
        n.start()
    time.sleep(0.2)
    a.add_peer("127.0.0.1", pb)
    b.add_peer("127.0.0.1", pa)
    b.add_peer("127.0.0.1", pc)
    c.add_peer("127.0.0.1", pb)
    yield a, b, c
    for n in (a, b, c):
        n.stop()


def test_block_propagates_multi_hop(line_nodes):
    a, b, c = line_nodes
    a.mine(generate_wallet().address)   # A не сосед C — блок дойдёт через B
    assert _wait_until(lambda: c.node.height == a.node.height)
    assert c.node.blockchain.last_block.hash == a.node.blockchain.last_block.hash


def test_transaction_propagates_multi_hop(line_nodes):
    a, b, c = line_nodes
    miner = generate_wallet()
    a.mine(miner.address)                                # фондируем + распространяем
    assert _wait_until(lambda: c.node.height == a.node.height)
    tx = a.node.create_transaction(miner, generate_wallet().address, 5)
    a.submit_transaction(tx)
    assert _wait_until(
        lambda: any(t.txid == tx.txid for t in c.node.mempool.transactions))
