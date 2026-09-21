"""Настоящий перебор вместо майнинга по таймеру: развилка по высоте.

⚠️ ЧТО БЫЛО НЕ ТАК. Цель генезиса (сложность 3) — 4 096 хешей в среднем, доли
миллисекунды. Блок находился мгновенно, а темп задавал ТАЙМЕР в интерфейсе:
узел ждал, пока от метки вершины пройдёт `TARGET_BLOCK_TIME`. Все интервалы
выходили РОВНО целевыми, LWMA всегда видела «фактически = ожидаемо», и за
1006 блоков цель не сдвинулась ни разу.

⚠️ ГЛАВНАЯ ОПАСНОСТЬ ПРАВКИ — ВЫБРОС ЭМИССИИ. Смоделировано на настоящем
`expected_target`: убери таймер при сложности 3 — и при 5,94 Мхеш/с блоки идут
по 1450 в секунду, а LWMA догоняет их сотнями. За модельный час 313 блоков и
15 650 BHY из воздуха. Поэтому цель на высоте развилки выставляется СРАЗУ
калиброванной, а не доезжает туда пересчётом.

⚠️ И ЭТОГО МАЛО: одного калиброванного блока не хватает. Окно LWMA — последние
60 блоков, и сразу после развилки они все дофорковые, с лёгкой целью; средняя
по окну ≈ генезис, и калиброванная цель в ней тонет. Поэтому она ДЕРЖИТСЯ,
пока окно не заполнится послефорковыми блоками. Проверяется ниже отдельно —
это самая дорогая ошибка во всей задаче.
"""

import time

import pytest

from b_hydra.blockchain import (MAX_ADJUST_FACTOR, MAX_SOLVETIME_FACTOR,
                                POW_FORK_HASHRATE, POW_FORK_HEIGHT,
                                POW_FORK_TARGET, RETARGET_INTERVAL,
                                TARGET_BLOCK_TIME, Block, Blockchain,
                                _HASH_SPACE, genesis_target_for)

# Развилка и окно в тестах маленькие: иначе пришлось бы майнить тысячу блоков
# ради одной проверки. Правило от этого не меняется — оно про высоту, а не про
# конкретное число.
ОКНО = 4
ФОРК = 8
#: Калиброванная цель для тестов — заведомо лёгкая, чтобы блок искался мгновенно.
КАЛИБР = genesis_target_for(2)


def цепочка(**kw):
    kw.setdefault("difficulty", 1)
    kw.setdefault("retarget_interval", ОКНО)
    kw.setdefault("pow_fork_height", ФОРК)
    kw.setdefault("pow_fork_target", КАЛИБР)
    return Blockchain(**kw)


class Фейк:
    """Блок с ЗАДАННЫМИ целью и меткой времени — для синтетики без майнинга."""

    def __init__(self, target, timestamp):
        self.target = target
        self.timestamp = timestamp


def окно_из(целей, интервал, старт=1_700_000_000):
    """Готовая цепочка-заглушка: `целей` блоков с равным интервалом."""
    return [Фейк(t, старт + i * интервал) for i, t in enumerate(целей)]


def с_историей(цели, интервал, **kw):
    """Блокчейн, у которого `chain` подменён синтетикой."""
    bc = цепочка(**kw)
    bc.chain = окно_из(цели, интервал)
    return bc


# --- Пересчёт сложности на синтетических метках --------------------------------
def test_blocks_faster_than_target_make_the_target_smaller():
    """Быстрее цели → труднее. Это и есть смысл ретаргета."""
    цель = genesis_target_for(4)
    быстро = с_историей([цель] * 10, int(TARGET_BLOCK_TIME) // 4,
                        pow_fork_height=None)
    assert быстро.expected_target(10) < цель


def test_blocks_slower_than_target_make_the_target_bigger():
    """Медленнее цели → проще."""
    цель = genesis_target_for(4)
    медленно = с_историей([цель] * 10, int(TARGET_BLOCK_TIME) * 3,
                          pow_fork_height=None)
    assert медленно.expected_target(10) > цель


def test_blocks_exactly_on_target_do_not_move_it():
    """⚠️ ИМЕННО ЭТО И ДЕЛАЛ ТАЙМЕР: интервал ровно целевой → цель стоит.

    Тест закрепляет причину, по которой за 1006 блоков сложность не изменилась.
    """
    цель = genesis_target_for(4)
    ровно = с_историей([цель] * 10, int(TARGET_BLOCK_TIME),
                       pow_fork_height=None)
    assert ровно.expected_target(10) == цель


def test_the_step_clamp_fires_on_frozen_timestamps():
    """⚠️ Ограничитель шага ОБЯЗАТЕЛЕН.

    Череда блоков с одинаковыми метками (все интервалы = 0) без него уронила бы
    цель до 1 за пару блоков, и цепочку стало бы невозможно продолжить —
    а раз новых блоков нет, вернуть сложность назад уже нечему.
    """
    цель = genesis_target_for(4)
    замерло = с_историей([цель] * 10, 0, pow_fork_height=None)
    assert замерло.expected_target(10) == цель // MAX_ADJUST_FACTOR


def test_one_absurd_timestamp_cannot_skew_the_whole_window():
    """Вклад одного блока ограничен MAX_SOLVETIME_FACTOR × цели."""
    цель = genesis_target_for(4)
    обычные = [цель] * 10
    bc = с_историей(обычные, int(TARGET_BLOCK_TIME), pow_fork_height=None)
    спокойно = bc.expected_target(10)

    # Один блок «из далёкого будущего» — интервал в сто раз больше цели.
    bc.chain[-1].timestamp += int(TARGET_BLOCK_TIME) * 100
    перекошено = bc.expected_target(10)
    предел = int(TARGET_BLOCK_TIME) * MAX_SOLVETIME_FACTOR
    assert перекошено > спокойно                      # сдвиг есть
    assert перекошено < спокойно * предел             # но ограниченный


def test_the_target_never_gets_easier_than_the_ceiling():
    """Потолок лёгкости держится и при сколь угодно медленных блоках."""
    цель = genesis_target_for(4)
    ползёт = с_историей([цель] * 10, int(TARGET_BLOCK_TIME) * 1000,
                        difficulty=4, pow_fork_height=None)
    assert ползёт.expected_target(10) <= ползёт.genesis_target


# --- Развилка ------------------------------------------------------------------
def test_the_fork_height_gets_the_calibrated_target():
    """На высоте развилки цель ровно калиброванная — не «доезжает» пересчётом."""
    bc = цепочка()
    assert bc.expected_target(ФОРК) == КАЛИБР


def test_the_calibrated_target_is_held_until_the_window_is_post_fork():
    """⚠️ САМАЯ ДОРОГАЯ ОШИБКА ЗАДАЧИ, если её не сделать.

    Выставить цель одним блоком мало: окно LWMA ещё целиком дофорковое, средняя
    по нему ≈ генезис, и уже следующий блок вернулся бы к лёгкой цели. Проверено
    моделью: так набегало 17 900 BHY за шесть часов. Цель обязана держаться,
    пока окно не заполнится послефорковыми блоками.
    """
    bc = цепочка()
    for height in range(ФОРК, ФОРК + ОКНО):
        assert bc.expected_target(height) == КАЛИБР, height


def test_after_the_window_fills_the_retarget_takes_over_again():
    """Как только окно послефорковое — снова работает обычный LWMA."""
    bc = цепочка()
    for _ in range(ФОРК + ОКНО + 2):
        bc.add_block([])
    # Последние блоки добыты мгновенно (цель лёгкая), значит цель обязана
    # ужаться — пересчёт снова живой.
    assert bc.chain[-1].target < КАЛИБР
    assert bc.is_chain_valid()


def test_the_ceiling_after_the_fork_is_the_calibrated_target_not_genesis():
    """⚠️ Дыра, которую легко оставить: потолок остаётся генезисом.

    Генезис — это сложность 3. Уйди майнер на несколько дней, LWMA начала бы
    поднимать цель и упёрлась в неё, вернув мгновенные блоки вместе со всем
    выбросом эмиссии. После развилки потолок обязан быть калиброванным.
    """
    жёсткая = genesis_target_for(6)
    bc = с_историей([жёсткая] * (ОКНО + 2), int(TARGET_BLOCK_TIME) * 1000,
                    difficulty=1, pow_fork_height=0, pow_fork_target=жёсткая)
    # Блоки идут в тысячу раз медленнее цели — цель просится вверх.
    assert bc.expected_target(ОКНО + 2) <= жёсткая
    assert bc.expected_target(ОКНО + 2) < bc.genesis_target


# --- Совместимость со старой цепочкой ------------------------------------------
def test_below_the_fork_nothing_changes_at_all():
    """⚠️ ГЛАВНОЕ ТРЕБОВАНИЕ: 1006 существующих блоков обязаны остаться валидными.

    `is_chain_valid` сверяет target КАЖДОГО блока с `expected_target`. Значит
    достаточно доказать, что ниже развилки новая функция отдаёт РОВНО ТО ЖЕ,
    что отдавала бы без развилки вовсе.
    """
    цели = [genesis_target_for(3)] * 40
    новая = с_историей(цели, int(TARGET_BLOCK_TIME), difficulty=3,
                       pow_fork_height=1030)
    старая = с_историей(цели, int(TARGET_BLOCK_TIME), difficulty=3,
                        pow_fork_height=None)
    for height in range(len(цели)):
        assert новая.expected_target(height) == старая.expected_target(height), height


def test_a_chain_mined_under_old_rules_still_validates():
    """Цепочка, добытая до правки, проходит проверку после неё."""
    старая = Blockchain(difficulty=1, retarget_interval=ОКНО,
                        pow_fork_height=None)
    for _ in range(12):
        старая.add_block([])
    assert старая.is_chain_valid()

    # Тот же набор блоков, но узел уже знает про развилку (высоко впереди).
    новая = Blockchain.from_dicts(старая.to_dicts(), difficulty=1,
                                  retarget_interval=ОКНО,
                                  pow_fork_height=1030,
                                  pow_fork_target=POW_FORK_TARGET)
    assert новая.is_chain_valid(), "старая цепочка перестала быть валидной"


def test_a_post_fork_block_with_the_old_easy_target_is_rejected():
    """⚠️ Развилка обязана ОТВЕРГАТЬ старую лёгкую цель после своей высоты.

    Иначе правило не введено: можно было бы и дальше майнить по сложности 3.
    """
    bc = цепочка()
    while len(bc.chain) < ФОРК:
        bc.add_block([])

    # Подделываем блок на высоте развилки со СТАРОЙ (лёгкой) целью.
    предыдущий = bc.chain[-1]
    самозванец = Block(index=ФОРК, previous_hash=предыдущий.hash, data=[],
                       timestamp=предыдущий.timestamp + int(TARGET_BLOCK_TIME),
                       target=bc.genesis_target)
    самозванец.mine_block()
    bc.chain.append(самозванец)

    assert not bc.is_chain_valid(), "блок со старой целью принят после развилки"


def test_the_fork_can_be_switched_off_entirely():
    """`pow_fork_height=None` — прежнее поведение, байт в байт.

    Нужно и тестам, и цепочке, поднятой старым кодом.
    """
    bc = цепочка(pow_fork_height=None)
    for _ in range(ФОРК + ОКНО + 2):
        bc.add_block([])
    assert all(b.target != КАЛИБР for b in bc.chain[1:])
    assert bc.is_chain_valid()


# --- Калиброванная цель --------------------------------------------------------
def test_the_calibrated_target_matches_the_declared_hashrate():
    """Цель развилки = ровно столько хешей, сколько влезает в целевое время."""
    ожидается = int(POW_FORK_HASHRATE * TARGET_BLOCK_TIME)
    assert _HASH_SPACE // POW_FORK_TARGET == pytest.approx(ожидается, rel=1e-9)


def test_the_calibrated_target_is_much_harder_than_genesis():
    """⚠️ Вся затея в этом: калиброванная цель обязана быть НАМНОГО труднее.

    Сложность 3 — это 4 096 хешей. Если калибровка не на порядки труднее,
    выброс эмиссии никуда не делся.
    """
    генезис = genesis_target_for(3)
    assert POW_FORK_TARGET < генезис
    во_сколько = генезис // POW_FORK_TARGET
    assert во_сколько > 1_000_000, f"калибровка труднее генезиса лишь в {во_сколько}"


def test_the_fork_height_is_ahead_of_the_live_chain():
    """⚠️ Развилка обязана быть ВЫШЕ нынешней вершины (1006 на момент правки).

    Окажись она ниже — уже добытые блоки стали бы невалидными, и узел просто
    не запустился бы.
    """
    assert POW_FORK_HEIGHT > 1006


def test_powcalib_agrees_with_the_constant():
    """Инструмент калибровки и константа считают одно и то же."""
    from b_hydra.powcalib import target_for

    assert target_for(POW_FORK_HASHRATE) == POW_FORK_TARGET


# --- Темп после развилки -------------------------------------------------------
def test_the_post_fork_rate_converges_to_the_target_time():
    """Модель: с развилкой темп сходится к целевому, без неё — взрыв эмиссии.

    ⚠️ Это не «тест на скорость», а проверка ЭКОНОМИКИ: сравниваются два
    правила на одинаковом железе, и разница между ними — тысячи BHY.
    """
    цель_блока = int(TARGET_BLOCK_TIME)

    def прогон(с_развилкой, хешрейт, часов=6):
        bc = цепочка(difficulty=3, retarget_interval=RETARGET_INTERVAL,
                     pow_fork_height=1006 if с_развилкой else None,
                     pow_fork_target=POW_FORK_TARGET)
        bc.chain = окно_из([genesis_target_for(3)] * 1006, цель_блока)
        время = bc.chain[-1].timestamp
        конец = время + часов * 3600
        блоков = 0
        while время < конец and блоков < 5000:
            t = bc.expected_target(len(bc.chain))
            время += (_HASH_SPACE / t) / хешрейт
            bc.chain.append(Фейк(t, int(время)))
            блоков += 1
        return блоков

    ожидается = 6 * 3600 / TARGET_BLOCK_TIME          # ≈ 7,4 блока за 6 часов

    # На железе, под которое цель и калибрована, темп сходится к целевому.
    по_калибровке = прогон(True, POW_FORK_HASHRATE)
    assert по_калибровке == pytest.approx(ожидается, abs=2), по_калибровке

    # ⚠️ На железе ВТРОЕ БЫСТРЕЕ калибровки блоки идут втрое чаще — и это
    # правильно, а не дефект: пока окно не заполнилось послефорковыми блоками,
    # держится калиброванная цель, и только потом LWMA подтянет её под
    # настоящую скорость. Важно, что «втрое», а не «в тысячу раз».
    быстрее = прогон(True, POW_FORK_HASHRATE * 3)
    assert быстрее == pytest.approx(ожидается * 3, rel=0.3), быстрее

    # А вот без развилки — тот самый выброс, ради которого всё затевалось.
    без = прогон(False, POW_FORK_HASHRATE * 3)
    assert без > быстрее * 10, f"без развилки выброса нет? {без} против {быстрее}"
    assert без * 50 > 10_000, f"выброс меньше ожидаемого: {без * 50} BHY"


# --- Перебор не должен замораживать интерфейс ----------------------------------
def test_a_long_search_can_be_stopped():
    """⚠️ При настоящем переборе это стало обязательным.

    Раньше блок находился за доли миллисекунды, и «бросить работу» было нужно
    только при гонке с соседом. Теперь перебор идёт ДЕСЯТКИ МИНУТ, и без
    остановки кнопка «остановить майнинг» висела бы до конца блока, а закрытие
    окна оставляло бы поток молотить впустую.
    """
    import threading

    from b_hydra.node import BHydraNode
    from b_hydra.wallet import generate_wallet

    node = BHydraNode(difficulty=1)
    node.blockchain.pow_fork_height = 1
    node.blockchain.pow_fork_target = genesis_target_for(9)   # неподъёмная
    кошелёк = generate_wallet()

    стоп = threading.Event()
    итог = []

    def майним():
        начало = time.monotonic()
        итог.append((node.mine_pending(кошелёк.address, should_stop=стоп.is_set),
                     time.monotonic() - начало))

    поток = threading.Thread(target=майним)
    поток.start()
    time.sleep(0.5)
    стоп.set()
    поток.join(timeout=30)

    assert not поток.is_alive(), "перебор не остановился"
    блок, секунд = итог[0]
    assert блок is None, "брошенный блок не должен возвращаться"
    assert секунд < 15, f"остановка заняла {секунд:.1f} с"
    # ⚠️ Недомайненный блок в цепочку не попадает, транзакции возвращаются.
    assert len(node.blockchain.chain) == 1


def test_the_gui_only_paces_below_the_fork():
    """⚠️ Таймер обязан остаться НИЖЕ развилки, а выше — исчезнуть.

    Убери его везде — и оставшиеся до развилки блоки (цель = сложность 3,
    4 096 хешей) добудутся мгновенно, по 50 BHY каждый. Оставь его везде —
    настоящий перебор так и не начнётся.
    """
    import ast
    import os

    корень = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    исходник = open(os.path.join(корень, "b_hydra", "gui.py"),
                    encoding="utf-8").read()
    дерево = ast.parse(исходник)
    методы = {n.name for n in ast.walk(дерево) if isinstance(n, ast.FunctionDef)}
    assert "_real_pow" in методы, "в окне нет проверки высоты развилки"

    tick = исходник[исходник.index("def _mining_tick"):]
    tick = tick[:tick.index("\n    def ")]
    assert "_real_pow()" in tick, "отсчёт не спрашивает про развилку"
    assert "_next_block_due" in tick, "таймер ниже развилки потерян"


def test_mining_leaves_a_core_for_the_machine():
    """По умолчанию перебор берёт все ядра КРОМЕ ОДНОГО.

    ⚠️ Ноль сюда попасть не должен: нативный майнер понимает 0 как «реши сам»
    и берёт все ядра — ровно то, от чего бережём старый ноутбук.
    """
    import os

    from b_hydra import native_miner

    потоков = native_miner.default_threads()
    assert потоков >= 1
    assert потоков == max(1, (os.cpu_count() or 2) - 1)


def test_the_core_count_can_be_set_by_hand(monkeypatch):
    from b_hydra import native_miner

    monkeypatch.setenv(native_miner.THREADS_ENV, "2")
    assert native_miner.default_threads() == 2
    monkeypatch.setenv(native_miner.THREADS_ENV, "мусор")
    assert native_miner.default_threads() == max(1, (__import__("os").cpu_count() or 2) - 1)
