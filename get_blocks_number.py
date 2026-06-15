"""
get_blocks_number.py — высота цепочки B-hydra.

Небольшая утилита поверх канонического Blockchain: создать цепочку, добавить
блоки и узнать их количество (высоту).
"""

from Blockchain import Blockchain


def get_blocks_number(blockchain) -> int:
    """Возвращает количество блоков (высоту цепочки)."""
    return len(blockchain.chain)


if __name__ == "__main__":
    blockchain = Blockchain(difficulty=2)
    blockchain.add_block("Первый блок данных")
    blockchain.add_block("Второй блок данных")
    print("Количество блоков в цепочке:", get_blocks_number(blockchain))
