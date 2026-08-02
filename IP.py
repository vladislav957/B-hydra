"""
IP.py — сетевые идентификаторы узлов B-hydra.

Генерация ID узла из публичного ключа и получение локального IP-адреса.
"""

import hashlib
import socket


def generate_node_id(public_key: str) -> str:
    """ID узла = SHA-256 от публичного ключа (hex)."""
    if isinstance(public_key, bytes):
        public_key = public_key.hex()
    return hashlib.sha256(public_key.encode("utf-8")).hexdigest()


def get_local_ip() -> str:
    """Определяет локальный IP-адрес машины (реализация — в `b_hydra.p2p`)."""
    from b_hydra.p2p import local_ip
    return local_ip()


if __name__ == "__main__":
    node_id = generate_node_id("node_public_key_example")
    print(f"Node ID : {node_id}")
    print(f"Local IP: {get_local_ip()}")
