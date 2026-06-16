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
