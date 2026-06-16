"""Тесты транзакций UTXO: txid, coinbase, мемпул, сериализация."""

from b_hydra.transaction import (
    Transaction, TransactionPool, TxInput, TxOutput, coinbase,
)


def _sample_tx(ts=1.0):
    return Transaction(vin=[TxInput("aa", 0)],
                       vout=[TxOutput(5, "BHYx")], timestamp=ts)


def test_txid_is_deterministic():
    assert _sample_tx().txid == _sample_tx().txid


def test_txid_changes_with_content():
    assert _sample_tx().txid != Transaction(
        vin=[TxInput("aa", 0)], vout=[TxOutput(6, "BHYx")], timestamp=1.0
    ).txid


def test_coinbase_properties():
    cb = coinbase("BHYminer", reward=50, fee_total=0.5, height=3)
    assert cb.is_coinbase
    assert cb.vout[0].amount == 50.5
    assert cb.total_output == 50.5


def test_regular_tx_is_not_coinbase():
    assert not _sample_tx().is_coinbase


def test_pool_rejects_duplicates():
    pool = TransactionPool()
    tx = _sample_tx()
    assert pool.add(tx)
    assert not pool.add(tx)
    assert len(pool) == 1


def test_pool_spent_outpoints():
    pool = TransactionPool()
    pool.add(_sample_tx())
    assert ("aa", 0) in pool.spent_outpoints()


def test_to_dict_from_dict_roundtrip():
    tx = coinbase("BHYm", 50, height=1)
    restored = Transaction.from_dict(tx.to_dict())
    assert restored.txid == tx.txid
    assert restored.is_coinbase
