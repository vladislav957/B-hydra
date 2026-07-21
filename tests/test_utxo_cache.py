"""Инкрементальный кэш UTXO: корректность, реорги, независимость копий."""

import pytest

from b_hydra.node import BHydraNode
from b_hydra.wallet import generate_wallet


def _brute_force_utxos(node):
    """Эталон: полный скан цепочки (как считал старый utxo_set)."""
    utxos = {}
    for block in node.blockchain.chain:
        for tx in node._block_transactions(block):
            for inp in tx.get("vin", []):
                utxos.pop((inp["txid"], inp["index"]), None)
            for index, out in enumerate(tx.get("vout", [])):
                utxos[(tx["txid"], index)] = {
                    "amount": out["amount"], "address": out["address"]}
    return utxos


def _busy_node(blocks=12):
    node = BHydraNode(difficulty=1)
    a, b = generate_wallet(), generate_wallet()
    for i in range(blocks):
        if i > 1 and i % 2 == 0:
            tx = node.create_transaction(a, b.address, 1, 0.1)
            if tx:
                node.add_transaction(tx)
        node.mine_pending(a.address)
    return node, a, b


def test_cache_matches_brute_force_scan():
    """Кэш байт-в-байт совпадает с полным сканом на живой цепочке."""
    node, a, b = _busy_node()
    assert node.utxo_set() == _brute_force_utxos(node)
    # и после ещё одного блока (инкрементальное применение)
    node.mine_pending(b.address)
    assert node.utxo_set() == _brute_force_utxos(node)


def test_cache_returns_independent_copy():
    """Мутация возвращённого набора (как в майнинге) не портит кэш."""
    node, a, _ = _busy_node(6)
    working = node.utxo_set()
    working.clear()                                   # варварская мутация
    assert node.utxo_set() == _brute_force_utxos(node)  # кэш цел


def test_cache_survives_replace_chain_reorg():
    """Замена цепочки (реорг) → полная пересборка кэша, балансы верные."""
    node, a, _ = _busy_node(4)
    stale_balance = node.get_balance(a.address)      # кэш прогрет

    # Другой узел с более тяжёлой цепочкой
    other = BHydraNode(difficulty=1)
    winner = generate_wallet()
    for _ in range(8):
        other.mine_pending(winner.address)
    assert node.replace_chain(other.blockchain.to_dicts())

    # После реорга кэш обязан отражать НОВУЮ цепочку
    assert node.utxo_set() == _brute_force_utxos(node)
    assert node.get_balance(winner.address) == other.get_balance(winner.address)
    assert node.get_balance(a.address) == 0           # старая цепь забыта
    assert stale_balance > 0                          # (а была ненулевая)


def test_cache_after_receive_block():
    """Блок, принятый от пира, инкрементально попадает в кэш."""
    a_node, miner, _ = _busy_node(3)
    b_node = BHydraNode(difficulty=1)
    b_node.replace_chain(a_node.blockchain.to_dicts())
    b_node.utxo_set()                                 # кэш прогрет

    new_block = a_node.mine_pending(miner.address)    # новый блок у пира A
    assert b_node.receive_block(new_block.to_dict())
    assert b_node.utxo_set() == _brute_force_utxos(b_node)
    assert b_node.get_balance(miner.address) == a_node.get_balance(miner.address)


def test_tx_index_consistent_with_chain():
    """Индекс транзакций находит то же, что и полный скан."""
    node, a, b = _busy_node()
    for block in node.blockchain.chain:
        for tx in node._block_transactions(block):
            found = node.find_transaction(tx["txid"])
            assert found is not None
            assert found["block_index"] == block.index
            assert found["transaction"]["txid"] == tx["txid"]
    assert node.find_transaction("00" * 64) is None


def test_balances_stable_across_many_calls():
    """Повторные вызовы (кэш-путь) дают одинаковый результат."""
    node, a, b = _busy_node()
    first = (node.get_balance(a.address), node.get_balance(b.address))
    for _ in range(10):
        assert (node.get_balance(a.address),
                node.get_balance(b.address)) == first


# --- Кэш проверенных ECDSA-подписей ------------------------------------------

def test_signature_cache_hits_on_revalidation():
    """Повторная проверка той же транзакции (мемпул → майнинг) идёт из кэша."""
    from b_hydra.node import _verify_ecdsa_cached
    _verify_ecdsa_cached.cache_clear()
    node = BHydraNode(difficulty=1)
    a, b = generate_wallet(), generate_wallet()
    node.mine_pending(a.address)
    tx = node.create_transaction(a, b.address, 5, 0.1)
    assert node.add_transaction(tx)                    # первая проверка (miss)
    misses_after_add = _verify_ecdsa_cached.cache_info().misses
    node.mine_pending(b.address)                       # повторная проверка
    info = _verify_ecdsa_cached.cache_info()
    assert info.hits > 0                               # кэш реально сработал
    assert info.misses == misses_after_add             # новых ECDSA не было


def test_signature_cache_does_not_accept_forgery():
    """Тёплый кэш не помогает подделке: другая подпись/данные — другой ключ."""
    from b_hydra.transaction import Transaction, TxInput, TxOutput
    node = BHydraNode(difficulty=1)
    a, b, evil = generate_wallet(), generate_wallet(), generate_wallet()
    node.mine_pending(a.address)
    good = node.create_transaction(a, b.address, 5, 0.1)
    assert node.add_transaction(good)                  # честная — в кэше True

    # Подделка 1: та же структура, но подпись чужим ключом.
    op = node.find_spendable(a.address)[0][0]
    forged = Transaction(vin=[TxInput(op[0], op[1])],
                         vout=[TxOutput(5, evil.address)])
    forged.sign(evil)                                  # ключ не владельца
    assert not node.validate_transaction(forged)

    # Подделка 2: валидная подпись, но данные подменены после подписания.
    tampered = node.create_transaction(a, b.address, 1, 0.1)
    if tampered is not None:
        tampered.vout[0].amount = 999.0                # правка после подписи
        assert not node.validate_transaction(tampered)
