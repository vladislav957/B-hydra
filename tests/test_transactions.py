"""Тесты транзакций UTXO: txid, coinbase, мемпул, сериализация."""

from b_hydra import transaction as txmod
from b_hydra.transaction import (
    Transaction, TransactionPool, TxInput, TxOutput, coinbase,
)
from b_hydra.wallet import Wallet, generate_wallet


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


def test_signing_payload_includes_chain_id():
    assert b"chain_id" in _sample_tx().signing_payload()


def test_chain_id_prevents_cross_network_replay(monkeypatch):
    """Подпись с одним chain_id недействительна в сети с другим chain_id."""
    wallet = generate_wallet()
    tx = Transaction(vin=[TxInput("aa", 0)], vout=[TxOutput(5, "BHYx")],
                     timestamp=1.0)
    tx.sign(wallet)
    sig, pub = tx.vin[0].signature, tx.vin[0].public_key
    # В своей сети подпись валидна.
    assert Wallet.verify(pub, tx.signing_payload(), sig)
    # На другой сети (другой chain_id) та же транзакция уже не проходит.
    monkeypatch.setattr(txmod, "CHAIN_ID", "other-network")
    assert not Wallet.verify(pub, tx.signing_payload(), sig)


# --- Кеш txid ------------------------------------------------------------------
def test_txid_is_cached_but_follows_the_content():
    """txid кешируется, но обязан меняться вместе с транзакцией.

    Запомнить его навсегда было бы неверно: транзакцию достраивают после
    создания, и «залипший» идентификатор означал бы, что дедуп в мемпуле и
    поиск в блоке работают по устаревшему ключу.
    """
    from b_hydra.transaction import Transaction, TxInput, TxOutput

    tx = Transaction(vin=[TxInput("ab" * 64, 0)],
                     vout=[TxOutput(1.0, "BHYxxx")], timestamp=1.0)
    first = tx.txid
    assert tx.txid == first                      # повтор — тот же

    tx.vout.append(TxOutput(2.0, "BHYyyy"))      # содержимое изменилось
    assert tx.txid != first

    tx.timestamp = 2.0
    assert tx.txid != first


def test_signature_does_not_change_the_txid():
    """Подпись не входит в payload — значит и txid от неё не зависит.

    Это правило консенсуса (иначе подписать транзакцию было бы невозможно:
    подпись меняла бы то, что подписывают), и кеш обязан ему следовать.
    """
    from b_hydra.wallet import generate_wallet

    wallet = generate_wallet()
    tx = Transaction(vin=[TxInput("ab" * 64, 0)],
                     vout=[TxOutput(1.0, wallet.address)], timestamp=1.0)
    before = tx.txid
    tx.sign(wallet)
    assert tx.txid == before
    assert tx.vin[0].signature is not None


def test_txid_cache_does_not_leak_between_transactions():
    """Кеш живёт на экземпляре, а не на классе."""
    from b_hydra.transaction import Transaction, TxInput, TxOutput

    first = Transaction(vin=[TxInput("ab" * 64, 0)],
                        vout=[TxOutput(1.0, "BHYaaa")], timestamp=1.0)
    second = Transaction(vin=[TxInput("cd" * 64, 0)],
                         vout=[TxOutput(1.0, "BHYbbb")], timestamp=1.0)
    assert first.txid != second.txid


def test_roundtrip_keeps_the_txid():
    """Сериализация туда-обратно не должна менять идентификатор."""
    from b_hydra.transaction import Transaction, TxInput, TxOutput

    tx = Transaction(vin=[TxInput("ab" * 64, 3)],
                     vout=[TxOutput(1.5, "BHYxxx")], timestamp=7.0)
    assert Transaction.from_dict(tx.to_dict()).txid == tx.txid
