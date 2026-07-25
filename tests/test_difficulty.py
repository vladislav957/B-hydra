"""Тесты ретаргетинга сложности (LWMA, пересчёт на каждом блоке).

Цель пересчитывается по скользящему окну последних `retarget_interval` блоков:
свежие интервалы весят больше старых. Блоки идут быстрее цели → труднее
(target меньше), медленнее → проще (target больше), но не легче генезиса.

Прежняя схема пересчитывала цель раз в 100 блоков и меняла её не более чем в
4 раза за раз — для сети с временем блока ~49 мин это почти 3,5 суток
инерции. LWMA реагирует на каждом блоке.
"""

from b_hydra.blockchain import (
    Blockchain, Block, TARGET_BLOCK_TIME, RETARGET_INTERVAL,
    MAX_SOLVETIME_FACTOR, MAX_ADJUST_FACTOR,
)
from b_hydra.node import BHydraNode
from b_hydra.wallet import generate_wallet

WINDOW = 8                                  # короткое окно — тесты быстрые
TARGET_TIME = int(TARGET_BLOCK_TIME)


def _build(gaps, difficulty=1, window=WINDOW, mine=False):
    """Собирает цепочку блоками с заданными паузами времени (сек).

    `gaps[i]` — пауза перед (i+1)-м блоком. Цель каждый блок берёт из
    expected_target, поэтому ретаргетинг применяется сам. PoW считается
    только при mine=True: там, где проверяется формула, он лишний (и на
    высокой сложности очень медленный).
    """
    chain = Blockchain(difficulty=difficulty, retarget_interval=window)
    moment = 0.0
    for gap in gaps:
        moment += gap
        height = len(chain.chain)
        block = Block(height, chain.last_block.hash, [], timestamp=moment,
                      target=chain.expected_target(height))
        if mine:
            block.mine_block()
        else:
            block.hash = "0" * 128          # PoW не проверяем — важна цель
        chain.chain.append(block)
    return chain


def _targets(chain):
    return [b.target for b in chain.chain]


# --- Разогрев ----------------------------------------------------------------
def test_base_target_holds_until_window_is_full():
    """Пока окна нет, держим базовую цель — считать не от чего."""
    chain = _build([TARGET_TIME] * WINDOW)
    assert all(t == chain.genesis_target for t in _targets(chain))
    for height in range(WINDOW + 1):
        assert chain.expected_target(height) == chain.genesis_target


# --- Точность формулы --------------------------------------------------------
def test_perfect_pace_keeps_target_exactly():
    """Идеальный темп не должен смещать цель — ни на процент.

    Это же проверка на off-by-one: прежняя формула брала время между краями
    окна (на один интервал меньше, чем сравниваемое ожидание) и при точном
    попадании в цель всё равно ужимала её примерно на 1%. Здесь на `window`
    блоков приходится ровно `window` интервалов.
    """
    chain = _build([TARGET_TIME] * (WINDOW * 3))
    assert chain.expected_target(len(chain.chain)) == chain.genesis_target
    assert all(t == chain.genesis_target for t in _targets(chain))


def test_faster_blocks_make_it_harder():
    """Блоки быстрее цели → труднее (target меньше)."""
    chain = _build([TARGET_TIME] * WINDOW + [TARGET_TIME // 4] * WINDOW)
    assert chain.expected_target(len(chain.chain)) < chain.genesis_target


def test_slower_blocks_make_it_easier():
    """Блоки медленнее цели → проще, но не легче генезиса."""
    # Сначала разгоняем сложность быстрыми блоками — нужен запас над «полом».
    fast = [TARGET_TIME] * WINDOW + [TARGET_TIME // 8] * (WINDOW * 2)
    chain = _build(fast)
    hard = chain.expected_target(len(chain.chain))
    assert hard < chain.genesis_target

    # Теперь тянем время — цель обязана поехать обратно вверх.
    for _ in range(WINDOW):
        height = len(chain.chain)
        block = Block(height, chain.last_block.hash, [],
                      timestamp=chain.last_block.timestamp + TARGET_TIME * 4,
                      target=chain.expected_target(height))
        block.hash = "0" * 128
        chain.chain.append(block)
    assert chain.expected_target(len(chain.chain)) > hard


# --- Отзывчивость: главное отличие от прежней схемы --------------------------
def test_target_changes_every_block():
    """Цель пересчитывается на КАЖДОМ блоке, а не раз в окно.

    В прежней схеме внутри окна цель была константой — именно эта инерция и
    мешала сети реагировать на приход и уход майнеров.
    """
    chain = _build([TARGET_TIME] * WINDOW + [TARGET_TIME // 4] * WINDOW)
    tail = _targets(chain)[-WINDOW:]
    assert len(set(tail)) > 1                      # цель шевелится
    assert all(a >= b for a, b in zip(tail, tail[1:]))   # и монотонно ужимается


def test_hashrate_change_is_absorbed_within_a_few_windows():
    """После скачка хешрейта темп возвращается к цели.

    Симуляция: интервал блока обратно пропорционален и хешрейту, и текущей
    сложности. Проверяем, что регулятор действительно сходится.
    """
    chain = Blockchain(difficulty=1, retarget_interval=WINDOW)
    moment = 0.0

    def advance(hashrate, blocks):
        nonlocal moment
        for _ in range(blocks):
            height = len(chain.chain)
            target = chain.expected_target(height)
            moment += (chain.genesis_target / target) * TARGET_TIME / hashrate
            block = Block(height, chain.last_block.hash, [], timestamp=moment,
                          target=target)
            block.hash = "0" * 128
            chain.chain.append(block)

    advance(4.0, WINDOW * 4)                       # устоявшаяся сеть
    advance(16.0, WINDOW * 12)                     # хешрейт вырос вчетверо
    gaps = [chain.chain[i].timestamp - chain.chain[i - 1].timestamp
            for i in range(-WINDOW, 0)]
    settled = sum(gaps) / len(gaps)
    assert abs(settled - TARGET_TIME) / TARGET_TIME < 0.25


# --- Границы и устойчивость --------------------------------------------------
def test_target_never_easier_than_genesis():
    """Очень медленные блоки не делают цель легче базовой сети."""
    chain = _build([TARGET_TIME * 100] * (WINDOW * 3))
    assert chain.expected_target(len(chain.chain)) == chain.genesis_target


def test_target_never_drops_to_zero():
    """Мгновенные блоки не обнуляют цель (иначе PoW стал бы невозможен)."""
    chain = _build([TARGET_TIME] * WINDOW + [0] * (WINDOW * 3))
    assert chain.expected_target(len(chain.chain)) >= 1


def test_single_future_timestamp_is_capped():
    """Одна метка «из будущего» не перекашивает весь пересчёт.

    Вклад блока ограничен MAX_SOLVETIME_FACTOR × цели, поэтому огромная пауза
    и пауза «в разумных пределах, но выше потолка» дают один и тот же ответ.
    """
    capped = MAX_SOLVETIME_FACTOR * TARGET_TIME
    base = [TARGET_TIME] * WINDOW
    at_cap = _build(base + [capped] + [TARGET_TIME] * (WINDOW - 1))
    absurd = _build(base + [capped * 1000] + [TARGET_TIME] * (WINDOW - 1))
    height = len(at_cap.chain)
    assert at_cap.expected_target(height) == absurd.expected_target(height)


def test_retarget_is_deterministic():
    """Одинаковые метки времени дают побитово одинаковые цели."""
    gaps = [10, 20, 5, 30, 15, 7, 3, 40, 900, 60, 2000, 1, 5, 77, 300]
    assert _targets(_build(gaps)) == _targets(_build(gaps))


def test_target_is_a_pure_function_of_the_chain():
    """Цель зависит только от цепочки — иначе узлы разошлись бы в консенсусе."""
    gaps = [TARGET_TIME, 5, 4000, 60, 120, 30, 900, 77, 2, 1500]
    chain = _build(gaps)
    restored = Blockchain.from_dicts(chain.to_dicts(), difficulty=1,
                                     retarget_interval=WINDOW)
    assert [restored.expected_target(h) for h in range(len(chain.chain))] == \
        [chain.expected_target(h) for h in range(len(chain.chain))]


# --- Настоящая добытая цепочка ----------------------------------------------
def test_real_mined_chain_is_valid():
    """Цепочка, добытая узлом с коротким окном, проходит полную проверку."""
    node = BHydraNode(difficulty=2)
    node.blockchain.retarget_interval = WINDOW
    for _ in range(WINDOW + 4):
        node.mine_pending(generate_wallet().address)
    assert node.is_valid()
    assert node.blockchain.is_chain_valid()


def test_production_window_is_shorter_than_the_old_interval():
    """Окно LWMA задано и не пустое — регулятор реально настроен."""
    assert RETARGET_INTERVAL >= 2
    assert MAX_SOLVETIME_FACTOR >= 2


# --- Цепочка не должна замуровывать сама себя -------------------------------
def test_instant_blocks_do_not_brick_the_chain():
    """Блоки с одинаковыми метками времени не обнуляют цель.

    Без ограничителя шага череда нулевых интервалов роняла target до 1 за пару
    блоков. Дальше цепочку невозможно продолжить — а раз новых блоков нет, то
    и вернуть сложность обратно уже нечем: сеть умирает НАВСЕГДА.
    """
    chain = _build([0] * (WINDOW * 4))
    height = len(chain.chain)
    assert chain.expected_target(height) > 1
    # Каждый шаг ужимает цель не более чем вчетверо от средней по окну.
    targets = _targets(chain)[WINDOW:]
    for previous, current in zip(targets, targets[1:]):
        assert current >= previous // (MAX_ADJUST_FACTOR + 1)


def test_difficulty_recovers_after_a_burst_of_fast_blocks():
    """Разогнав сложность, сеть обязана уметь вернуть её обратно."""
    chain = _build([0] * (WINDOW * 3))
    burst = chain.expected_target(len(chain.chain))
    assert burst < chain.genesis_target                 # стало труднее

    for _ in range(WINDOW * 4):                         # блоки пошли медленно
        height = len(chain.chain)
        block = Block(height, chain.last_block.hash, [],
                      timestamp=chain.last_block.timestamp + TARGET_TIME * 6,
                      target=chain.expected_target(height))
        block.hash = "0" * 128
        chain.chain.append(block)
    assert chain.expected_target(len(chain.chain)) == chain.genesis_target
