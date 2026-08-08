"""Перебор nonce на видеокарте (`gpu/bhydra_miner.cl` + `b_hydra/gpu_miner.py`).

⚠️ ГЛАВНОЕ ЗДЕСЬ — НЕ СКОРОСТЬ, А ТОЖДЕСТВЕННОСТЬ ХЕША. Ядро на OpenCL — уже
ТРЕТЬЯ реализация SHA-512 в проекте (после `sha2.py` и `cpp/bhydra_hash.hpp`),
и разойдись она с остальными хоть в бите, узел исправно находил бы «решения»,
которые сеть отвергает как неверный PoW: майнер работал бы вхолостую и выглядел
бы при этом совершенно здоровым. Поэтому дайджест, посчитанный устройством,
сверяется с Python побайтово — и на границах блока SHA-512, и на разной длине
десятичного nonce.

⚠️ ЧЕГО ЭТИ ТЕСТЫ НЕ ПРОВЕРЯЮТ: настоящей видеокарты в контейнере нет. Ядро
гоняется на CPU-устройстве OpenCL (POCL), поэтому проверена ПРАВИЛЬНОСТЬ, но не
поведение драйверов NVIDIA/AMD/Intel и не скорость на живом железе.

Без единого устройства OpenCL всё пропускается — это штатная ситуация, майнинг
идёт на процессоре.
"""

import pytest

from b_hydra import gpu_miner, hashing
from b_hydra.blockchain import Block, Blockchain

DEVICES = gpu_miner.devices()
needs_opencl = pytest.mark.skipif(not DEVICES, reason="нет устройств OpenCL")


@pytest.fixture(scope="module")
def miner():
    if not DEVICES:
        pytest.skip("нет устройств OpenCL")
    try:
        # Явно первое устройство: в контейнере это CPU, и `default()` его
        # намеренно не берёт — а проверить ядро надо.
        engine = gpu_miner.GPUMiner(device=DEVICES[0], work_size=256, per_item=4)
    except gpu_miner.GPUError as error:
        pytest.skip(f"устройство не поднялось: {error}")
    yield engine
    engine.close()


@pytest.fixture(scope="module")
def single():
    """Майнер, который за один запуск пробует РОВНО ОДИН nonce.

    Нужен для точечной сверки: обычный запуск берёт тысячи nonce сразу, и
    победителем становится любой подошедший, а не заданный. Здесь пространство
    поиска — одно число, поэтому ответ однозначен.
    """
    if not DEVICES:
        pytest.skip("нет устройств OpenCL")
    try:
        engine = gpu_miner.GPUMiner(device=DEVICES[0], work_size=1, per_item=1)
    except gpu_miner.GPUError as error:
        pytest.skip(f"устройство не поднялось: {error}")
    yield engine
    engine.close()


def _one(engine, prefix: bytes, nonce: int, target: bytes):
    """Прогнать ядро ровно на одном nonce. Срез около нуля → один запуск."""
    return engine.mine(prefix.hex(), target.hex(), nonce, seconds=1e-9)


# --- Обнаружение устройств -----------------------------------------------------
def test_devices_never_raises():
    """Список устройств — не ошибка даже без драйвера, а пустой список."""
    assert isinstance(gpu_miner.devices(), list)


@needs_opencl
def test_device_entries_are_described():
    for device in DEVICES:
        assert device["name"]
        assert device["kind"] in ("gpu", "cpu", "other")
        assert device["units"] >= 1
        assert isinstance(device["little_endian"], bool)


def test_switch_off_by_environment(monkeypatch):
    """`BHYDRA_GPU=off` обязан полностью выключать видеокарту."""
    monkeypatch.setenv(gpu_miner.GPU_ENV, "off")
    gpu_miner.reset()
    try:
        assert gpu_miner.default() is None
    finally:
        gpu_miner.reset()


@needs_opencl
def test_a_cpu_device_is_not_taken_automatically(monkeypatch):
    """Само собой берётся только настоящая видеокарта.

    CPU-устройство OpenCL считает те же хеши на тех же ядрах, только через
    драйвер, — молча подменять им наш нативный майнер незачем.
    """
    monkeypatch.delenv(gpu_miner.GPU_ENV, raising=False)
    gpu_miner.reset()
    try:
        chosen = gpu_miner.default()
        if chosen is not None:
            assert chosen.kind == "gpu"
    finally:
        gpu_miner.reset()


def test_unknown_device_name_is_refused():
    assert gpu_miner._pick("такого-устройства-нет") is None


# --- Тождественность хеша (главное) --------------------------------------------
@needs_opencl
def test_kernel_agrees_with_python_about_the_hash(single):
    """Дайджест устройства обязан совпасть с нашим SHA-512 байт-в-байт.

    Приём: целью ставится САМ хеш искомого nonce, а пространство поиска сужено
    до одного числа. Тогда подходит ровно он, и ответ сверяется точно, а не
    «нашлось хоть что-то».
    """
    prefix = "заголовок-b-hydra-".encode("utf-8")
    for nonce in (0, 1, 9, 10, 99, 100, 12345, 999999, 2 ** 32 + 7,
                  2 ** 63 + 12345):
        expected = bytes.fromhex(hashing.sha512(prefix.decode("utf-8") + str(nonce)))
        answer = _one(single, prefix, nonce, expected)
        assert answer and answer["found"], f"nonce {nonce} не найден"
        assert int(answer["nonce"]) == nonce
        assert answer["hash"] == expected.hex()


@needs_opencl
@pytest.mark.parametrize("length", [0, 1, 110, 111, 112, 126, 127, 128, 129, 200, 255])
def test_hash_matches_across_the_block_boundary(single, length):
    """Длины префикса вокруг 128 байт — там живёт набивка SHA-512.

    Именно на границе блока ошибаются в набивке: сообщению, которому не хватает
    места под 16 байт длины, нужен ВТОРОЙ блок. Ошибись ядро тут — оно считало
    бы правильно почти всегда и врало на редком заголовке.
    """
    prefix = ("a" * length).encode("utf-8")
    for nonce in (0, 7, 123456789, 99999999999999999999 % (2 ** 64)):
        expected = bytes.fromhex(hashing.sha512("a" * length + str(nonce)))
        answer = _one(single, prefix, nonce, expected)
        assert answer and answer["found"], f"префикс {length}, nonce {nonce}"
        assert int(answer["nonce"]) == nonce
        assert answer["hash"] == expected.hex()


@needs_opencl
def test_nonce_is_appended_as_a_decimal_string(single):
    """nonce приписывается ДЕСЯТИЧНОЙ строкой, как `str(nonce)` в Python.

    Возьми ядро двоичный nonce — хеши совпадали бы сами с собой, но не с сетью,
    и каждый добытый блок отвергался бы всеми. Проверяем на числах, у которых
    десятичная и двоичная записи заведомо разные, и на границах числа цифр.
    """
    prefix = b"x"
    for nonce in (9, 10, 99, 100, 256, 1000, 65536, 10 ** 19):
        expected = bytes.fromhex(hashing.sha512("x" + str(nonce)))
        answer = _one(single, prefix, nonce, expected)
        assert answer and answer["found"]
        assert answer["hash"] == expected.hex()


@needs_opencl
def test_a_nonce_that_misses_the_target_is_not_reported(single):
    """Обратная половина: не подошёл — значит не найден.

    Ядро, отвечающее «нашёл» на всё, дало бы блоки, которые сеть отвергает.
    Цель — недостижимый ноль, пространство поиска — один nonce.
    """
    answer = _one(single, b"x", 12345, b"\x00" * 64)
    assert answer is not None
    assert answer["found"] is False


@needs_opencl
def test_selftest_passes_on_a_working_device(miner):
    assert miner.selftest() is True


# --- Поиск и срезы -------------------------------------------------------------
@needs_opencl
def test_finds_a_real_solution(miner):
    """Найденный nonce обязан давать хеш, реально проходящий порог."""
    prefix = "блок-".encode("utf-8")
    target = b"\x00\x00" + b"\xff" * 62
    answer = miner.mine(prefix.hex(), target.hex(), 0, seconds=20.0)
    assert answer and answer["found"]
    digest = bytes.fromhex(hashing.sha512("блок-" + str(answer["nonce"])))
    assert digest.hex() == answer["hash"]
    assert digest <= target


@needs_opencl
def test_unreachable_target_returns_the_next_nonce(miner):
    """Не нашли за срез — отдаём, откуда продолжать, и сколько попыток сделали.

    Без `next_nonce` перебор начинался бы каждый срез с нуля и не двигался бы
    никогда.
    """
    answer = miner.mine(b"z".hex(), (b"\x00" * 64).hex(), 0, seconds=0.2)
    assert answer and answer["found"] is False
    assert answer["next_nonce"] > 0
    assert answer["attempts"] > 0


@needs_opencl
def test_slice_returns_control(miner):
    """Срез обязан заканчиваться примерно вовремя: на нём держится право
    бросить блок, когда сосед прислал свой."""
    import time

    started = time.monotonic()
    miner.mine(b"z".hex(), (b"\x00" * 64).hex(), 0, seconds=0.3)
    assert time.monotonic() - started < 10.0


@needs_opencl
def test_a_wrong_target_length_is_refused(miner):
    assert miner.mine(b"z".hex(), "00" * 10, 0, seconds=1.0) is None


# --- Встраивание в майнинг блока -----------------------------------------------
@needs_opencl
def test_mines_a_block_through_the_normal_path(miner):
    """`mine_block(miner=…)` принимает GPU-майнер без единой правки.

    Интерфейс намеренно тот же, что у `native_miner.NativeMiner`, поэтому
    вместе с ним достаются даром и срезы, и проверка результата.
    """
    chain = Blockchain()
    previous = chain.chain[-1]
    block = Block(1, previous.hash, [], target=previous.target)
    found = block.mine_block(miner=miner)
    assert found == block.calculate_hash()
    assert bytes.fromhex(found) <= block.target_bytes()


@needs_opencl
def test_a_whole_chain_mined_on_the_device_is_valid(miner):
    chain = Blockchain()
    for _ in range(3):
        chain.add_block([], miner=miner)
    assert chain.is_chain_valid()
    assert len(chain.chain) == 4


def test_a_lying_miner_is_caught():
    """Майнеру НЕ ВЕРЯТ: хеш пересчитывается своим кодом.

    Это защита именно от чужого движка — ядро на видеокарте пишется на другом
    языке и исполняется драйвером, который мы не контролируем. Молчаливый откат
    на Python скрыл бы поломку до момента, когда сеть начнёт отвергать блоки.
    """
    class Liar:
        def selftest(self):
            return True

        def mine(self, prefix, target, start, seconds=None):
            return {"found": True, "nonce": 777, "hash": "00" * 64,
                    "attempts": 1}

    chain = Blockchain()
    previous = chain.chain[-1]
    block = Block(1, previous.hash, [], target=previous.target)
    with pytest.raises(ValueError):
        block.mine_block(miner=Liar())


def test_a_miner_that_never_answers_falls_back_to_python():
    """Движок, который ничего не вернул, не должен ронять майнинг.

    Драйвер видеокарты может отвалиться посреди работы (перегрев, сброс,
    выгрузка модуля). Это не повод потерять блок: перебор молча продолжается на
    процессоре, и результат обязан быть настоящим.
    """
    class Silent:
        def selftest(self):
            return True

        def mine(self, prefix, target, start, seconds=None):
            return None

    chain = Blockchain()
    previous = chain.chain[-1]
    block = Block(1, previous.hash, [], target=previous.target)
    found = block.mine_block(miner=Silent())
    assert found == block.calculate_hash()
    assert bytes.fromhex(found) <= block.target_bytes()


# --- midstate ------------------------------------------------------------------
def test_midstate_reproduces_the_whole_hash():
    """Состояние + хвост обязаны давать тот же хеш, что разовый вызов.

    На этом стоит вся экономия ядра: целые блоки заголовка сжимаются один раз
    на хосте. Разойдись midstate с разовым хешем — устройство считало бы
    правильный SHA-512 не от того сообщения.
    """
    from b_hydra import sha2

    for prefix in ("", "a" * 127, "a" * 128, "a" * 129, "заголовок-"):
        hasher = sha2.Sha512()
        hasher.update(prefix.encode("utf-8"))
        state, tail, length = hasher.midstate()
        assert len(state) == 8
        assert len(tail) < 128
        assert length == len(prefix.encode("utf-8"))
        # Досчитываем хвост поверх состояния — как это делает ядро.
        clone = sha2.Sha512()
        clone._h = list(state)
        clone._buffer = bytearray(tail)
        clone._length = length
        clone.update(b"12345")
        assert clone.hexdigest() == hashing.sha512(prefix + "12345")


def test_kernel_source_is_shipped():
    """Ядро — часть репозитория: без файла GPU-майнер не соберётся."""
    import os

    assert os.path.exists(gpu_miner._KERNEL_PATH)
    with open(gpu_miner._KERNEL_PATH, "r", encoding="utf-8") as stream:
        source = stream.read()
    assert "__kernel void bhydra_mine" in source
    # Константы SHA-512: первая и последняя из FIPS 180-4.
    assert "0x428a2f98d728ae22UL" in source
    assert "0x6c44198c4a475817UL" in source
