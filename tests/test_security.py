"""Тесты безопасности сети: защита от фальшивых цепочек и консенсус по работе.

Проверяем, что узел НЕ принимает структурно валидную, но «жульническую»
цепочку/блок: с напечатанным из воздуха coinbase или с тратой чужих средств.
"""

from b_hydra.blockchain import Block
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
