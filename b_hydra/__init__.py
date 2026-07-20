"""B-hydra — одноранговая электронная денежная система (P2P).

Профессионально оформленный пакет: от хеширования (SHA-512) через кошелёк и
UTXO-транзакции к блокчейну, узлу, P2P-сети и REST API.

Слои:
    hashing / sha2      — хеш-функции (обёртки и реализация «с нуля»)
    merkle, hashcash    — дерево Меркла и proof-of-work примитив
    wallet              — ключи ECDSA secp256k1 и адреса
    transaction         — UTXO-транзакции (входы/выходы), мемпул
    blockchain          — блоки, PoW, динамическая сложность, эмиссия
    economics           — награда, халвинг, конец эмиссии
    node                — узел: UTXO-набор, балансы, майнинг, синхронизация
    p2p, tcp            — одноранговая сеть
    api, mobile_client  — REST API и эталонный мобильный кошелёк
    contract            — смарт-контракты на цепочке: эскроу и смарт-чеки
"""

from __future__ import annotations

from .blockchain import Block, Blockchain
from .contract import ContractManager, verify_cheque
from .merkle import MerkleTree, merkle_proof, merkle_root, verify_proof
from .node import BHydraNode
from .transaction import (
    Transaction,
    TransactionPool,
    TxInput,
    TxOutput,
    coinbase,
)
from .wallet import Wallet, generate_wallet

__all__ = [
    "Block",
    "Blockchain",
    "BHydraNode",
    "Transaction",
    "TransactionPool",
    "TxInput",
    "TxOutput",
    "coinbase",
    "Wallet",
    "generate_wallet",
    "ContractManager",
    "verify_cheque",
    "MerkleTree",
    "merkle_root",
    "merkle_proof",
    "verify_proof",
]

__version__ = "0.0.2"
