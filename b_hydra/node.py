"""
Node.py — узел сети B-hydra (модель UTXO).

Узел хранит блокчейн и мемпул, ведёт набор непотраченных выходов (UTXO),
принимает и проверяет транзакции (ссылки на UTXO + подписи входов), майнит
блоки с coinbase-наградой и считает балансы как сумму UTXO адреса.

Сетевую (сокетную) часть см. в P2P.py.
"""

import json

import time
from functools import lru_cache

if __name__ == "__main__" and __package__ in (None, ""):
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    __package__ = "b_hydra"

from .blockchain import (
    Block, Blockchain, DEFAULT_DIFFICULTY, sha512d,
    MAX_BLOCK_TRANSACTIONS, MAX_MEMPOOL_TRANSACTIONS, MAX_FUTURE_DRIFT,
)
from .transaction import (
    NULL_TXID, Transaction, TxInput, TxOutput, TransactionPool, coinbase,
)
from .wallet import Wallet, is_valid_address

# Допуск для сравнения сумм с плавающей точкой.
_EPS = 1e-9


def _is_coinbase_dict(tx: dict) -> bool:
    """coinbase-транзакция: единственный вход «ниоткуда» (NULL_TXID)."""
    vin = tx.get("vin", [])
    return len(vin) == 1 and vin[0].get("txid") == NULL_TXID


@lru_cache(maxsize=20000)
def _verify_ecdsa_cached(public_key: str, payload: bytes, signature: str) -> bool:
    """Кэш проверенных ECDSA-подписей.

    Одна и та же транзакция проверяется несколько раз (вход в мемпул →
    сборка блока → прунинг мемпула → приём блока), а ECDSA — самая дорогая
    операция узла. Ключ — ТОЧНАЯ тройка (ключ, данные, подпись), значение —
    реальный результат проверки именно этих байтов, поэтому «отравить» кэш
    невозможно: другая подпись или другие данные — другой ключ. LRU
    ограничивает память (~20k записей)."""
    return Wallet.verify(public_key, payload, signature)


class BHydraNode:
    """Логический узел B-hydra (блокчейн + мемпул + UTXO)."""

    def __init__(self, difficulty=DEFAULT_DIFFICULTY,
                 mempool_size=MAX_MEMPOOL_TRANSACTIONS):
        self.blockchain = Blockchain(difficulty=difficulty)
        self.mempool = TransactionPool(max_size=mempool_size)
        self._rebuild_effective()

    # --- Набор UTXO (инкрементальный кэш) --------------------------------
    def utxo_set(self):
        """
        Набор непотраченных выходов по всей цепочке:
        dict (txid, index) -> {"amount", "address"}.

        КЭШИРУЕТСЯ инкрементально: при росте цепочки применяются только новые
        блоки; полная пересборка — лишь при реорганизации (цепочка заменена).
        Заодно ведёт индекс транзакций txid → (блок, tx) — им пользуются
        find_transaction / address_history / merkle_proof — и учёт
        израсходованных PQ-ключей цепочки. Возвращает КОПИЮ (вызывающие,
        например майнинг, мутируют её как рабочий набор).
        """
        chain = self.blockchain.chain
        done = getattr(self, "_confirmed_height", None)
        if (done is None or done > len(chain)
                or (done > 0 and chain[done - 1].hash != self._confirmed_tip)):
            # Первая инициализация или реорганизация — пересборка с нуля.
            self._confirmed = {}
            self._tx_index = {}
            self._pq_chain_used = set()
            done = 0
        for block in chain[done:]:            # только НОВЫЕ блоки
            for tx in self._block_transactions(block):
                self._tx_index[tx["txid"]] = (block.index, tx)
                for inp in tx.get("vin", []):
                    self._confirmed.pop((inp["txid"], inp["index"]), None)
                    pq = inp.get("pq_signature")
                    root = inp.get("pq_public_key")
                    if pq and root is not None:
                        self._pq_chain_used.add((root, pq.get("index")))
                for index, out in enumerate(tx.get("vout", [])):
                    self._confirmed[(tx["txid"], index)] = {
                        "amount": out["amount"], "address": out["address"]}
        self._confirmed_height = len(chain)
        self._confirmed_tip = chain[-1].hash if chain else None
        return dict(self._confirmed)

    def get_balance(self, address: str) -> float:
        """Баланс адреса = сумма его непотраченных выходов (UTXO)."""
        return sum(u["amount"] for u in self.utxo_set().values()
                   if u["address"] == address)

    def find_spendable(self, address: str, include_mempool: bool = False):
        """UTXO, принадлежащие адресу: список (outpoint, amount).

        include_mempool=True учитывает и выходы неподтверждённых транзакций из
        мемпула (сдачу) — тогда с одного кошелька можно выстроить цепочку из
        многих транзакций, не дожидаясь майнинга каждой. Поиск идёт по индексу
        адресов, поэтому не зависит от общего размера мемпула.
        """
        if include_mempool:
            self.effective_utxo_set()  # гарантируем актуальность кэша/индекса
            return [(op, self._effective[op]["amount"])
                    for op in self._eff_by_addr.get(address, ())]
        return [(outpoint, u["amount"])
                for outpoint, u in self.utxo_set().items()
                if u["address"] == address]

    @staticmethod
    def _apply_tx_to_utxos(tx: Transaction, utxos: dict):
        """Применяет транзакцию к рабочему набору UTXO: убирает потраченные
        входами выходы и добавляет собственные выходы (для цепочек в мемпуле
        и внутри одного блока)."""
        for inp in tx.vin:
            utxos.pop(inp.outpoint, None)
        for index, out in enumerate(tx.vout):
            utxos[(tx.txid, index)] = {"amount": out.amount,
                                       "address": out.address}

    # --- Кэш эффективного набора UTXO (подтверждённые + мемпул) ----------
    def _cache_spend(self, outpoint):
        u = self._effective.pop(outpoint, None)
        if u is not None:
            bucket = self._eff_by_addr.get(u["address"])
            if bucket is not None:
                bucket.discard(outpoint)
                if not bucket:
                    self._eff_by_addr.pop(u["address"], None)

    def _cache_add(self, outpoint, amount, address):
        self._effective[outpoint] = {"amount": amount, "address": address}
        self._eff_by_addr.setdefault(address, set()).add(outpoint)

    def _cache_apply(self, tx: Transaction):
        """Инкрементально обновляет кэш эффективного набора одной транзакцией."""
        for inp in tx.vin:
            self._cache_spend(inp.outpoint)
        for index, out in enumerate(tx.vout):
            self._cache_add((tx.txid, index), out.amount, out.address)

    def _rebuild_effective(self):
        """Полностью пересобирает кэш эффективного набора и индекс адресов.
        Вызывается при смене цепочки или массовой правке мемпула (майнинг,
        prune); в горячем пути add_transaction кэш правится инкрементально."""
        self._effective = self.utxo_set()
        self._eff_by_addr = {}
        for op, u in self._effective.items():
            self._eff_by_addr.setdefault(u["address"], set()).add(op)
        for tx in self.mempool.transactions:
            self._cache_apply(tx)
        return self._effective

    def effective_utxo_set(self) -> dict:
        """Кэш «виртуального» набора UTXO: подтверждённые + выходы мемпула −
        их входы. По нему проверяются и собираются новые транзакции: он даёт
        тратить неподтверждённую сдачу и не даёт повторно потратить выход,
        уже зарезервированный мемпулом."""
        if getattr(self, "_effective", None) is None:
            self._rebuild_effective()
        return self._effective

    # --- Авторизация входа (ECDSA или гибрид ECDSA+XMSS) -----------------
    def pq_used_indices(self, include_mempool: bool = True) -> set:
        """Множество израсходованных одноразовых XMSS-ключей (root, index).

        Учёт в консенсусе: один WOTS/XMSS-ключ нельзя использовать дважды —
        повтор ослабляет подпись. Часть по цепочке берётся из инкрементального
        кэша (utxo_set ведёт её попутно), мемпул добавляется вживую."""
        self.utxo_set()                       # актуализировать кэш цепочки
        used = set(self._pq_chain_used)
        if include_mempool:
            for tx in self.mempool.transactions:
                for inp in tx.vin:
                    if inp.pq_signature and inp.pq_public_key is not None:
                        used.add((inp.pq_public_key,
                                  inp.pq_signature.get("index")))
        return used

    @staticmethod
    def _verify_input_auth(public_key, signature, pq_public_key, pq_signature,
                           utxo_address, payload, pq_used) -> bool:
        """Проверяет, что вход авторизован владельцем расходуемого выхода.

        Обычный (ECDSA) выход — по ключу и ECDSA-подписи. Гибридный выход
        (0x2f) требует ОБЕ подписи (ECDSA + XMSS), совпадение отпечатка обоих
        ключей с адресом и неповторное использование одноразового XMSS-ключа.
        При успешном гибридном входе ключ помечается использованным в pq_used.
        """
        from .wallet import Wallet, hybrid_address, is_hybrid_address
        if not public_key or not signature:
            return False
        if is_hybrid_address(utxo_address):
            if not pq_public_key or not pq_signature:
                return False  # с гибридного адреса — только гибридная подпись
            if hybrid_address(bytes.fromhex(public_key),
                              pq_public_key) != utxo_address:
                return False  # ключи не соответствуют адресу
            key = (pq_public_key, pq_signature.get("index"))
            if key in pq_used:
                return False  # повторное использование одноразового XMSS-ключа
            from .pqcrypto import MerkleSigner
            # ECDSA-половина — через кэш; XMSS (хеши) проверяется напрямую.
            if not _verify_ecdsa_cached(public_key, payload, signature):
                return False
            if not MerkleSigner.verify(pq_public_key, payload, pq_signature):
                return False
            pq_used.add(key)
            return True
        # Обычный ECDSA-выход: PQ-поля игнорируются.
        if Wallet.address_from_public_key(public_key) != utxo_address:
            return False
        return _verify_ecdsa_cached(public_key, payload, signature)

    # --- Проверка транзакции ---------------------------------------------
    def validate_transaction(self, tx: Transaction, utxos=None,
                             reserved=None, pq_used=None) -> bool:
        """
        Проверяет обычную (не coinbase) транзакцию:
          * входы ссылаются на существующие непотраченные выходы;
          * публичный ключ входа соответствует адресу расходуемого выхода;
          * подпись каждого входа верна;
          * сумма входов >= суммы выходов (разница — комиссия >= 0);
          * нет повторного расходования.
        """
        if tx.is_coinbase:
            return False  # coinbase создаёт только узел при майнинге
        if not tx.vin or not tx.vout:
            return False
        if any(o.amount <= 0 for o in tx.vout):
            return False

        utxos = utxos if utxos is not None else self.utxo_set()
        reserved = reserved if reserved is not None else set()
        # Учёт одноразовых XMSS-ключей: если не передан, собираем из цепочки и
        # мемпула (гибридную трату нельзя повторить тем же индексом).
        pq_used = pq_used if pq_used is not None else self.pq_used_indices()
        payload = tx.signing_payload()

        total_in = 0.0
        seen = set()
        for inp in tx.vin:
            outpoint = inp.outpoint
            if outpoint in seen or outpoint in reserved:
                return False  # двойная трата
            seen.add(outpoint)
            utxo = utxos.get(outpoint)
            if utxo is None:
                return False  # вход ссылается на несуществующий/потраченный выход
            if not self._verify_input_auth(
                    inp.public_key, inp.signature, inp.pq_public_key,
                    inp.pq_signature, utxo["address"], payload, pq_used):
                return False
            total_in += utxo["amount"]

        return total_in >= tx.total_output

    # --- Транзакции ------------------------------------------------------
    def add_transaction(self, tx: Transaction) -> bool:
        """Добавляет транзакцию в мемпул после проверки UTXO и подписей.

        Проверка идёт против эффективного набора UTXO (подтверждённые + мемпул),
        поэтому транзакция может тратить неподтверждённую сдачу предыдущей —
        так мемпул наполняется тысячами связанных транзакций. Повторная трата
        уже зарезервированного выхода отклоняется (его нет в наборе)."""
        if tx is None:
            return False
        if not self.validate_transaction(tx, utxos=self.effective_utxo_set()):
            return False
        if not self.mempool.add(tx):
            return False
        self._cache_apply(tx)  # инкрементально: без пересборки всего набора
        return True

    def create_transaction(self, wallet: Wallet, recipient: str,
                           amount: float, fee: float = 0.0) -> Transaction:
        """
        Собирает подписанную транзакцию: выбирает UTXO отправителя на сумму
        amount + fee, формирует выход получателю и сдачу обратно отправителю.

        Возвращает Transaction или None, если средств недостаточно, адрес
        получателя некорректен либо сумма/комиссия неположительные.
        """
        if not is_valid_address(recipient):
            return None
        if amount <= 0 or fee < 0:
            return None  # нулевая/отрицательная сумма → некорректная транзакция
        need = amount + fee
        chosen, gathered = [], 0.0
        # include_mempool=True: можно тратить и неподтверждённую сдачу, поэтому
        # с одного кошелька собирается цепочка из многих транзакций подряд.
        for outpoint, value in self.find_spendable(wallet.address,
                                                   include_mempool=True):
            chosen.append((outpoint, value))
            gathered += value
            if gathered >= need:
                break
        if gathered < need:
            return None  # недостаточно средств

        vin = [TxInput(txid=op[0], index=op[1]) for op, _ in chosen]
        vout = [TxOutput(amount=amount, address=recipient)]
        change = gathered - need
        if change > 0:
            vout.append(TxOutput(amount=change, address=wallet.address))

        tx = Transaction(vin=vin, vout=vout)
        tx.sign(wallet)
        return tx

    def create_hybrid_transaction(self, hybrid_wallet, recipient: str,
                                  amount: float, fee: float = 0.0):
        """Собирает транзакцию с ГИБРИДНОГО (квантово-защищённого) адреса.

        Подписывает каждый вход и ECDSA, и XMSS (одноразовый ключ на вход).
        Возвращает Transaction или None — при некорректном адресе/сумме,
        нехватке средств либо нехватке XMSS-ключей (нужен по одному на вход)."""
        if not is_valid_address(recipient):
            return None
        if amount <= 0 or fee < 0:
            return None
        need = amount + fee
        chosen, gathered = [], 0.0
        for outpoint, value in self.find_spendable(hybrid_wallet.address,
                                                   include_mempool=True):
            chosen.append((outpoint, value))
            gathered += value
            if gathered >= need:
                break
        if gathered < need:
            return None  # недостаточно средств
        if hybrid_wallet.remaining < len(chosen):
            return None  # не хватает одноразовых XMSS-ключей на все входы

        vin = [TxInput(txid=op[0], index=op[1]) for op, _ in chosen]
        vout = [TxOutput(amount=amount, address=recipient)]
        change = gathered - need
        if change > 0:
            vout.append(TxOutput(amount=change, address=hybrid_wallet.address))

        tx = Transaction(vin=vin, vout=vout)
        tx.sign_hybrid(hybrid_wallet)
        return tx

    # --- Майнинг ---------------------------------------------------------
    def mine_pending(self, miner_address: str):
        """Собирает транзакции из мемпула в блок и майнит его.

        Берёт до MAX_BLOCK_TRANSACTIONS-1 транзакций (плюс coinbase), проверяя
        их по нарастающему набору UTXO — поэтому в один блок попадают и связанные
        цепочки (потомок тратит выход предка). Что не влезло в блок, остаётся в
        мемпуле для следующего."""
        # -1 — место под coinbase, которая тоже считается транзакцией блока.
        limit = MAX_BLOCK_TRANSACTIONS - 1
        utxos = self.utxo_set()
        # Учёт одноразовых XMSS-ключей нарастает по блоку: два входа в одном
        # блоке не могут потратить один и тот же гибридный ключ.
        pq_used = self.pq_used_indices(include_mempool=False)
        valid = []
        fees = 0.0
        snapshot = list(self.mempool.transactions)
        leftover = []
        for i, tx in enumerate(snapshot):
            if len(valid) >= limit:
                leftover = snapshot[i:]  # не влезло — оставляем в мемпуле
                break
            if self.validate_transaction(tx, utxos=utxos, pq_used=pq_used):
                fees += self._tx_fee(tx, utxos)
                self._apply_tx_to_utxos(tx, utxos)
                valid.append(tx)
            # невалидная транзакция просто отбрасывается
        self.mempool.transactions = leftover

        height = len(self.blockchain.chain)
        reward = self.blockchain.block_reward(height)
        reward_tx = coinbase(miner_address, reward, fees, height=height)

        data = [reward_tx.to_dict()] + [tx.to_dict() for tx in valid]
        block = self.blockchain.add_block(data=data)
        self._rebuild_effective()  # цепочка и мемпул изменились
        return block

    def _tx_fee(self, tx: Transaction, utxos) -> float:
        total_in = sum(utxos[inp.outpoint]["amount"] for inp in tx.vin)
        return total_in - tx.total_output

    # --- Служебное -------------------------------------------------------
    @staticmethod
    def _block_transactions(block):
        data = block.data
        if isinstance(data, (list, tuple)):
            return [tx for tx in data if isinstance(tx, dict)]
        return []

    def is_valid(self) -> bool:
        return self.blockchain.is_chain_valid()

    # --- Синхронизация P2P ----------------------------------------------
    @property
    def height(self) -> int:
        return len(self.blockchain.chain)

    def _prune_mempool(self):
        """Убирает из мемпула транзакции, уже попавшие в цепочку или ставшие
        невалидными (например, их входы потрачены)."""
        in_chain = {tx["txid"] for block in self.blockchain.chain
                    for tx in self._block_transactions(block)}
        utxos = self.utxo_set()
        kept = []
        for tx in self.mempool.transactions:
            if tx.txid in in_chain:
                continue
            if self.validate_transaction(tx, utxos=utxos):
                # Применяем к набору, чтобы связанные транзакции (потомок тратит
                # неподтверждённую сдачу предка) тоже проходили проверку.
                self._apply_tx_to_utxos(tx, utxos)
                kept.append(tx)
        self.mempool.transactions = kept
        self._rebuild_effective()  # мемпул изменился — обновляем кэш

    # --- Полная проверка транзакций (безопасность) ----------------------
    def _validate_block_transactions(self, block, height, utxos,
                                     pq_used=None) -> bool:
        """
        Проверяет транзакции блока против набора UTXO (НЕ мутирует его):
          * первая транзакция — coinbase, остальные — нет;
          * каждый вход ссылается на непотраченный выход, подпись верна,
            публичный ключ соответствует адресу выхода (для гибридных выходов —
            ОБЕ подписи ECDSA+XMSS и неповторный одноразовый ключ);
          * сумма входов >= суммы выходов;
          * выпуск coinbase <= награда за блок + собранные комиссии.

        Это закрывает атаки «фальшивый coinbase» (печать монет), трату чужих
        средств и повторное использование одноразового XMSS-ключа. pq_used —
        накопитель израсходованных гибридных ключей (общий на всю цепочку).
        """
        if pq_used is None:
            pq_used = set()
        txs = self._block_transactions(block)
        if not txs:
            return True  # генезис / блок со строковыми данными
        if len(txs) > MAX_BLOCK_TRANSACTIONS:
            return False  # анти-DoS: слишком большой блок
        # Запрет дублей транзакций (двойное включение / malleability Меркла).
        txids = [Transaction.from_dict(t).txid for t in txs]
        if len(set(txids)) != len(txids):
            return False
        if not _is_coinbase_dict(txs[0]):
            return False

        fees = 0.0
        spent: set = set()
        for raw in txs[1:]:
            if _is_coinbase_dict(raw):
                return False  # только одна coinbase на блок
            tx = Transaction.from_dict(raw)
            if not tx.vin or not tx.vout or any(o.amount <= 0 for o in tx.vout):
                return False
            payload = tx.signing_payload()
            total_in = 0.0
            for inp in tx.vin:
                outpoint = inp.outpoint
                if outpoint in spent or outpoint not in utxos:
                    return False  # двойная трата / несуществующий выход
                spent.add(outpoint)
                utxo = utxos[outpoint]
                if not self._verify_input_auth(
                        inp.public_key, inp.signature, inp.pq_public_key,
                        inp.pq_signature, utxo["address"], payload, pq_used):
                    return False
                total_in += utxo["amount"]
            if total_in + _EPS < tx.total_output:
                return False
            fees += total_in - tx.total_output

        coinbase_out = Transaction.from_dict(txs[0]).total_output
        if coinbase_out > self.blockchain.block_reward(height) + fees + _EPS:
            return False  # фальшивый coinbase — печать монет
        return True

    @staticmethod
    def _apply_block_to_utxos(block, utxos) -> None:
        """Применяет блок к набору UTXO: убирает потраченные, добавляет новые."""
        for raw in BHydraNode._block_transactions(block):
            tx = Transaction.from_dict(raw)
            for inp in tx.vin:
                utxos.pop(inp.outpoint, None)
            for idx, out in enumerate(tx.vout):
                utxos[(tx.txid, idx)] = {"amount": out.amount, "address": out.address}

    def _validate_chain(self, blockchain) -> bool:
        """Полная проверка цепочки: структура (PoW/Меркл/связность) + транзакции."""
        if not blockchain.is_chain_valid():
            return False
        # Вершина цепочки не должна быть из далёкого будущего.
        if blockchain.last_block.timestamp > time.time() + MAX_FUTURE_DRIFT:
            return False
        utxos: dict = {}
        pq_used: set = set()   # израсходованные XMSS-ключи на всю цепочку
        for height, block in enumerate(blockchain.chain):
            if not self._validate_block_transactions(block, height, utxos,
                                                     pq_used=pq_used):
                return False
            self._apply_block_to_utxos(block, utxos)
        return True

    def receive_block(self, block_dict) -> bool:
        """
        Принимает одиночный блок от пира. Добавляет его, только если он
        продолжает нашу цепочку (prev = наш последний хеш), структурно валиден
        И его транзакции корректны (подписи, отсутствие двойных трат, честный
        coinbase). Возвращает False, если блок не подходит.
        """
        block = Block.from_dict(block_dict)
        last = self.blockchain.last_block
        if block.previous_hash != last.hash or block.index != self.height:
            return False
        # Время блока: не из будущего и не раньше предыдущего.
        if block.timestamp > time.time() + MAX_FUTURE_DRIFT:
            return False
        if block.timestamp < last.timestamp:
            return False
        if block.merkle_root != block._calculate_merkle_root():
            return False
        if block.hash != block.calculate_hash():
            return False
        if block.target != self.blockchain.expected_target(block.index):
            return False
        if int(block.hash, 16) > block.target:
            return False
        # Проверка транзакций против текущего набора UTXO + учёт уже
        # израсходованных гибридных ключей во всей цепочке.
        if not self._validate_block_transactions(
                block, block.index, self.utxo_set(),
                pq_used=self.pq_used_indices(include_mempool=False)):
            return False
        self.blockchain.chain.append(block)
        self._prune_mempool()
        return True

    def replace_chain(self, chain_dicts) -> bool:
        """
        Правило консенсуса: принять чужую цепочку, если у неё БОЛЬШЕ суммарной
        работы, она полностью валидна (структура + транзакции) и имеет тот же
        генезис. Возвращает True, если заменили.
        """
        candidate = Blockchain.from_dicts(chain_dicts, self.blockchain.difficulty)
        if not candidate.chain:
            return False
        if candidate.chain[0].hash != self.blockchain.chain[0].hash:
            return False  # другой генезис — это другая сеть
        if candidate.total_work <= self.blockchain.total_work:
            return False  # консенсус по суммарной работе, не по длине
        if not self._validate_chain(candidate):
            return False  # отвергаем фальшивые транзакции/coinbase
        self.blockchain = candidate
        self._prune_mempool()
        return True

    # --- Обозреватель блоков (read-only) --------------------------------
    def get_block(self, index: int):
        """Блок по высоте (dict) или None."""
        if 0 <= index < len(self.blockchain.chain):
            return self.blockchain.chain[index].to_dict()
        return None

    def _resolve_output(self, txid, index):
        """Находит выход (txid, index) в цепочке — по индексу транзакций O(1)."""
        self.utxo_set()                       # актуализировать индекс
        found = self._tx_index.get(txid)
        if found:
            vout = found[1].get("vout", [])
            if 0 <= index < len(vout):
                return vout[index]
        return None

    def find_transaction(self, txid: str):
        """Транзакция по txid вместе с номером блока, или None (индекс O(1))."""
        self.utxo_set()                       # актуализировать индекс
        found = self._tx_index.get(txid)
        if found:
            return {"transaction": found[1], "block_index": found[0]}
        return None

    def merkle_proof(self, txid: str):
        """Доказательство включения транзакции txid в её блок (для SPV).

        Возвращает dict с корнем Меркла, позицией транзакции и audit-путём —
        по нему verify_proof() подтверждает включение без всего блока — или
        None, если транзакция не найдена в цепочке.
        """
        self.utxo_set()                       # актуализировать индекс
        found = self._tx_index.get(txid)
        if found is None:
            return None
        block = self.blockchain.chain[found[0]]
        txs = self._block_transactions(block)
        for index, tx in enumerate(txs):      # скан только ОДНОГО блока
            if tx.get("txid") == txid:
                leaf = sha512d(str(tx).encode("utf-8")).hex()
                return {
                    "txid": txid,
                    "block_index": block.index,
                    "merkle_root": block.merkle_root,
                    "leaf": leaf,
                    "index": index,
                    "tx_count": len(txs),
                    "proof": block.merkle_proof(index),
                }
        return None

    def address_history(self, address: str):
        """История транзакций адреса: получено/потрачено по каждой операции.

        Кроме сумм определяет НАПРАВЛЕНИЕ и КОНТРАГЕНТА:
          * "Майнинг"    — награда за блок (coinbase) на этот адрес;
          * "Пополнение" — кто-то прислал монеты (counterparty = отправитель);
          * "Отправка"   — этот адрес отправил монеты (counterparty = получатель).
        Поля received/sent/txid/block_index сохранены для обратной совместимости.
        """
        history = []
        for block in self.blockchain.chain:
            for tx in self._block_transactions(block):
                vout = tx.get("vout", [])
                received = sum(o["amount"] for o in vout if o["address"] == address)
                sent = 0.0
                sender_addrs = []
                for inp in tx.get("vin", []):
                    ref = self._resolve_output(inp.get("txid"), inp.get("index"))
                    if ref:
                        if ref["address"] == address:
                            sent += ref["amount"]
                        else:
                            sender_addrs.append(ref["address"])
                if not (received or sent):
                    continue

                if _is_coinbase_dict(tx):
                    direction, counterparty, amount = "Майнинг", "—", received
                elif sent > received:
                    # Контрагент — получатель (выходы не на наш адрес).
                    others = [o["address"] for o in vout if o["address"] != address]
                    if others:
                        direction = "Отправка"
                        counterparty = others[0]
                        amount = sent - received                  # ушло нетто (+ комиссия)
                    else:
                        # Все выходы — на наш же адрес: перевод самому себе.
                        # Нетто из кошелька уходит только комиссия майнеру.
                        direction = "Себе"
                        counterparty = address
                        amount = sent - received                  # = комиссия
                else:
                    # Пополнение: контрагент — отправитель (вход не наш).
                    direction = "Пополнение"
                    counterparty = sender_addrs[0] if sender_addrs else "—"
                    amount = received - sent

                history.append({
                    "txid": tx["txid"],
                    "block_index": block.index,
                    "timestamp": tx.get("timestamp"),
                    "block_time": block.timestamp,  # когда блок добыт (epoch, сек)
                    "received": received,
                    "sent": sent,
                    "direction": direction,        # Майнинг / Пополнение / Отправка
                    "counterparty": counterparty,  # от кого / куда
                    "amount": amount,              # сумма операции (нетто), BHY
                })
        return history

    def address_stats(self, limit: int = None):
        """Сводка по ВСЕМ адресам цепочки — для обозревателя адресов.

        Один проход по цепочке: для каждого адреса считаются баланс (по
        непотраченным выходам), всего получено/отправлено, число транзакций
        и первый/последний блок активности. Возвращает список словарей,
        отсортированный по балансу (rich list); limit обрезает вершину.
        """
        stats = {}
        outputs = {}                      # (txid, index) -> {amount, address}

        def rec(addr):
            return stats.setdefault(addr, {
                "address": addr, "balance": 0.0,
                "received": 0.0, "sent": 0.0, "tx_count": 0,
                "first_block": None, "last_block": None})

        for block in self.blockchain.chain:
            for tx in self._block_transactions(block):
                touched = set()
                for inp in tx.get("vin", []):
                    spent = outputs.pop((inp.get("txid"), inp.get("index")),
                                        None)
                    if spent:
                        s = rec(spent["address"])
                        s["sent"] += spent["amount"]
                        s["balance"] -= spent["amount"]
                        touched.add(spent["address"])
                for index, out in enumerate(tx.get("vout", [])):
                    outputs[(tx["txid"], index)] = {
                        "amount": out["amount"], "address": out["address"]}
                    s = rec(out["address"])
                    s["received"] += out["amount"]
                    s["balance"] += out["amount"]
                    touched.add(out["address"])
                for addr in touched:
                    s = stats[addr]
                    s["tx_count"] += 1
                    if s["first_block"] is None:
                        s["first_block"] = block.index
                    s["last_block"] = block.index

        ranked = sorted(stats.values(),
                        key=lambda s: (-s["balance"], s["address"]))
        return ranked[:limit] if limit else ranked

    def mempool_info(self):
        """Сведения о неподтверждённых транзакциях (мемпул).

        Возвращает целевой блок (в какой попадут при ближайшем майнинге) и
        список транзакций: txid, сумма выходов, комиссия, число входов/выходов.
        """
        utxos = self.utxo_set()
        target_block = len(self.blockchain.chain)     # следующий добытый блок
        items = []
        for tx in self.mempool.transactions:
            d = tx.to_dict()
            # Адреса-отправители (владельцы входов) — чтобы отделить сдачу себе.
            sender_addrs = set()
            for inp in d.get("vin", []):
                ref = utxos.get((inp["txid"], inp["index"]))
                if ref:
                    sender_addrs.add(ref["address"])
            # Переведено получателям = выходы НЕ на адрес отправителя (без сдачи).
            sent = sum(o["amount"] for o in d.get("vout", [])
                       if o["address"] not in sender_addrs)
            recipients = [o["address"] for o in d.get("vout", [])
                          if o["address"] not in sender_addrs]
            try:
                fee = self._tx_fee(tx, utxos)          # вход − выход
            except (KeyError, AttributeError):
                fee = None                             # вход не найден (цепочка в мемпуле)
            items.append({
                "txid": d["txid"],
                "amount": sent,                        # переведено получателям
                "total_out": sum(o["amount"] for o in d.get("vout", [])),
                "fee": fee,
                "vin": len(d.get("vin", [])),
                "vout": len(d.get("vout", [])),
                "recipients": recipients,
                "target_block": target_block,
            })
        return {"target_block": target_block,
                "pending": len(items),
                "transactions": items}

    # --- Сохранение / загрузка ------------------------------------------
    def save(self, path: str) -> None:
        """Сохраняет цепочку и мемпул в JSON-файл."""
        with open(path, "w", encoding="utf-8") as f:
            json.dump(
                {"difficulty": self.blockchain.difficulty,
                 "chain": self.blockchain.to_dicts(),
                 "mempool": [tx.to_dict() for tx in self.mempool.transactions]},
                f, ensure_ascii=False, indent=2,
            )

    @classmethod
    def load(cls, path: str) -> "BHydraNode":
        """Загружает узел из JSON-файла с цепочкой и мемпулом."""
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        node = cls.__new__(cls)
        node.blockchain = Blockchain.from_dicts(data["chain"], data["difficulty"])
        node.mempool = TransactionPool()
        for tx_dict in data.get("mempool", []):
            node.mempool.add(Transaction.from_dict(tx_dict))
        node._rebuild_effective()
        return node


if __name__ == "__main__":
    from .wallet import generate_wallet

    node = BHydraNode(difficulty=3)
    alice = generate_wallet()
    bob = generate_wallet()

    # Алиса добывает первый блок и получает награду (coinbase-выход).
    node.mine_pending(alice.address)
    print(f"Баланс Алисы после майнинга: {node.get_balance(alice.address)} BHY")

    # Алиса переводит 10 BHY Бобу — тратит свой UTXO, остаток уходит сдачей.
    tx = node.create_transaction(alice, bob.address, amount=10, fee=0.5)
    print("Транзакция создана:", tx is not None)
    print("  входов:", len(tx.vin), "| выходов:", len(tx.vout))
    print("Принята в мемпул:", node.add_transaction(tx))

    # Боб майнит блок с этой транзакцией (получает награду + комиссию).
    node.mine_pending(bob.address)
    print(f"Баланс Алисы: {node.get_balance(alice.address)} BHY")
    print(f"Баланс Боба : {node.get_balance(bob.address)} BHY")
    print("Цепочка валидна:", node.is_valid())
