"""
Transactinons.py — транзакции B-hydra по модели UTXO (входы и выходы).

Как в Bitcoin, транзакция состоит из:
  * входов (vin)  — ссылок на непотраченные выходы прошлых транзакций (UTXO);
  * выходов (vout) — новых сумм, заблокированных на адрес получателя.

Каждый вход подписывается владельцем расходуемого выхода. Сумма входов должна
быть не меньше суммы выходов; разница — комиссия майнеру. Награда за блок
оформляется специальной coinbase-транзакцией без реальных входов.

Имя файла исторически содержит опечатку (Transactinons) — оно сохранено,
поскольку на него ссылаются другие модули проекта.
"""

import json
import time

if __name__ == "__main__" and __package__ in (None, ""):
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    __package__ = "b_hydra"

from . import hashing
from .blockchain import CHAIN_ID

# Псевдо-идентификатор «ниоткуда» для входа coinbase-транзакции.
NULL_TXID = "0" * 128


class TxOutput:
    """Выход транзакции: сумма, заблокированная на адрес получателя."""

    def __init__(self, amount, address):
        self.amount = float(amount)
        self.address = address

    def to_dict(self):
        return {"amount": self.amount, "address": self.address}

    @classmethod
    def from_dict(cls, data):
        return cls(amount=data["amount"], address=data["address"])

    def __repr__(self):
        return f"<TxOut {self.amount} BHY → {self.address[:12]}…>"


class TxInput:
    """Вход транзакции: ссылка на конкретный выход (txid, index) + подпись."""

    def __init__(self, txid, index, public_key=None, signature=None,
                 pq_public_key=None, pq_signature=None, miner_key=None):
        self.txid = txid            # id транзакции, чей выход расходуется
        self.index = index          # номер выхода в той транзакции (vout)
        self.public_key = public_key  # hex ECDSA-публичного ключа владельца
        self.signature = signature    # hex ECDSA-подписи входа
        # Пост-квантовая часть (только для гибридных входов):
        self.pq_public_key = pq_public_key   # XMSS-корень (hex) владельца
        self.pq_signature = pq_signature     # XMSS-подпись (dict) или None
        # Только для coinbase: публичный ключ автора заметки майнера. У обычных
        # входов ключ лежит в public_key, но у coinbase это поле занято самой
        # заметкой — подписи там исторически не было, а место есть.
        self.miner_key = miner_key

    @property
    def outpoint(self):
        """Уникальная ссылка на расходуемый выход."""
        return (self.txid, self.index)

    def to_dict(self):
        d = {
            "txid": self.txid,
            "index": self.index,
            "public_key": self.public_key,
            "signature": self.signature,
        }
        # PQ-поля пишем только у гибридных входов — обычные tx не меняются
        # (txid и совместимость сериализации сохранены).
        if self.pq_public_key is not None or self.pq_signature is not None:
            d["pq_public_key"] = self.pq_public_key
            d["pq_signature"] = self.pq_signature
        # Ключ автора заметки — только у подписанной coinbase, чтобы обычные
        # транзакции и неподписанные блоки сериализовались как раньше.
        if self.miner_key is not None:
            d["miner_key"] = self.miner_key
        return d

    @classmethod
    def from_dict(cls, data):
        return cls(
            txid=data["txid"],
            index=data["index"],
            public_key=data.get("public_key"),
            signature=data.get("signature"),
            pq_public_key=data.get("pq_public_key"),
            pq_signature=data.get("pq_signature"),
            miner_key=data.get("miner_key"),
        )

    def __repr__(self):
        return f"<TxIn {self.txid[:12]}…:{self.index}>"


class Transaction:
    """Транзакция UTXO: набор входов (vin) и выходов (vout)."""

    def __init__(self, vin=None, vout=None, timestamp=None):
        self.vin = vin or []        # список TxInput
        self.vout = vout or []      # список TxOutput
        # float() обязателен, как и для суммы в TxOutput: подпись берётся от
        # json.dumps, а он пишет int 10 и float 10.0 по-разному. Клиент,
        # приславший целую метку времени (JS сериализует 1785009903.0 как
        # «1785009903»), иначе получал бы неверный payload и отказ подписи.
        self.timestamp = float(timestamp) if timestamp is not None else time.time()
        self._txid = None            # кеш идентификатора…
        self._txid_payload = None    # …и содержимого, от которого он посчитан

    # --- Идентификация и сериализация ------------------------------------
    def signing_payload(self) -> bytes:
        """
        Канонические байты для подписи и txid: идентификатор сети (chain_id,
        защита от replay), outpoints входов и выходы (без подписей и публичных
        ключей), плюс временная метка.
        """
        payload = {
            "chain_id": CHAIN_ID,
            "vin": [{"txid": i.txid, "index": i.index} for i in self.vin],
            "vout": [o.to_dict() for o in self.vout],
            "timestamp": self.timestamp,
        }
        return json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")

    @property
    def txid(self) -> str:
        """Идентификатор транзакции = SHA-512 от её содержимого.

        Кешируется ПО СОДЕРЖИМОМУ: payload собирается заново (это ~6 мкс) и
        сверяется с тем, от которого считался хеш; совпал — отдаём готовый.
        Просто запомнить результат навсегда было бы неверно — транзакцию могут
        достроить после создания, и txid обязан измениться вместе с ней.

        Смысл в том, что SHA-512 здесь В 122 РАЗА дороже сборки payload, а
        `txid` дёргается постоянно: дедуп в мемпуле, индекс, сериализация,
        поиск в блоке. Один `add()` в мемпул трогал его трижды — 2 мс на
        чистом Python-движке, то есть 10 000 транзакций укладывались в
        полминуты чистого хеширования одного и того же.
        """
        payload = self.signing_payload()
        if payload != self._txid_payload:
            self._txid = hashing.sha512(payload)
            self._txid_payload = payload
        return self._txid

    def to_dict(self):
        return {
            "txid": self.txid,
            "vin": [i.to_dict() for i in self.vin],
            "vout": [o.to_dict() for o in self.vout],
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data):
        return cls(
            vin=[TxInput.from_dict(i) for i in data.get("vin", [])],
            vout=[TxOutput.from_dict(o) for o in data.get("vout", [])],
            timestamp=data.get("timestamp"),
        )

    # --- Суммы -----------------------------------------------------------
    @property
    def total_output(self) -> float:
        return sum(o.amount for o in self.vout)

    def size_bytes(self) -> int:
        """Сколько места транзакция займёт в блоке.

        Место в блоке ограничено БАЙТАМИ, поэтому и комиссия сравнивается на
        байт, а не целиком: транзакция с двадцатью входами и щедрой комиссией
        может быть невыгоднее пяти маленьких с той же суммой на всех.
        """
        return len(json.dumps(self.to_dict(),
                              ensure_ascii=False).encode("utf-8"))

    # --- Подпись ---------------------------------------------------------
    def sign(self, wallet):
        """Подписывает все входы транзакции кошельком-владельцем (ECDSA)."""
        payload = self.signing_payload()
        sig = wallet.sign(payload)
        for inp in self.vin:
            inp.public_key = wallet.public_key_hex
            inp.signature = sig
        return self

    def sign_hybrid(self, hybrid_wallet):
        """Подписывает входы ОБЕИМИ схемами (ECDSA + XMSS) — для трат с
        гибридного адреса. Каждый вход тратит один одноразовый XMSS-ключ,
        поэтому на входы уходят РАЗНЫЕ подписи (индексы не переиспользуются)."""
        payload = self.signing_payload()
        for inp in self.vin:
            ecdsa_sig, pq_sig = hybrid_wallet.sign(payload)
            inp.public_key = hybrid_wallet.ecdsa_public_key_hex
            inp.signature = ecdsa_sig
            inp.pq_public_key = hybrid_wallet.pq_public_key
            inp.pq_signature = pq_sig
        return self

    @property
    def is_coinbase(self) -> bool:
        return len(self.vin) == 1 and self.vin[0].txid == NULL_TXID

    def __repr__(self):
        kind = "coinbase" if self.is_coinbase else "tx"
        return f"<{kind} {self.txid[:12]}… in={len(self.vin)} out={len(self.vout)}>"


def message_payload(message, miner, height) -> bytes:
    """
    Канонические байты, которые подписывает майнер, оставляя заметку в блоке.

    Заметка привязана к сети (chain_id), к ВЫСОТЕ и к адресу получателя
    награды — поэтому подписанную чужую заметку нельзя ни переставить в другой
    блок, ни выдать за свою: адрес в payload не совпадёт с coinbase-выходом.
    """
    payload = {
        "chain_id": CHAIN_ID,
        "height": int(height),
        "miner": miner,
        "message": message,
    }
    return json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")


def coinbase(recipient, reward, fee_total=0.0, height=0, message="B-hydra",
             wallet=None):
    """
    Создаёт coinbase-транзакцию (награда майнеру + собранные комиссии).

    Вход — фиктивный (NULL_TXID); `height` в поле index делает txid уникальным
    для каждого блока. Подпись входа не требуется.

    `wallet` — кошелёк майнера. Если он передан, заметка ПОДПИСЫВАЕТСЯ его
    ключом: подпись ложится в свободное поле signature фиктивного входа, ключ —
    в miner_key. Любой узел потом проверит, что заметку написал держатель
    адреса, на который ушла награда.
    """
    vin = [TxInput(txid=NULL_TXID, index=height, public_key=message)]
    vout = [TxOutput(amount=reward + fee_total, address=recipient)]
    if wallet is not None:
        vin[0].miner_key = wallet.public_key_hex
        vin[0].signature = wallet.sign(message_payload(message, recipient, height))
    return Transaction(vin=vin, vout=vout)


def coinbase_message_author(raw, height):
    """
    Адрес автора заметки майнера — или None, если заметка не подписана либо
    подпись негодная.

    `raw` — coinbase-транзакция в виде dict (как она лежит в блоке).
    Проверяется и сама подпись, и то, что ключ принадлежит получателю награды:
    иначе заметку можно было бы подписать любым ключом и «расписаться» чужим.
    """
    from .wallet import Wallet   # локально: wallet.py не должен зависеть от tx

    try:
        vin = raw["vin"][0]
        miner = raw["vout"][0]["address"]
    except (KeyError, IndexError, TypeError):
        return None
    key = vin.get("miner_key")
    signature = vin.get("signature")
    message = vin.get("public_key")
    if not (isinstance(key, str) and isinstance(signature, str)
            and isinstance(message, str)):
        return None
    if not Wallet.verify(key, message_payload(message, miner, height), signature):
        return None
    author = Wallet.address_from_public_key(key)
    return author if author == miner else None


def claims_signed_message(raw) -> bool:
    """Заявлена ли у coinbase подпись заметки (есть ключ или подпись)."""
    try:
        vin = raw["vin"][0]
    except (KeyError, IndexError, TypeError):
        return False
    return vin.get("miner_key") is not None or vin.get("signature") is not None


class TransactionPool:
    """Мемпул неподтверждённых транзакций.

    max_size ограничивает вместимость (анти-DoS): по умолчанию пул держит до
    MAX_MEMPOOL_TRANSACTIONS транзакций. Дедуп и поиск идут по индексу
    txid → транзакция, поэтому add() и get() — O(1), и мемпул спокойно вмещает
    десятки тысяч транзакций.

    ⚠️ Полный мемпул ВЫТЕСНЯЕТ самое дешёвое, а не отказывает всем подряд.
    Прежний `return False` при переполнении означал, что сеть встаёт, как
    только кто-то набил пул копеечными транзакциями: перебить ставкой было
    нельзя вообще ничем. Теперь дороже — значит вперёд, и спам стоит денег.
    Ставка считается НА БАЙТ: место в блоке ограничено байтами.
    """

    def __init__(self, max_size=50000):
        self.max_size = max_size
        self._transactions = []
        self._index = {}                # txid → Transaction
        self._rates = {}                # txid → комиссия за байт

    @property
    def transactions(self):
        return self._transactions

    @transactions.setter
    def transactions(self, txs):
        # Прямое присваивание (например, из _prune_mempool) держит индекс
        # txid в синхроне, чтобы дедуп в add() оставался корректным.
        self._transactions = list(txs)
        self._index = {t.txid: t for t in self._transactions}
        # Ставка нужна КАЖДОЙ транзакции в пуле: cheapest() смотрит только в
        # _rates, и запись без ставки была бы для него невидима.
        previous = self._rates
        self._rates = {txid: previous.get(txid, 0.0) for txid in self._index}

    def fee_rate(self, txid) -> float:
        """Комиссия за байт у транзакции в пуле (0 — если неизвестна)."""
        return self._rates.get(txid, 0.0)

    def cheapest(self):
        """Транзакция с наименьшей ставкой — кандидат на вытеснение.

        Идём по УЖЕ ИЗВЕСТНЫМ txid из `_rates`, а не по объектам: обращение к
        `tx.txid` стоит хеша, и перебор всего пула на каждой вставке был бы
        дороже самой вставки в тысячи раз.
        """
        if not self._rates:
            return None
        txid = min(self._rates, key=self._rates.get)
        return self._index.get(txid)

    def remove(self, txid) -> bool:
        """Убирает транзакцию из пула по txid.

        Нужный объект берётся из индекса и выкидывается по ТОЖДЕСТВУ, а не
        сравнением txid у каждой записи: `tx.txid` пересобирает payload, и
        такой проход по полному пулу стоил 6 мс — при вытеснении на каждой
        вставке это превращалось в секунды на ровном месте.
        """
        target = self._index.pop(txid, None)
        if target is None:
            return False
        self._rates.pop(txid, None)
        for position, candidate in enumerate(self._transactions):
            if candidate is target:
                del self._transactions[position]
                break
        return True

    def add(self, transaction: Transaction, fee: float = 0.0) -> bool:
        """Кладёт транзакцию в пул. `fee` — комиссия целиком, в BHY.

        При переполнении вытесняет самую дешёвую, если новая дороже ЗА БАЙТ.
        Если новая не дороже — отказ: иначе полный пул можно было бы бесконечно
        перемешивать, ничего не платя.
        """
        txid = transaction.txid
        if txid in self._index:
            return False  # дубликат
        size = max(transaction.size_bytes(), 1)
        rate = float(fee) / size
        if self.max_size is not None and len(self._transactions) >= self.max_size:
            victim = self.cheapest()
            if victim is None or self._rates.get(victim.txid, 0.0) >= rate:
                return False          # дешевле того, что уже лежит, — мимо
            self.remove(victim.txid)
        self._transactions.append(transaction)
        self._index[txid] = transaction
        self._rates[txid] = rate
        return True

    def get(self, txid):
        """Транзакция по txid или None. Нужна для ответа на get_tx: сосед
        анонсирует txid, а тело просит отдельно — искать линейным перебором по
        50 000 транзакциям на каждый анонс было бы дорого."""
        return self._index.get(txid)

    def spent_outpoints(self):
        """Все outpoints, уже расходуемые транзакциями в мемпуле."""
        spent = set()
        for tx in self.transactions:
            for inp in tx.vin:
                spent.add(inp.outpoint)
        return spent

    def take_all(self):
        """Забирает все транзакции из пула (для включения в блок)."""
        pending = self.transactions
        self.transactions = []
        return pending

    def __len__(self):
        return len(self.transactions)


# Совместимость со старым кодом, который импортировал `Transactions`.
Transactions = Transaction


if __name__ == "__main__":
    cb = coinbase("BHYminer", 50, fee_total=0.5, height=1)
    print("coinbase:", cb)
    print("  is_coinbase:", cb.is_coinbase)
    print("  txid:", cb.txid[:32], "…")
    print("  выход:", cb.vout[0])
