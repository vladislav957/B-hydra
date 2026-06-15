"""
Blocks_date.py — работа с временными метками блоков B-hydra.

Утилиты для перевода timestamp блока в человекочитаемую дату и сбора
краткой сводки по блокам цепочки.
"""

from datetime import datetime, timezone


def format_timestamp(timestamp: float) -> str:
    """Переводит unix-время блока в строку UTC."""
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S UTC"
    )


def block_date(block) -> str:
    """Дата конкретного блока."""
    return format_timestamp(block.timestamp)


def chain_dates(blockchain):
    """Список (index, дата, hash) по всем блокам цепочки."""
    return [
        (block.index, format_timestamp(block.timestamp), block.hash)
        for block in blockchain.chain
    ]


if __name__ == "__main__":
    from Blockchain import Blockchain

    chain = Blockchain(difficulty=2)
    chain.add_block("Первый блок данных")
    chain.add_block("Второй блок данных")
    for index, date, block_hash in chain_dates(chain):
        print(f"Блок #{index} | {date} | {block_hash[:16]}…")
