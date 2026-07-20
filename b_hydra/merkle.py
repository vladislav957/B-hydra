"""
merkle.py — дерево Меркла B-hydra на двойном SHA-512.

Корень Меркла фиксирует набор транзакций блока: изменение любой транзакции
меняет корень, а значит и хеш блока. Кроме корня модуль умеет строить
**доказательства включения** (audit path / Merkle proof): по ним лёгкий
клиент (SPV) проверяет, что транзакция входит в блок, зная только корень из
заголовка — не скачивая все транзакции.

Единственный источник правды: `merkle_root()` отсюда использует и
`blockchain.py` (заголовок блока), и обозреватель — реализация не дублируется.

Модель безопасности:
  * **CVE-2012-2459** (подмена за счёт дублирования нечётного узла): при
    нечётном числе узлов последний дублируется — как в Bitcoin. Это создаёт
    теоретическую неоднозначность корня, поэтому узел ОТДЕЛЬНО запрещает
    повторяющиеся txid в блоке (`_validate_block_transactions`), что и
    закрывает атаку. `has_duplicate_promotion()` помечает такие деревья.
  * **Second-preimage** (выдать внутренний узел за лист): листья — двойной
    SHA-512 от JSON транзакции, внутренние узлы — двойной SHA-512 от 128 байт
    (два хеша). Подобрать транзакцию, чья сериализация равна склейке двух
    хешей, вычислительно нереально; вдобавок узел заново разбирает каждый
    лист как транзакцию — 128-байтная склейка не пройдёт как валидная tx.
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
    """Хеш листа: двойной SHA-512 от данных (bytes) или их str-представления."""
    if isinstance(data, bytes):
        return _sha512d(data)
    return _sha512d(str(data).encode("utf-8"))


def _as_leaves(items) -> list:
    """Приводит вход к списку хешей-листьев (bytes на входе — уже хеши)."""
    return [item if isinstance(item, bytes) else leaf_hash(item)
            for item in items]


def _build_layers(leaves: list) -> list:
    """Строит все слои дерева снизу вверх (нижний — листья, верхний — корень).

    Нечётный слой дополняется копией последнего узла — так корень совпадает
    с классической схемой Bitcoin/предыдущей реализацией B-hydra (байт-в-байт).
    Возвращает список слоёв; каждый слой — список bytes.
    """
    if not leaves:
        return [[_sha512d(b"")]]
    layers = [list(leaves)]
    while len(layers[-1]) > 1:
        cur = layers[-1]
        if len(cur) % 2 == 1:
            cur = cur + [cur[-1]]        # дублируем последний при нечётном числе
            layers[-1] = cur             # сохраняем дополненный слой (для proof)
        layers.append([_sha512d(cur[i] + cur[i + 1])
                       for i in range(0, len(cur), 2)])
    return layers


def merkle_root(leaves) -> str:
    """Корень дерева Меркла из списка листьев (bytes-хеши или строки) → hex."""
    return _build_layers(_as_leaves(leaves))[-1][0].hex()


def merkle_proof(leaves, index: int) -> list:
    """Доказательство включения листа №index: путь от листа к корню.

    Возвращает список шагов `{"hash": <hex соседа>, "position": "left"|"right"}`,
    где position — с какой стороны сосед при склейке. Проверяется
    `verify_proof()` без доступа ко всем листьям (SPV).
    """
    leaves = _as_leaves(leaves)
    if not leaves:
        raise IndexError("пустой набор листьев")
    if not 0 <= index < len(leaves):
        raise IndexError(f"индекс {index} вне диапазона 0..{len(leaves) - 1}")

    layers = _build_layers(leaves)
    proof = []
    idx = index
    for layer in layers[:-1]:            # все слои, кроме корневого
        sibling = idx ^ 1               # сосед: чётный↔следующий, нечётный↔предыдущий
        position = "right" if idx % 2 == 0 else "left"
        proof.append({"hash": layer[sibling].hex(), "position": position})
        idx //= 2
    return proof


def verify_proof(leaf, proof, root: str) -> bool:
    """Проверяет доказательство включения: лист + путь дают заявленный корень.

    leaf — bytes-хеш листа или исходные данные (будут захешированы);
    root — hex-строка корня из заголовка блока. Возвращает True/False.
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
    """Дерево Меркла с корнем и доказательствами включения."""

    def __init__(self, data_blocks=None):
        self.leaves: list = []
        for block in data_blocks or []:
            self.add(block)

    def add(self, data) -> None:
        """Добавляет лист (данные хешируются двойным SHA-512)."""
        self.leaves.append(leaf_hash(data))

    @classmethod
    def from_hashes(cls, hashes) -> "MerkleTree":
        """Строит дерево из готовых хешей-листьев (bytes)."""
        tree = cls()
        tree.leaves = [h if isinstance(h, bytes) else bytes.fromhex(h)
                       for h in hashes]
        return tree

    @property
    def root(self) -> str:
        return merkle_root(self.leaves)

    def proof(self, index: int) -> list:
        """Доказательство включения листа №index."""
        return merkle_proof(self.leaves, index)

    def prove_data(self, data):
        """Ищет лист по данным и возвращает (index, proof) или (None, None)."""
        target = leaf_hash(data)
        for i, leaf in enumerate(self.leaves):
            if leaf == target:
                return i, merkle_proof(self.leaves, i)
        return None, None

    def has_duplicate_promotion(self) -> bool:
        """True, если где-то дублировался нечётный последний узел
        (индикатор потенциальной CVE-2012-2459-неоднозначности)."""
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
