"""
merkle.py — B-hydra's Merkle tree over double SHA-512.

The Merkle root pins down the block's set of transactions: change any
transaction and the root changes, and with it the block hash. Besides the
root, the module can build **inclusion proofs** (audit path / Merkle proof):
with one, a light client (SPV) verifies that a transaction belongs to a block
knowing only the root from the header — without downloading every
transaction.

A single source of truth: the `merkle_root()` here is used both by
`blockchain.py` (the block header) and by the explorer — the implementation is
not duplicated.

Security model:
  * **CVE-2012-2459** (forgery via duplication of an odd node): with an odd
    number of nodes the last one is duplicated — as in Bitcoin. That creates
    a theoretical ambiguity in the root, so the node SEPARATELY forbids
    repeated txids inside a block (`_validate_block_transactions`), which is
    what closes the attack. `has_duplicate_promotion()` flags such trees.
  * **Second-preimage** (passing an internal node off as a leaf): leaves are
    the double SHA-512 of the transaction JSON, internal nodes are the double
    SHA-512 of 128 bytes (two hashes). Finding a transaction whose
    serialization equals the concatenation of two hashes is computationally
    out of reach; on top of that the node re-parses every leaf as a
    transaction — a 128-byte concatenation will not pass as a valid tx.
"""

from __future__ import annotations

if __name__ == "__main__" and __package__ in (None, ""):
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    __package__ = "b_hydra"

from . import hashing


def _sha512d(data: bytes) -> bytes:
    return hashing.double_sha512(data)


def leaf_hash(data) -> bytes:
    """Leaf hash: double SHA-512 of the data (bytes) or of its str form."""
    if isinstance(data, bytes):
        return _sha512d(data)
    return _sha512d(str(data).encode("utf-8"))


def _as_leaves(items) -> list:
    """Normalises the input to a list of leaf hashes (bytes in = already hashes)."""
    return [item if isinstance(item, bytes) else leaf_hash(item)
            for item in items]


def _build_layers(leaves: list) -> list:
    """Builds every layer bottom-up (lowest = leaves, topmost = the root).

    An odd layer is padded with a copy of its last node — that is what makes
    the root match the classic Bitcoin scheme and B-hydra's previous
    implementation byte for byte. Returns a list of layers; each layer is a
    list of bytes.
    """
    if not leaves:
        return [[_sha512d(b"")]]
    layers = [list(leaves)]
    while len(layers[-1]) > 1:
        cur = layers[-1]
        if len(cur) % 2 == 1:
            cur = cur + [cur[-1]]        # duplicate the last one when the count is odd
            layers[-1] = cur             # keep the padded layer (needed for the proof)
        layers.append([_sha512d(cur[i] + cur[i + 1])
                       for i in range(0, len(cur), 2)])
    return layers


def merkle_root(leaves) -> str:
    """Merkle root from a list of leaves (byte hashes or strings) -> hex."""
    return _build_layers(_as_leaves(leaves))[-1][0].hex()


def merkle_proof(leaves, index: int) -> list:
    """Inclusion proof for leaf number `index`: the path from leaf to root.

    Returns a list of steps `{"hash": <sibling hex>, "position":
    "left"|"right"}`, where position says which side the sibling sits on when
    concatenated. Checked by `verify_proof()` without access to all the
    leaves (SPV).
    """
    leaves = _as_leaves(leaves)
    if not leaves:
        raise IndexError("пустой набор листьев")
    if not 0 <= index < len(leaves):
        raise IndexError(f"индекс {index} вне диапазона 0..{len(leaves) - 1}")

    layers = _build_layers(leaves)
    proof = []
    idx = index
    for layer in layers[:-1]:            # every layer except the root one
        sibling = idx ^ 1               # sibling: even <-> the next, odd <-> the previous
        position = "right" if idx % 2 == 0 else "left"
        proof.append({"hash": layer[sibling].hex(), "position": position})
        idx //= 2
    return proof


def verify_proof(leaf, proof, root: str) -> bool:
    """Verifies an inclusion proof: leaf + path yield the claimed root.

    leaf — the leaf's byte hash or the original data (which will be hashed);
    root — the hex root string from the block header. Returns True/False.
    """
    try:
        h = leaf if isinstance(leaf, bytes) else leaf_hash(leaf)
        for step in proof:
            sib = bytes.fromhex(step["hash"])
            h = _sha512d(sib + h) if step["position"] == "left" else _sha512d(h + sib)
        return h.hex() == root
    except (KeyError, TypeError, ValueError):
        return False


class MerkleTree:
    """A Merkle tree with a root and inclusion proofs."""

    def __init__(self, data_blocks=None):
        self.leaves: list = []
        for block in data_blocks or []:
            self.add(block)

    def add(self, data) -> None:
        """Adds a leaf (the data is hashed with double SHA-512)."""
        self.leaves.append(leaf_hash(data))

    @classmethod
    def from_hashes(cls, hashes) -> "MerkleTree":
        """Builds the tree from ready-made leaf hashes (bytes)."""
        tree = cls()
        tree.leaves = [h if isinstance(h, bytes) else bytes.fromhex(h)
                       for h in hashes]
        return tree

    @property
    def root(self) -> str:
        return merkle_root(self.leaves)

    def proof(self, index: int) -> list:
        """Inclusion proof for leaf number `index`."""
        return merkle_proof(self.leaves, index)

    def prove_data(self, data):
        """Finds a leaf by its data and returns (index, proof) or (None, None)."""
        target = leaf_hash(data)
        for i, leaf in enumerate(self.leaves):
            if leaf == target:
                return i, merkle_proof(self.leaves, i)
        return None, None

    def has_duplicate_promotion(self) -> bool:
        """True if an odd last node was duplicated anywhere
        (an indicator of potential CVE-2012-2459 ambiguity)."""
        n = len(self.leaves)
        while n > 1:
            if n % 2 == 1:
                return True
            n //= 2
        return False


if __name__ == "__main__":
    tree = MerkleTree(["a", "b", "c", "d", "e"])
    root = tree.root
    print("Корень дерева Меркла:", root[:32], "…")
    idx, path = tree.prove_data("c")
    print(f"Доказательство для «c» (лист №{idx}): {len(path)} шагов")
    print("Проверка:", verify_proof("c", path, root))
