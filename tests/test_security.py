"""Тесты безопасности сети: защита от фальшивых цепочек и консенсус по работе.

Проверяем, что узел НЕ принимает структурно валидную, но «жульническую»
цепочку/блок: с напечатанным из воздуха coinbase или с тратой чужих средств.
"""

import struct
import time

from b_hydra import tcp
from b_hydra.blockchain import Block, MAX_BLOCK_TRANSACTIONS
from b_hydra.node import BHydraNode
from b_hydra.transaction import Transaction, TxInput, TxOutput, coinbase
from b_hydra.wallet import generate_wallet


def _mine(blockchain, data):
    """Создаёт и майнит блок поверх цепочки (для конструирования атак)."""
    height = len(blockchain.chain)
    block = Block(index=height, previous_hash=blockchain.last_block.hash,
                  data=data, difficulty=blockchain.expected_difficulty(height))
    block.mine_block()
    blockchain.chain.append(block)
    return block


def test_total_work_sums_difficulty():
    bc = BHydraNode(difficulty=2).blockchain
    assert bc.total_work == 16 ** 2          # только генезис, сложность 2


def test_receive_block_accepts_honest_block():
    a, b = BHydraNode(difficulty=2), BHydraNode(difficulty=2)
    block = a.mine_pending(generate_wallet().address)
    assert b.receive_block(block.to_dict()) is True
    assert b.height == a.height


def test_receive_block_rejects_inflated_coinbase():
    node = BHydraNode(difficulty=2)
    fake = coinbase(generate_wallet().address, 999, height=1)   # награда 50, а тут 999
    block = Block(index=1, previous_hash=node.blockchain.last_block.hash,
                  data=[fake.to_dict()],
                  difficulty=node.blockchain.expected_difficulty(1))
    block.mine_block()
    assert node.receive_block(block.to_dict()) is False


def test_replace_chain_rejects_inflated_coinbase():
    honest = BHydraNode(difficulty=2)
    attacker = BHydraNode(difficulty=2)               # тот же генезис
    fake = coinbase(generate_wallet().address, 1_000_000, height=1)
    _mine(attacker.blockchain, [fake.to_dict()])

    assert attacker.blockchain.is_chain_valid()        # структурно валидна
    assert attacker.blockchain.total_work > honest.blockchain.total_work
    # …но печать монет в coinbase отвергается полной проверкой:
    assert honest.replace_chain(attacker.blockchain.to_dicts()) is False
    assert honest.height == 1


def test_replace_chain_rejects_forged_spend():
    honest = BHydraNode(difficulty=2)
    attacker = BHydraNode(difficulty=2)
    victim, thief = generate_wallet(), generate_wallet()

    # Блок 1: честный coinbase жертве.
    cb1 = coinbase(victim.address, 50, height=1)
    _mine(attacker.blockchain, [cb1.to_dict()])

    # Блок 2: вор тратит UTXO жертвы СВОЕЙ подписью (чужой ключ).
    steal = Transaction(vin=[TxInput(cb1.txid, 0)],
                        vout=[TxOutput(50, thief.address)])
    steal.sign(thief)
    cb2 = coinbase(thief.address, 50, height=2)
    _mine(attacker.blockchain, [cb2.to_dict(), steal.to_dict()])

    assert attacker.blockchain.is_chain_valid()        # структурно валидна
    assert honest.replace_chain(attacker.blockchain.to_dicts()) is False


def test_replace_chain_accepts_honest_longer_chain():
    honest = BHydraNode(difficulty=2)
    other = BHydraNode(difficulty=2)
    for _ in range(3):
        other.mine_pending(generate_wallet().address)  # честные блоки
    assert honest.replace_chain(other.blockchain.to_dicts()) is True
    assert honest.height == other.height
    assert honest.is_valid()


def test_receive_block_rejects_future_timestamp():
    node = BHydraNode(difficulty=1)
    cb = coinbase(generate_wallet().address, 50, height=1)
    block = Block(index=1, previous_hash=node.blockchain.last_block.hash,
                  data=[cb.to_dict()], timestamp=time.time() + 10 * 3600,
                  difficulty=node.blockchain.expected_difficulty(1))
    block.mine_block()
    assert node.receive_block(block.to_dict()) is False     # из будущего


def test_replace_chain_rejects_time_travel():
    honest = BHydraNode(difficulty=1)
    attacker = BHydraNode(difficulty=1)
    now = time.time()
    cb1 = coinbase(generate_wallet().address, 50, height=1)
    b1 = Block(1, attacker.blockchain.last_block.hash, [cb1.to_dict()],
               timestamp=now, difficulty=attacker.blockchain.expected_difficulty(1))
    b1.mine_block()
    attacker.blockchain.chain.append(b1)
    cb2 = coinbase(generate_wallet().address, 50, height=2)
    b2 = Block(2, b1.hash, [cb2.to_dict()], timestamp=now - 3600,  # время назад
               difficulty=attacker.blockchain.expected_difficulty(2))
    b2.mine_block()
    attacker.blockchain.chain.append(b2)
    assert attacker.blockchain.is_chain_valid() is False
    assert honest.replace_chain(attacker.blockchain.to_dicts()) is False


def test_block_rejects_duplicate_transaction():
    node = BHydraNode(difficulty=1)
    payer = generate_wallet()
    node.mine_pending(payer.address)                       # у payer есть 50 BHY
    spend = node.create_transaction(payer, generate_wallet().address, 5)
    cb = coinbase(generate_wallet().address, 50, height=node.height)
    data = [cb.to_dict(), spend.to_dict(), spend.to_dict()]   # дубль транзакции
    block = Block(node.height, node.blockchain.last_block.hash, data,
                  difficulty=node.blockchain.expected_difficulty(node.height))
    block.mine_block()
    assert node.receive_block(block.to_dict()) is False


def test_block_rejects_too_many_transactions():
    node = BHydraNode(difficulty=1)

    class _FakeBlock:
        data = [{"vin": [{"txid": "0" * 128, "index": 0}],
                 "vout": [{"amount": 1, "address": "x"}],
                 "timestamp": 0}] * (MAX_BLOCK_TRANSACTIONS + 1)

    assert node._validate_block_transactions(_FakeBlock(), 1, {}) is False


def test_tcp_rejects_oversize_message():
    class _FakeSock:
        def __init__(self, data):
            self.buf = data

        def recv(self, n):
            chunk = self.buf[:n]
            self.buf = self.buf[n:]
            return chunk

    header = struct.pack(">I", tcp.MAX_MESSAGE_SIZE + 1)
    assert tcp.recv_message(_FakeSock(header)) == b""
