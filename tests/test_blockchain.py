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

import pytest

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


# --- Заметка майнера в блоке (coinbase-послание) ----------------------------
# Как scriptSig в Bitcoin, где в генезисе лежит «The Times 03/Jan/2009…»:
# произвольный текст, который нашедший блок оставляет в нём навсегда.
from b_hydra.blockchain import MAX_COINBASE_MESSAGE
from b_hydra.transaction import coinbase


def test_miner_can_leave_a_message():
    node = BHydraNode(difficulty=1)
    block = node.mine_pending(generate_wallet().address, message="i-3ru")
    assert node.block_message(block.index) == "i-3ru"
    assert node.is_valid()


def test_message_defaults_when_not_given():
    node = BHydraNode(difficulty=1)
    block = node.mine_pending(generate_wallet().address)
    assert node.block_message(block.index) == "B-hydra"


def test_message_is_protected_by_proof_of_work():
    """Заметка входит в дерево Меркла, значит защищена PoW.

    Переписать её задним числом нельзя — изменится корень Меркла, а с ним и
    хеш блока, то есть придётся перемайнить блок заново.
    """
    reference = Block(1, "0", [coinbase("BHYx", 50, height=1,
                                        message="первая").to_dict()],
                      timestamp=1.0)
    altered = Block(1, "0", [coinbase("BHYx", 50, height=1,
                                      message="вторая").to_dict()],
                    timestamp=reference.data[0]["timestamp"])
    altered.data[0]["timestamp"] = reference.data[0]["timestamp"]
    altered.merkle_root = altered._calculate_merkle_root()
    altered.hash = altered.calculate_hash()
    assert altered.merkle_root != reference.merkle_root
    assert altered.hash != reference.hash


def test_message_length_is_capped():
    node = BHydraNode(difficulty=1)
    with pytest.raises(ValueError):
        node.mine_pending(generate_wallet().address,
                          message="я" * (MAX_COINBASE_MESSAGE + 1))


def test_node_rejects_a_block_with_an_oversized_message():
    """Чужой блок с раздутой заметкой невалиден — это правило сети.

    Иначе блок можно было бы набивать произвольными данными, которые каждый
    узел обязан хранить вечно.
    """
    node = BHydraNode(difficulty=1)
    reference = node.mine_pending(generate_wallet().address)
    fat = coinbase(generate_wallet().address, 50, height=1,
                   message="x" * (MAX_COINBASE_MESSAGE + 1))
    block = Block(1, node.blockchain.chain[0].hash, [fat.to_dict()],
                  timestamp=reference.timestamp, target=reference.target)
    block.mine_block()
    assert BHydraNode(difficulty=1).receive_block(block.to_dict()) is False


def test_message_exactly_at_the_limit_is_accepted():
    node = BHydraNode(difficulty=1)
    reference = node.mine_pending(generate_wallet().address)
    edge = coinbase(generate_wallet().address, 50, height=1,
                    message="x" * MAX_COINBASE_MESSAGE)
    block = Block(1, node.blockchain.chain[0].hash, [edge.to_dict()],
                  timestamp=reference.timestamp, target=reference.target)
    block.mine_block()
    assert BHydraNode(difficulty=1).receive_block(block.to_dict()) is True


def test_unicode_message_is_measured_in_bytes():
    """Лимит считается в БАЙТАХ UTF-8, а не в символах.

    Кириллица занимает по два байта, поэтому 60 букв — это уже 120 байт.
    """
    node = BHydraNode(difficulty=1)
    assert len(("я" * 60).encode("utf-8")) > MAX_COINBASE_MESSAGE
    with pytest.raises(ValueError):
        node.mine_pending(generate_wallet().address, message="я" * 60)
    block = node.mine_pending(generate_wallet().address, message="я" * 40)
    assert node.block_message(block.index) == "я" * 40


# --- Подпись заметки майнера ------------------------------------------------
# PoW доказывает, что блок ДОБЫТ, но не то, КТО написал заметку: текст в
# coinbase свободный, и любой майнер может подписаться чужим именем. Поэтому
# заметку можно подписать ключом майнера — тогда авторство проверяемо.
from b_hydra.transaction import coinbase_message_author, message_payload


def _signed_block(node, wallet, message, height=1, miner=None):
    """Блок с подписанной заметкой, добытый «вручную» (для проверок чужим узлом)."""
    reference = node.blockchain.chain[height - 1]
    tx = coinbase(miner or wallet.address, 50, height=height,
                  message=message, wallet=wallet)
    block = Block(height, reference.hash, [tx.to_dict()],
                  timestamp=reference.timestamp + 1,
                  target=node.blockchain.expected_target(height))
    block.mine_block()
    return block


def test_miner_signs_his_message():
    node = BHydraNode(difficulty=1)
    miner = generate_wallet()
    block = node.mine_pending(miner.address, message="i-3ru", wallet=miner)
    assert node.block_message(block.index) == "i-3ru"
    assert node.block_message_author(block.index) == miner.address
    assert node.is_valid()


def test_unsigned_message_has_no_author():
    """Подпись необязательна: анонимная заметка валидна, как в Bitcoin."""
    node = BHydraNode(difficulty=1)
    block = node.mine_pending(generate_wallet().address, message="аноним")
    assert node.block_message(block.index) == "аноним"
    assert node.block_message_author(block.index) is None


def test_signed_message_travels_over_the_network():
    node = BHydraNode(difficulty=1)
    miner = generate_wallet()
    block = _signed_block(node, miner, "i-3ru")
    peer = BHydraNode(difficulty=1)
    assert peer.receive_block(block.to_dict()) is True
    assert peer.block_message_author(1) == miner.address


def test_rewriting_a_signed_message_invalidates_the_block():
    """Текст сменили, подпись оставили — блок отвергается всей сетью."""
    node = BHydraNode(difficulty=1)
    miner = generate_wallet()
    block = _signed_block(node, miner, "i-3ru")
    block.data[0]["vin"][0]["public_key"] = "это писал не я"
    block.merkle_root = block._calculate_merkle_root()
    block.mine_block()
    assert BHydraNode(difficulty=1).receive_block(block.to_dict()) is False


def test_stolen_signature_does_not_pass():
    """Чужую подпись нельзя приложить к своему блоку.

    В payload входит адрес получателя награды, поэтому подпись «прилипает» к
    майнеру: переставить её на другой адрес — значит сломать проверку.
    """
    node = BHydraNode(difficulty=1)
    miner, thief = generate_wallet(), generate_wallet()
    block = _signed_block(node, miner, "i-3ru")
    block.data[0]["vout"][0]["address"] = thief.address
    block.merkle_root = block._calculate_merkle_root()
    block.mine_block()
    assert BHydraNode(difficulty=1).receive_block(block.to_dict()) is False


def test_signing_with_someone_elses_key_does_not_pass():
    """Ключ обязан принадлежать получателю награды, а не быть «каким-нибудь»."""
    node = BHydraNode(difficulty=1)
    miner, stranger = generate_wallet(), generate_wallet()
    block = _signed_block(node, stranger, "i-3ru", miner=miner.address)
    assert BHydraNode(difficulty=1).receive_block(block.to_dict()) is False


def test_signature_is_bound_to_the_height():
    """Подписанную заметку нельзя перенести в другой блок той же сети."""
    miner = generate_wallet()
    signed = coinbase(miner.address, 50, height=1, message="i-3ru",
                      wallet=miner).to_dict()
    assert coinbase_message_author(signed, 1) == miner.address
    assert coinbase_message_author(signed, 2) is None


def test_broken_signature_is_rejected_not_ignored():
    """Мусор вместо подписи — блок невалиден, а не «просто без автора».

    Иначе поле подписи стало бы ещё одним куском свободного текста, и по нему
    нельзя было бы судить об авторстве вообще.
    """
    node = BHydraNode(difficulty=1)
    miner = generate_wallet()
    block = _signed_block(node, miner, "i-3ru")
    block.data[0]["vin"][0]["signature"] = "00" * 64
    block.merkle_root = block._calculate_merkle_root()
    block.mine_block()
    assert BHydraNode(difficulty=1).receive_block(block.to_dict()) is False


def test_mining_with_a_foreign_key_is_refused():
    node = BHydraNode(difficulty=1)
    with pytest.raises(ValueError):
        node.mine_pending(generate_wallet().address, message="i-3ru",
                          wallet=generate_wallet())


def test_unsigned_coinbase_serialisation_is_unchanged():
    """Обычная (неподписанная) coinbase сериализуется как раньше.

    Новое поле miner_key появляется только у подписанной — иначе изменился бы
    лист дерева Меркла у всех старых блоков.
    """
    plain = coinbase("BHYx", 50, height=1, message="i-3ru").to_dict()
    assert "miner_key" not in plain["vin"][0]
    assert plain["vin"][0]["signature"] is None
    signed = coinbase("BHYx", 50, height=1, message="i-3ru",
                      wallet=generate_wallet()).to_dict()
    assert "miner_key" in signed["vin"][0]


def test_signature_does_not_change_the_txid():
    """Подпись заметки не влияет на txid coinbase — как и сама заметка.

    signing_payload берёт из входа только txid и index, поэтому награда
    идентифицируется одинаково, подписался майнер или нет.
    """
    miner = generate_wallet()
    plain = coinbase(miner.address, 50, height=1, message="i-3ru")
    signed = coinbase(miner.address, 50, height=1, message="i-3ru",
                      wallet=miner)
    signed.timestamp = plain.timestamp
    assert signed.txid == plain.txid


def test_message_payload_binds_network_height_and_miner():
    """В подписываемых байтах есть сеть, высота, майнер и текст."""
    import json
    from b_hydra.blockchain import CHAIN_ID
    payload = json.loads(message_payload("i-3ru", "BHYx", 7).decode("utf-8"))
    assert payload == {"chain_id": CHAIN_ID, "height": 7,
                       "miner": "BHYx", "message": "i-3ru"}
