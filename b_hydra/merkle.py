"""
Merkle_python.py — дерево Меркла B-hydra на двойном SHA-512.

Корень Меркла фиксирует набор транзакций блока: изменение любой транзакции
меняет корень, а значит и хеш блока.
"""

import hashlib


def _sha512d(data: bytes) -> bytes:
    return hashlib.sha512(hashlib.sha512(data).digest()).digest()


def merkle_root(leaves) -> str:
    """
    Корень дерева Меркла из списка листьев.

    Листья могут быть bytes (готовые хеши) или строки (будут захешированы).
    Возвращает hex-строку.
    """
    if not leaves:
        return _sha512d(b"").hex()

    layer = []
    for leaf in leaves:
        if isinstance(leaf, bytes):
            layer.append(leaf)
        else:
            layer.append(_sha512d(str(leaf).encode("utf-8")))

    while len(layer) > 1:
        if len(layer) % 2 == 1:
            layer.append(layer[-1])  # дублируем последний при нечётном числе
        layer = [_sha512d(layer[i] + layer[i + 1]) for i in range(0, len(layer), 2)]

    return layer[0].hex()


class MerkleTree:
    """Дерево Меркла с возможностью получить корень по списку данных."""

    def __init__(self, data_blocks=None):
        self.leaves = []
        if data_blocks:
            for block in data_blocks:
                self.add(block)

    def add(self, data):
        """Добавляет лист (данные хешируются двойным SHA-512)."""
        if isinstance(data, bytes):
            self.leaves.append(_sha512d(data))
        else:
            self.leaves.append(_sha512d(str(data).encode("utf-8")))

    @property
    def root(self) -> str:
        return merkle_root(self.leaves)


if __name__ == "__main__":
    tree = MerkleTree(["a", "b", "c", "d", "e"])
    print("Корень дерева Меркла:", tree.root[:32], "…")
