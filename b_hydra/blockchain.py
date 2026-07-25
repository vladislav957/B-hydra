"""
B-hydra — одноранговая электронная денежная система (P2P electronic cash).

Эталонная реализация блокчейна согласно белой книге B-hydra:

  * Хеширование               : SHA-512 (двойной SHA-512 в дереве Меркла);
  * Консенсус                 : Proof-of-Work (хеш ≤ target);
  * Сложность                 : ретаргетинг LWMA на КАЖДОМ блоке — цель
                                пересчитывается по скользящему окну так, чтобы
                                средний темп возвращался к целевому времени
                                блока (больше майнеров → блоки идут быстрее
                                цели → цель тут же ужимается);
  * Начальная награда         : 50 BHY;
  * Интервал халвинга         : 310 000 блоков;
  * Максимальная эмиссия      : 31 000 000 BHY
                                (310 000 * 50 * 2 = 31 000 000).

Пример использования (как в README):

    from .blockchain import Blockchain

    blockchain = Blockchain()
    blockchain.add_block(data="Пример транзакции")
    print(blockchain.chain)
"""

import json
import time

# Позволяет запускать этот файл напрямую (двойной клик / IDLE / python
# b_hydra/blockchain.py), а не только как часть пакета: добавляем корень
# проекта в путь поиска и объявляем имя пакета, чтобы работали импорты «from .».
if __name__ == "__main__" and __package__ in (None, ""):
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    __package__ = "b_hydra"

from . import hashing

# --- Параметры сети B-hydra (белая книга) -------------------------------------
# Идентификатор сети: вшивается в подпись транзакции и защищает от replay —
# транзакция, подписанная для этой сети, недействительна в другой сети/форке.
CHAIN_ID = "b-hydra-mainnet"
INITIAL_REWARD = 50            # Начальная награда за блок, BHY
HALVING_INTERVAL = 310_000     # Каждые 310 000 блоков награда делится пополам
MAX_SUPPLY = 31_000_000        # Максимальная эмиссия монет
DECIMALS = 8                   # Делимость монеты: наименьшая единица = 1e-8 BHY
DEFAULT_FEE = 0.0001           # Стандартная низкая комиссия перевода, BHY
                               # (как в крипте: маленькая, идёт майнеру блока)

# Награда за блок строго 50 BHY и делится пополам каждые HALVING_INTERVAL блоков
# (как халвинг Bitcoin). Время блока подобрано так, чтобы выпуск монет тянулся
# примерно до TARGET_END_YEAR — майнеры получают награду до ~3000 года.
GENESIS_YEAR = 2026
# Год окончания эмиссии. Пасхалка: 3000 — это год, в котором Фрай проснулся
# из криокамеры в «Футураме» (заморозился 31.12.1999, оттаял 31.12.2999). 🚀
TARGET_END_YEAR = 3000
SECONDS_PER_YEAR = 365.25 * 24 * 3600


def _effective_halvings():
    """Сколько халвингов проходит, прежде чем награда станет меньше 1e-DECIMALS."""
    halvings = 0
    while round(INITIAL_REWARD / (2 ** halvings), DECIMALS) > 0:
        halvings += 1
    return halvings


# Высота блока, на которой выпуск новых монет прекращается.
MINING_END_HEIGHT = _effective_halvings() * HALVING_INTERVAL
# Время блока подобрано так, чтобы майнинг закончился примерно в TARGET_END_YEAR.
BLOCK_TIME_SECONDS = ((TARGET_END_YEAR - GENESIS_YEAR) * SECONDS_PER_YEAR
                      / MINING_END_HEIGHT)
TARGET_BLOCK_TIME = BLOCK_TIME_SECONDS

# --- Ретаргетинг сложности: LWMA, пересчёт на КАЖДОМ блоке -------------------
# Раньше цель пересчитывалась раз в 100 блоков по времени всего окна (схема
# Bitcoin). Для маленькой сети это слишком инерционно: при TARGET_BLOCK_TIME
# ≈ 49 мин окно длиной 100 блоков — почти 3,5 суток. Если половина майнеров
# ушла, блоки идут вдвое медленнее, и следующего пересчёта приходится ждать
# неделю; если ушли почти все — можно не дождаться вовсе.
#
# Теперь используется LWMA (linear weighted moving average) — тот же подход,
# что у Monero и Zcash, и ровно по той же причине. Цель пересчитывается на
# КАЖДОМ блоке по скользящему окну последних RETARGET_INTERVAL блоков, причём
# свежие интервалы весят больше старых (вес i у i-го блока окна). Реакция
# почти мгновенная, а формула остаётся детерминированной и целочисленной.
#
# Сложность по-прежнему следует за РАБОТОЙ, а не за числом «участников»:
# больше майнеров → блоки находятся быстрее → цель ужимается. Считать головы
# нельзя — адрес ничего не стоит, и новый адрес на каждый блок раздувал бы
# счётчик бесплатно (а общий адрес у картеля — занижал).
RETARGET_INTERVAL = 60         # длина скользящего окна LWMA, блоков
# Вклад одного блока в окно ограничен: иначе одна метка времени «из будущего»
# перекосила бы весь пересчёт. Отрицательных интервалов не бывает — время
# блока не может идти назад (проверяется в is_chain_valid).
MAX_SOLVETIME_FACTOR = 6       # интервал блока учитывается не более 6×цели
# Ограничитель шага за один блок — ОБЯЗАТЕЛЕН. Без него череда блоков с
# одинаковыми метками времени (все интервалы = 0) роняет цель до 1 за пару
# блоков, и цепочка становится недобываемой НАВСЕГДА: новых блоков нет, а
# значит нечему и вернуть сложность обратно. С ограничителем цель ужимается
# не более чем вчетверо за блок и так же быстро отыгрывает назад, когда блоки
# начинают отставать от цели.
MAX_ADJUST_FACTOR = 4          # цель меняется не более чем в 4 раза за блок

# --- Proof-of-Work как в Bitcoin: порог-цель (target) -------------------------
# Хеш блока (512-битное число) должен быть НЕ больше target. Чем меньше target,
# тем труднее найти блок — строго пропорционально (ожидаемое число попыток
# = 2^512 / target). Сложность растёт с числом майнеров: target = генезис / N,
# то есть чем больше майнеров, тем труднее; чем меньше — тем проще.
_HASH_SPACE = 1 << 512                 # всё пространство значений SHA-512
DEFAULT_DIFFICULTY = 4                 # генезис: эквивалент N ведущих нулей hex


def genesis_target_for(difficulty: int) -> int:
    """Целевой порог генезиса по «числу ведущих нулей hex» (база сети)."""
    return _HASH_SPACE >> (4 * difficulty)


# Лимиты безопасности (анти-DoS / манипуляции).
MAX_BLOCK_TRANSACTIONS = 5000      # максимум транзакций в блоке
MAX_MEMPOOL_TRANSACTIONS = 50000   # вместимость мемпула (неподтверждённых тх)
MAX_FUTURE_DRIFT = 2 * 60 * 60 # блок не может быть из будущего более чем на 2 ч

# Фиксированная метка времени генезис-блока. Благодаря ей все узлы с одинаковой
# базовой сложностью получают ИДЕНТИЧНЫЙ генезис — иначе сеть не сможет
# синхронизироваться (у каждого узла была бы своя «нулевая точка»).
GENESIS_TIMESTAMP = 0.0


def sha512d(data: bytes) -> bytes:
    """Двойной SHA-512 — основной хеш B-hydra."""
    return hashing.double_sha512(data)


# Дерево Меркла — единая реализация в merkle.py (корень + доказательства
# включения). Здесь используется тот же корень, что и лёгкими клиентами.
from .merkle import merkle_root, merkle_proof  # noqa: E402,F401


class Block:
    """Блок цепочки B-hydra."""

    def __init__(self, index, previous_hash, data, timestamp=None, nonce=0,
                 target=None):
        self.index = index
        self.previous_hash = previous_hash
        self.data = data
        self.timestamp = timestamp if timestamp is not None else time.time()
        self.nonce = nonce
        # target — порог PoW: хеш (как число) должен быть ≤ target.
        self.target = target if target is not None else genesis_target_for(DEFAULT_DIFFICULTY)
        self.merkle_root = self._calculate_merkle_root()
        self.hash = self.calculate_hash()

    # «Сложность» в привычных единицах (число ведущих нулей hex) — для показа.
    @property
    def difficulty(self) -> int:
        return max(0, 129 - len(f"{self.target:x}"))

    # Проделанная работа = ожидаемое число хешей для такого target.
    @property
    def work(self) -> int:
        return _HASH_SPACE // self.target

    def _tx_leaves(self):
        """Листья дерева Меркла блока (хеши транзакций в порядке блока)."""
        transactions = self.data if isinstance(self.data, (list, tuple)) else [self.data]
        return [sha512d(str(tx).encode("utf-8")) for tx in transactions]

    def _calculate_merkle_root(self):
        """Строит корень Меркла по транзакциям блока."""
        return merkle_root(self._tx_leaves())

    def merkle_proof(self, index: int):
        """Доказательство включения транзакции №index в этот блок (SPV-путь).

        Путь проверяется verify_proof() против merkle_root из заголовка —
        лёгкому клиенту не нужны остальные транзакции блока.
        """
        return merkle_proof(self._tx_leaves(), index)

    def calculate_hash(self):
        """SHA-512 от содержимого заголовка блока (включая target)."""
        header = (
            f"{self.index}{self.previous_hash}{self.merkle_root}"
            f"{self.timestamp}{self.target:x}{self.nonce}"
        )
        return hashing.sha512(header)

    def mine_block(self):
        """Proof-of-Work: перебираем nonce, пока хеш (как число) не станет ≤ target.

        Запоминает число перебранных хешей в self.mining_attempts — это и есть
        проделанная майнером работа за найденный блок.
        """
        self.mining_attempts = 1          # первый хеш уже посчитан в __init__
        while int(self.hash, 16) > self.target:
            self.nonce += 1               # перебираем nonce…
            self.hash = self.calculate_hash()   # …и пересчитываем SHA-512
            self.mining_attempts += 1
        return self.hash

    def to_dict(self):
        return {
            "index": self.index,
            "previous_hash": self.previous_hash,
            "data": self.data,
            "timestamp": self.timestamp,
            "nonce": self.nonce,
            "target": f"{self.target:x}",
            "difficulty": self.difficulty,   # производное (для отображения)
            "work": self.work,               # производное (ожидаемое число хешей)
            "merkle_root": self.merkle_root,
            "hash": self.hash,
        }

    @classmethod
    def from_dict(cls, data):
        """Восстанавливает блок из словаря без повторного майнинга."""
        block = cls.__new__(cls)
        block.index = data["index"]
        block.previous_hash = data["previous_hash"]
        block.data = data["data"]
        block.timestamp = data["timestamp"]
        block.nonce = data["nonce"]
        block.target = int(data["target"], 16)
        block.merkle_root = data["merkle_root"]
        block.hash = data["hash"]
        return block

    def __repr__(self):
        return f"<Block #{self.index} hash={self.hash[:16]}…>"


class Blockchain:
    """Цепочка блоков B-hydra с PoW-консенсусом и халвингом награды."""

    def __init__(self, difficulty=DEFAULT_DIFFICULTY,
                 retarget_interval=RETARGET_INTERVAL):
        self.difficulty = difficulty                      # генезис-сложность (база)
        self.genesis_target = genesis_target_for(difficulty)
        # Скользящее окно LWMA — параметр сети (одинаков для всех узлов).
        # По умолчанию RETARGET_INTERVAL; меньшие значения удобны в тестах.
        # Пока высота не превысила окно, держится базовая цель генезиса.
        self.retarget_interval = max(1, retarget_interval)
        self.chain = [self.create_genesis_block()]

    def create_genesis_block(self):
        """Детерминированный генезис-блок сети B-hydra (одинаков для всех узлов)."""
        genesis = Block(0, "0" * 128, "B-hydra Genesis Block",
                        timestamp=GENESIS_TIMESTAMP, target=self.genesis_target)
        genesis.mine_block()
        return genesis

    @property
    def last_block(self):
        return self.chain[-1]

    @property
    def total_work(self) -> int:
        """Суммарная работа цепочки = Σ (2^512 / target) по всем блокам.

        Консенсус выбирает цепочку с НАИБОЛЬШЕЙ работой, а не самую длинную:
        иначе атакующий мог бы обогнать честную сеть длинной цепочкой
        «дешёвых» блоков низкой сложности.
        """
        return sum(block.work for block in self.chain)

    @staticmethod
    def _miner_of(block):
        """Адрес майнера блока (получатель coinbase) или None."""
        data = block.data
        if isinstance(data, list) and data and isinstance(data[0], dict):
            vout = data[0].get("vout")
            if vout and isinstance(vout[0], dict):
                return vout[0].get("address")
        return None

    def distinct_miners(self, upto=None):
        """Множество разных майнеров в цепочке (до блока upto, не включая)."""
        blocks = self.chain[:upto] if upto is not None else self.chain
        miners = set()
        for block in blocks:
            miner = self._miner_of(block)
            if miner:
                miners.add(miner)
        return miners

    def participants(self, upto=None) -> int:
        """Число разных майнеров (участников) в цепочке."""
        return len(self.distinct_miners(upto=upto))

    # --- Сложность: LWMA-ретаргетинг на каждом блоке --------------------
    def expected_target(self, height):
        """
        Целевой порог PoW для блока на высоте `height` — детерминирован из
        цепочки (по меткам времени), поэтому любой узел получает тот же результат.

        Пересчёт идёт на КАЖДОМ блоке по скользящему окну последних
        `retarget_interval` блоков (LWMA): берутся их интервалы между метками
        времени, свежие весят больше старых, и средняя цель окна
        масштабируется отношением «фактически / ожидаемо». Быстрее цели →
        target уменьшается (труднее), медленнее → увеличивается (проще).

        Пока окна нет (высота не больше его длины), держим базовую цель
        генезиса — иначе не от чего считать.

        Вклад одного блока ограничен MAX_SOLVETIME_FACTOR × цели: одна метка
        «из будущего» не должна перекашивать весь пересчёт. Результат не легче
        генезиса (база сети) и не нулевой. Вся арифметика целочисленная —
        чтобы результат был побитово одинаков на всех платформах.
        """
        if height == 0:
            return self.genesis_target
        window = self.retarget_interval
        # Окно — блоки [height-window; height-1], а для первого из них нужен
        # ещё предыдущий блок-якорь, чтобы получить его интервал.
        if height <= window:
            return self.genesis_target

        target_time = int(TARGET_BLOCK_TIME)
        max_solvetime = MAX_SOLVETIME_FACTOR * target_time

        weighted_time = 0        # Σ i × интервал_i — свежие блоки весомее
        target_sum = 0           # Σ целей блоков окна
        for i in range(1, window + 1):
            block = self.chain[height - window + i - 1]
            previous = self.chain[height - window + i - 2]
            # Ровно `window` интервалов на `window` блоков: прежняя формула
            # брала время между краями окна, а это на один интервал меньше,
            # чем сравниваемое ожидание (та же историческая опечатка, что и
            # в Bitcoin, — систематический сдвиг около 1%).
            solvetime = int(block.timestamp) - int(previous.timestamp)
            solvetime = max(0, min(solvetime, max_solvetime))
            weighted_time += i * solvetime
            target_sum += block.target

        # При идеальном темпе (каждый интервал равен цели) weighted_time в
        # точности равен знаменателю, и цель остаётся средней по окну.
        denominator = target_time * window * (window + 1) // 2
        new_target = target_sum * weighted_time // (window * denominator)

        # Шаг за один блок ограничен относительно средней цели окна: иначе
        # блоки с одинаковыми метками времени (все интервалы = 0) обнулили бы
        # цель, и цепочку стало бы невозможно продолжить — а раз новых блоков
        # нет, то и сложность назад уже ничто не вернёт.
        average_target = target_sum // window
        new_target = max(average_target // MAX_ADJUST_FACTOR,
                         min(average_target * MAX_ADJUST_FACTOR, new_target))
        # Не легче генезиса (это «потолок» лёгкости) и не нулевая.
        return max(1, min(new_target, self.genesis_target))

    def block_reward(self, height):
        """Награда за блок с учётом халвинга (Bitcoin-подобная схема).

        Строго 50 BHY, и каждые HALVING_INTERVAL блоков делится пополам.
        Округляется до делимости монеты (DECIMALS) и становится 0, как только
        опускается ниже наименьшей единицы — это конец эмиссии (~TARGET_END_YEAR).
        """
        halvings = height // HALVING_INTERVAL
        if halvings >= 64:
            return 0.0
        return round(INITIAL_REWARD / (2 ** halvings), DECIMALS)

    @property
    def total_supply(self):
        """Текущая эмиссия = сумма наград за добытые блоки."""
        supply = sum(self.block_reward(b.index) for b in self.chain[1:])
        return min(supply, MAX_SUPPLY)

    def add_block(self, data):
        """Создаёт, майнит и добавляет новый блок в цепочку.

        Порог-цель определяется LWMA-ретаргетингом (expected_target).
        """
        height = len(self.chain)
        new_block = Block(
            index=height,
            previous_hash=self.last_block.hash,
            data=data,
            target=self.expected_target(height),
        )
        new_block.mine_block()
        self.chain.append(new_block)
        return new_block

    def is_chain_valid(self):
        """Проверяет целостность цепочки, PoW и корректность сложности."""
        for i in range(1, len(self.chain)):
            current = self.chain[i]
            previous = self.chain[i - 1]

            # Корень Меркла должен соответствовать данным блока: иначе
            # подмена транзакций осталась бы незамеченной.
            if current.merkle_root != current._calculate_merkle_root():
                return False
            if current.hash != current.calculate_hash():
                return False
            if current.previous_hash != previous.hash:
                return False
            # Порог должен совпадать с детерминированно ожидаемым (ретаргетинг)…
            if current.target != self.expected_target(i):
                return False
            # …и хеш — реально удовлетворять порогу (PoW).
            if int(current.hash, 16) > current.target:
                return False
            # Время блока не может идти назад (защита от манипуляций временем).
            if current.timestamp < previous.timestamp:
                return False
        return True

    def to_dicts(self):
        return [b.to_dict() for b in self.chain]

    def to_json(self):
        return json.dumps(self.to_dicts(), ensure_ascii=False, indent=2)

    @classmethod
    def from_dicts(cls, chain_dicts, difficulty=DEFAULT_DIFFICULTY,
                   retarget_interval=RETARGET_INTERVAL):
        """Восстанавливает блокчейн из списка словарей (например, из файла).

        retarget_interval нужно передавать тем же, с каким цепочка майнилась,
        иначе expected_target/is_chain_valid посчитают восстановленную цепочку
        невалидной (по умолчанию — сетевой RETARGET_INTERVAL).
        """
        blockchain = cls.__new__(cls)
        blockchain.difficulty = difficulty
        blockchain.genesis_target = genesis_target_for(difficulty)
        blockchain.retarget_interval = max(1, retarget_interval)
        blockchain.chain = [Block.from_dict(d) for d in chain_dicts]
        return blockchain


if __name__ == "__main__":
    # Демонстрация: участники сети (майнеры) помогают блокчейну и получают
    # награду в BHY. В блоках хранятся ТОЛЬКО адреса B-hydra и суммы — без имён.
    from .node import BHydraNode
    from .transaction import NULL_TXID
    from .wallet import generate_wallet

    node = BHydraNode(difficulty=3)
    miner_a = generate_wallet()
    miner_b = generate_wallet()

    # Майнеры добывают блоки и получают награду на свой адрес.
    node.mine_pending(miner_a.address)
    node.mine_pending(miner_b.address)

    # Перевод между адресами (попадёт в следующий блок).
    tx = node.create_transaction(miner_a, miner_b.address, amount=10, fee=0.5)
    node.add_transaction(tx)
    node.mine_pending(miner_a.address)

    def short(addr):
        return addr if len(addr) <= 18 else addr[:10] + "…" + addr[-6:]

    print("Содержимое блоков (только адреса B-hydra и суммы):\n")
    for block in node.blockchain.chain:
        print(f"Блок #{block.index}  (сложность {block.difficulty})")
        txs = block.data if isinstance(block.data, list) else []
        if not txs:
            print("    — генезис —")
        for t in txs:
            out = t["vout"]
            if t["vin"] and t["vin"][0]["txid"] == NULL_TXID:
                print(f"    награда: → {short(out[0]['address'])}  +{out[0]['amount']} BHY")
            else:
                moves = ", ".join(f"→ {short(o['address'])} {o['amount']} BHY" for o in out)
                print(f"    перевод: {len(t['vin'])} вход(ов)  {moves}")
        print()

    print("Балансы по адресам:")
    print(f"  {short(miner_a.address)}: {node.get_balance(miner_a.address)} BHY")
    print(f"  {short(miner_b.address)}: {node.get_balance(miner_b.address)} BHY")
    print(f"\nВсего выпущено  : {node.blockchain.total_supply} BHY")
    print(f"Цепочка валидна : {node.is_valid()}")
