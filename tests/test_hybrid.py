"""Гибридные (квантово-защищённые) кошельки в консенсусе: ECDSA + XMSS."""

import pytest

from b_hydra import hashing
from b_hydra.node import BHydraNode
from b_hydra.pqcrypto import HybridWallet
from b_hydra.wallet import (
    Wallet, generate_wallet, hybrid_address, is_hybrid_address,
    is_valid_address, address_version, HYBRID_VERSION,
)


@pytest.fixture(autouse=True)
def _fast_sha():
    """Гибрид гоняет XMSS-keygen (тысячи хешей) — на быстром движке ради CI;
    байт-совместимость pure↔fast проверяют test_hashing/test_pqcrypto."""
    original = hashing.is_pure()
    hashing.use_pure_sha(False)
    try:
        yield
    finally:
        hashing.use_pure_sha(original)


# --- Адреса -----------------------------------------------------------------

def test_hybrid_address_format_and_detection():
    w = HybridWallet(height=3, seed=b"h1")
    addr = w.address
    assert addr.startswith("BHY")
    assert is_valid_address(addr)                    # принимается как получатель
    assert is_hybrid_address(addr)                   # и распознан как гибридный
    assert address_version(addr) == HYBRID_VERSION
    # обычный ECDSA-адрес — не гибридный
    assert not is_hybrid_address(generate_wallet().address)


def test_hybrid_address_binds_both_keys():
    w = HybridWallet(height=3, seed=b"h2")
    # адрес пересобирается ровно из ECDSA-ключа и XMSS-корня
    assert hybrid_address(w.ecdsa.public_key_bytes, w.pq_public_key) == w.address
    # смена любого из ключей → другой адрес
    other_pq = HybridWallet(height=3, seed=b"other").pq_public_key
    assert hybrid_address(w.ecdsa.public_key_bytes, other_pq) != w.address


# --- Полный цикл трат с гибридного адреса -----------------------------------

def _fund_hybrid(node, hw, blocks=1):
    """Майнит на гибридный адрес и подтверждает (награда становится UTXO)."""
    for _ in range(blocks):
        node.mine_pending(hw.address)


def test_hybrid_spend_end_to_end():
    node = BHydraNode(difficulty=1)
    alice = HybridWallet(height=4, seed=b"alice")     # 16 одноразовых ключей
    bob = generate_wallet()
    _fund_hybrid(node, alice)                          # +50 на гибридный адрес
    assert node.get_balance(alice.address) == 50

    tx = node.create_hybrid_transaction(alice, bob.address, 10, fee=0.5)
    assert tx is not None
    # у входа обе подписи
    assert tx.vin[0].signature and tx.vin[0].pq_signature
    assert node.add_transaction(tx)                   # прошёл проверку консенсуса
    node.mine_pending(generate_wallet().address)

    assert node.get_balance(bob.address) == 10
    assert node.get_balance(alice.address) == pytest.approx(39.5)
    assert node.is_valid()


def test_quantum_attacker_with_only_ecdsa_cannot_spend():
    """Квантовый злоумышленник сломал ECDSA (знает приватный ECDSA-ключ), но
    не имеет XMSS-ключа — потратить с гибридного адреса он не может."""
    node = BHydraNode(difficulty=1)
    victim = HybridWallet(height=3, seed=b"victim")
    _fund_hybrid(node, victim)

    # «Взломанный» ECDSA-ключ жертвы, обычная (не гибридная) подпись.
    stolen = victim.ecdsa
    tx = node.create_transaction(stolen, generate_wallet().address, 10)
    # адрес выхода гибридный → чистая ECDSA-подпись не проходит
    assert tx is None or not node.add_transaction(tx)
    # даже вручную собранная ECDSA-only трата отвергается валидацией
    from b_hydra.transaction import Transaction, TxInput, TxOutput
    op = node.find_spendable(victim.address)[0][0]
    forged = Transaction(vin=[TxInput(op[0], op[1])],
                         vout=[TxOutput(5, generate_wallet().address)])
    forged.sign(stolen)                                # только ECDSA
    assert not node.validate_transaction(forged)


def test_forged_pq_key_rejected():
    """Подмена XMSS-корня (чужой pq_public_key) не проходит: отпечаток адреса
    не сойдётся, и XMSS-подпись будет от другого дерева."""
    node = BHydraNode(difficulty=1)
    alice = HybridWallet(height=3, seed=b"a1")
    _fund_hybrid(node, alice)
    tx = node.create_hybrid_transaction(alice, generate_wallet().address, 5)
    # злоумышленник подменяет pq_public_key на чужой корень
    tx.vin[0].pq_public_key = HybridWallet(height=3, seed=b"evil").pq_public_key
    assert not node.validate_transaction(tx)


# --- Учёт одноразовых XMSS-ключей (консенсус) -------------------------------

def test_one_time_key_reuse_rejected():
    """Один XMSS-индекс нельзя потратить дважды: повторная трата тем же
    ключом отвергается учётом израсходованных индексов."""
    node = BHydraNode(difficulty=1)
    alice = HybridWallet(height=4, seed=b"reuse")
    _fund_hybrid(node, alice)
    tx1 = node.create_hybrid_transaction(alice, generate_wallet().address, 10)
    assert node.add_transaction(tx1)

    # Искусственно повторяем подпись первого входа (индекс 0) в новой трате.
    from b_hydra.transaction import Transaction, TxInput, TxOutput
    node.mine_pending(generate_wallet().address)      # подтверждаем tx1
    spend = node.find_spendable(alice.address)
    assert spend, "должна остаться сдача"
    op = spend[0][0]
    replay = Transaction(vin=[TxInput(op[0], op[1])],
                         vout=[TxOutput(1, generate_wallet().address)])
    # переиспользуем уже потраченный одноразовый ключ (index 0) из tx1
    replay.vin[0].public_key = tx1.vin[0].public_key
    replay.vin[0].signature = tx1.vin[0].signature
    replay.vin[0].pq_public_key = tx1.vin[0].pq_public_key
    replay.vin[0].pq_signature = tx1.vin[0].pq_signature
    assert (tx1.vin[0].pq_public_key, tx1.vin[0].pq_signature["index"]) \
        in node.pq_used_indices()
    assert not node.validate_transaction(replay)      # индекс уже израсходован


def test_pq_used_indices_tracks_chain():
    node = BHydraNode(difficulty=1)
    alice = HybridWallet(height=4, seed=b"track")
    _fund_hybrid(node, alice)
    assert node.pq_used_indices() == set()            # пока трат не было
    tx = node.create_hybrid_transaction(alice, generate_wallet().address, 10)
    node.add_transaction(tx)
    node.mine_pending(generate_wallet().address)
    used = node.pq_used_indices()
    assert (alice.pq_public_key, 0) in used           # индекс 0 израсходован


def test_normal_ecdsa_wallets_still_work():
    """Гибрид не ломает обычные ECDSA-кошельки: классический путь цел."""
    node = BHydraNode(difficulty=1)
    a, b = generate_wallet(), generate_wallet()
    node.mine_pending(a.address)
    tx = node.create_transaction(a, b.address, 10, fee=0.5)
    assert node.add_transaction(tx)
    node.mine_pending(b.address)
    assert node.get_balance(b.address) == pytest.approx(60.5)
    assert node.is_valid()
