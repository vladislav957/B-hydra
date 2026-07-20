"""Профессиональное дерево Меркла: корень, доказательства включения (SPV)."""

import pytest

from b_hydra import merkle
from b_hydra.blockchain import merkle_root as bc_merkle_root, sha512d
from b_hydra.merkle import MerkleTree, merkle_proof, merkle_root, verify_proof
from b_hydra.node import BHydraNode
from b_hydra.wallet import generate_wallet


def test_root_byte_compatible_with_blockchain():
    """Единый корень: merkle.merkle_root == прежний blockchain.merkle_root."""
    for n in range(0, 17):
        leaves = [sha512d(str(i).encode()) for i in range(n)]
        assert merkle_root(leaves) == bc_merkle_root(list(leaves))


def test_root_sensitive_to_data():
    assert merkle_root(["a", "b", "c"]) != merkle_root(["a", "b", "d"])
    assert len(merkle_root(["x"])) == 128            # hex двойного SHA-512


def test_proof_roundtrip_all_indices():
    for n in range(1, 20):
        tree = MerkleTree([f"tx{i}" for i in range(n)])
        root = tree.root
        for i in range(n):
            proof = tree.proof(i)
            assert verify_proof(f"tx{i}", proof, root)
            assert not verify_proof("подделка", proof, root)


def test_proof_rejects_tampering():
    tree = MerkleTree(["a", "b", "c", "d"])
    root, proof = tree.root, tree.proof(1)
    assert verify_proof("b", proof, root)
    # переворот стороны склейки ломает путь
    bad = [dict(s) for s in proof]
    bad[0]["position"] = "left" if bad[0]["position"] == "right" else "right"
    assert not verify_proof("b", bad, root)
    # чужой корень не принимается
    assert not verify_proof("b", proof, merkle_root(["z"]))


def test_prove_data_finds_leaf():
    tree = MerkleTree(["alice", "bob", "carol"])
    idx, proof = tree.prove_data("bob")
    assert idx == 1
    assert verify_proof("bob", proof, tree.root)
    assert tree.prove_data("нет") == (None, None)


def test_out_of_range_and_empty():
    assert merkle_root([]) == sha512d(b"").hex()     # корень пустого набора
    with pytest.raises(IndexError):
        merkle_proof([], 0)
    with pytest.raises(IndexError):
        MerkleTree(["a", "b"]).proof(5)


def test_duplicate_promotion_flag():
    assert MerkleTree(list("abcd")).has_duplicate_promotion() is False
    assert MerkleTree(list("abc")).has_duplicate_promotion() is True
    assert MerkleTree(list("ab")).has_duplicate_promotion() is False


def test_block_and_node_merkle_proof():
    """SPV через узел: путь до merkle_root заголовка проверяется без блока."""
    node = BHydraNode(difficulty=1)
    alice, bob = generate_wallet(), generate_wallet()
    node.mine_pending(alice.address)
    tx = node.create_transaction(alice, bob.address, 10, fee=0.5)
    node.add_transaction(tx)
    node.mine_pending(bob.address)               # блок #2: coinbase + перевод

    got = node.merkle_proof(tx.txid)
    assert got is not None
    assert got["block_index"] == 2
    # Клиент проверяет включение по листу и пути против корня из заголовка.
    leaf = bytes.fromhex(got["leaf"])
    assert verify_proof(leaf, got["proof"], got["merkle_root"])
    # Корень в доказательстве совпадает с реальным заголовком блока.
    assert got["merkle_root"] == node.blockchain.chain[2].merkle_root
    # Неизвестная транзакция — нет доказательства.
    assert node.merkle_proof("00" * 64) is None
