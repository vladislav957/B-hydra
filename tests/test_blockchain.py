"""Тесты блокчейна: PoW, связность, обнаружение подделки, сериализация."""

from b_hydra.blockchain import Blockchain


def test_genesis_block_mined():
    bc = Blockchain(difficulty=2)
    assert len(bc.chain) == 1
    assert bc.chain[0].hash.startswith("00")


def test_blocks_are_linked():
    bc = Blockchain(difficulty=2)
    bc.add_block("hello")
    assert bc.chain[1].previous_hash == bc.chain[0].hash
    assert bc.is_chain_valid()


def test_proof_of_work_meets_difficulty():
    bc = Blockchain(difficulty=3)
    bc.add_block("x")
    assert bc.chain[1].hash.startswith("000")


def test_tampering_data_is_detected():
    bc = Blockchain(difficulty=2)
    bc.add_block("hello")
    bc.chain[1].data = "evil"           # подменяем данные блока
    assert not bc.is_chain_valid()


def test_from_dicts_roundtrip():
    bc = Blockchain(difficulty=2)
    bc.add_block("x")
    bc.add_block("y")
    restored = Blockchain.from_dicts(bc.to_dicts(), difficulty=2)
    assert restored.is_chain_valid()
    assert [b.hash for b in restored.chain] == [b.hash for b in bc.chain]


# --- Потолок размера блока: правило сети, а не настройка ---------------------
# Счётчика транзакций мало: транзакция с множеством входов/выходов может быть
# сколь угодно большой, поэтому «5000 штук» сами по себе размер НЕ ограничивают.
# Тесты ниже ЗАКРЕПЛЯЮТ величины: поменять их молча не выйдет.
import json

from b_hydra.blockchain import (
    Block, MAX_BLOCK_SIZE, MAX_BLOCK_TRANSACTIONS,
)
from b_hydra.node import BHydraNode
from b_hydra.tcp import MAX_MESSAGE_SIZE
from b_hydra.wallet import generate_wallet


def test_block_size_limit_is_pinned():
    """Величина потолка зафиксирована. Менять — только осознанно.

    Это правило консенсуса: узлы с разными потолками разойдутся в том, какой
    блок считать валидным, то есть разъедутся в разные сети.
    """
    assert MAX_BLOCK_SIZE == 4 * 1024 * 1024


def test_block_size_fits_the_transport():
    """Потолок блока связан с лимитом сообщения: ровно 8 блоков в сообщение.

    На этот тест ссылается комментарий у MAX_BLOCK_SIZE. Поднять потолок блока,
    не подняв лимит сообщения, значит сделать часть ВАЛИДНЫХ блоков
    непередаваемыми: узлы примут такой блок, но не смогут отдать его друг
    другу — и сеть встанет.
    """
    assert MAX_MESSAGE_SIZE == 8 * MAX_BLOCK_SIZE


def test_sync_batch_can_always_carry_a_full_block():
    """В пачку синхронизации обязан влезать хотя бы один полный блок."""
    from b_hydra.p2p import MAX_BATCH_BYTES
    assert MAX_BATCH_BYTES >= MAX_BLOCK_SIZE


def test_full_block_of_ordinary_transactions_fits():
    """Два лимита согласованы: 5000 обычных транзакций укладываются в потолок.

    Обычная транзакция ~795 байт, значит полный по счётчику блок ≈ 3,79 МиБ —
    впритык под 4 МиБ. Если разъехаться, один из лимитов станет мёртвым.
    """
    node = BHydraNode(difficulty=1)
    alice, bob = generate_wallet(), generate_wallet()
    node.mine_pending(alice.address)
    node.add_transaction(node.create_transaction(alice, bob.address, 1, fee=0.001))
    sample = node.mempool.transactions[0]
    per_tx = len(json.dumps(sample.to_dict(), ensure_ascii=False).encode("utf-8"))
    assert per_tx * (MAX_BLOCK_TRANSACTIONS - 1) < MAX_BLOCK_SIZE


def test_ordinary_block_is_far_below_the_limit():
    node = BHydraNode(difficulty=1)
    block = node.mine_pending(generate_wallet().address)
    assert 0 < block.size_bytes() < MAX_BLOCK_SIZE
    assert node.is_valid()


def test_oversized_block_makes_the_chain_invalid():
    node = BHydraNode(difficulty=1)
    reference = node.mine_pending(generate_wallet().address)
    fat = Block(1, node.blockchain.chain[0].hash,
                ["x" * (MAX_BLOCK_SIZE + 1000)],
                timestamp=reference.timestamp, target=reference.target)
    assert fat.size_bytes() > MAX_BLOCK_SIZE
    other = BHydraNode(difficulty=1)
    other.blockchain.chain.append(fat)
    assert other.blockchain.is_chain_valid() is False


def test_node_refuses_an_oversized_block_from_the_network():
    node = BHydraNode(difficulty=1)
    reference = node.mine_pending(generate_wallet().address)
    fat = Block(1, node.blockchain.chain[0].hash,
                ["x" * (MAX_BLOCK_SIZE + 1000)],
                timestamp=reference.timestamp, target=reference.target)
    assert BHydraNode(difficulty=1).receive_block(fat.to_dict()) is False


def test_miner_never_builds_an_oversized_block(monkeypatch):
    """Майнер обязан уложиться в потолок, а лишнее оставить в мемпуле.

    Иначе узел собрал бы блок, который сам же считает невалидным и не может
    ни передать, ни продолжить им цепочку.
    """
    from b_hydra import node as node_module

    node = BHydraNode(difficulty=1)
    alice, bob = generate_wallet(), generate_wallet()
    node.mine_pending(alice.address)
    for _ in range(8):
        node.add_transaction(node.create_transaction(alice, bob.address,
                                                     0.01, fee=0.001))
    assert len(node.mempool) == 8

    # Ужимаем потолок так, чтобы в блок влезла лишь пара транзакций.
    monkeypatch.setattr(node_module, "MAX_BLOCK_SIZE",
                        node_module._BLOCK_SIZE_RESERVE + 2000)
    block = node.mine_pending(alice.address)
    assert len(block.data) - 1 < 8          # часть транзакций не влезла
    assert len(node.mempool) > 0            # и осталась в мемпуле
