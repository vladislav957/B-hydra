"""Нативный майнер: тот же перебор, но на всех ядрах.

Перебор nonce — единственное место, где Python считает САМ и много: миллионы
SHA-512 подряд, и всё в один поток из-за GIL. `cpp/bhydra_miner.cpp` делает то
же самое на C++ и на всех ядрах.

Главное здесь — НЕ скорость. Главное, что:

  * блок, найденный внешней программой, проходит проверку НАШИМ кодом;
  * майнер, который соврал, ловится сразу и громко, а не превращается в
    отвергнутый сетью блок;
  * несобранный C++ ничего не ломает — майнинг просто идёт на Python.

Тесты пропускаются, если нет компилятора.
"""

import json
import os
import shutil
import subprocess
import time

import pytest

from b_hydra import native_miner
from b_hydra.blockchain import Block, Blockchain, genesis_target_for
from b_hydra.node import BHydraNode
from b_hydra.wallet import generate_wallet

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(ROOT, "cpp", "bhydra_miner.cpp")

COMPILER = None
for candidate in ("g++", "clang++"):
    if shutil.which(candidate):
        COMPILER = candidate
        break


@pytest.fixture(scope="module")
def binary(tmp_path_factory):
    if COMPILER is None:
        pytest.skip("нет компилятора C++")
    out = str(tmp_path_factory.mktemp("miner") / "bhydra_miner")
    result = subprocess.run(
        [COMPILER, "-O2", "-std=c++17", "-pthread", "-Wall", "-Wextra",
         "-I", os.path.join(ROOT, "cpp"), "-o", out, SOURCE],
        capture_output=True, text=True, timeout=600)
    assert result.returncode == 0, result.stderr
    # Предупреждений быть не должно — правило для всего нативного кода проекта.
    assert result.stderr.strip() == "", result.stderr
    return out


@pytest.fixture
def miner(binary):
    """Готовый мост к собранному майнеру (кэш `default()` не трогаем)."""
    return native_miner.NativeMiner(binary, slice_seconds=2.0)


def _block(**kwargs):
    options = dict(index=1, previous_hash="0" * 128, data=["tx"],
                   timestamp=1000.0, target=genesis_target_for(3))
    options.update(kwargs)
    return Block(**options)


# --- Сам бинарник --------------------------------------------------------------
def test_native_miner_passes_its_own_vectors(miner):
    """SHA-512 совпал с вектором, midstate — с разовым хешем, hex — туда-обратно."""
    assert miner.selftest() is True


def test_unknown_command_is_refused(binary):
    result = subprocess.run([binary, "выдумка"], capture_output=True, text=True)
    assert result.returncode == 2


def test_bad_arguments_are_refused(binary):
    """Мусор вместо hex — понятная ошибка, а не случайный блок."""
    result = subprocess.run([binary, "mine", "не-hex", "ff" * 64, "0", "1", "1"],
                            capture_output=True, text=True, timeout=60)
    assert "error" in json.loads(result.stdout)

    result = subprocess.run([binary, "mine", "abcd", "ff", "0", "1", "1"],
                            capture_output=True, text=True, timeout=60)
    assert "error" in json.loads(result.stdout)   # цель не 64 байта


# --- Совпадение с Python -------------------------------------------------------
def test_native_hash_matches_python_byte_for_byte(miner):
    """Найденный nonce обязан давать ТОТ ЖЕ хеш, что и наш Python.

    Иначе «найденный» блок не пройдёт проверку у собственного узла — а это
    единственное, ради чего майнер вообще нужен.
    """
    block = _block()
    answer = miner.mine(block.header_prefix().encode("utf-8").hex(),
                        block.target_bytes().hex(), 0, seconds=10)
    assert answer is not None and answer.get("found"), answer
    block.nonce = int(answer["nonce"])
    assert block.calculate_hash() == answer["hash"]
    assert int(answer["hash"], 16) <= block.target


def test_block_mined_natively_is_accepted_by_our_own_code(miner):
    """Сквозной путь: блок найден C++, проверен Python, лёг в цепочку."""
    block = _block(target=genesis_target_for(3))
    found = block.mine_block(miner=miner)
    assert found == block.calculate_hash()
    assert int(found, 16) <= block.target
    assert block.mining_attempts > 0 and block.hashrate() > 0


def test_whole_chain_can_be_mined_natively(miner):
    """Цепочка из нескольких блоков остаётся валидной."""
    chain = Blockchain(difficulty=2)
    for _ in range(3):
        assert chain.add_block(data=["tx"], miner=miner) is not None
    assert chain.is_chain_valid() is True


# --- Безопасность: майнеру не верят на слово -----------------------------------
def _fake_miner(tmp_path, answer):
    script = tmp_path / "liar"
    script.write_text("#!/bin/sh\n"
                      "if [ \"$1\" = selftest ]; then echo '{\"ok\": true}'; exit 0; fi\n"
                      f"echo '{json.dumps(answer)}'\n", encoding="utf-8")
    script.chmod(0o755)
    return native_miner.NativeMiner(str(script))


def test_lying_miner_is_caught(tmp_path):
    """Внешняя программа вернула неверный блок — это ОШИБКА, и громкая.

    Молча откатываться на Python нельзя: сломанный майнер иначе всплыл бы
    только как отвергнутые сетью блоки, и искать причину пришлось бы долго.
    """
    liar = _fake_miner(tmp_path, {"found": True, "nonce": 7, "hash": "de" * 64,
                                  "attempts": 1, "seconds": 0.1})
    with pytest.raises(ValueError, match="неверный блок"):
        _block().mine_block(miner=liar)


def test_miner_that_misses_the_target_is_caught(tmp_path):
    """Хеш настоящий, но порогу не удовлетворяет — тоже отказ.

    Такой блок сеть отвергнет, поэтому принимать его нельзя даже при том, что
    сам хеш посчитан честно.
    """
    block = _block(target=genesis_target_for(6))    # заведомо труднее
    block.nonce = 0
    honest_hash = block.calculate_hash()
    assert int(honest_hash, 16) > block.target      # он не проходит порог
    liar = _fake_miner(tmp_path, {"found": True, "nonce": 0,
                                  "hash": honest_hash, "attempts": 1,
                                  "seconds": 0.1})
    with pytest.raises(ValueError, match="неверный блок"):
        block.mine_block(miner=liar)


# --- Отсутствие бинарника ничего не ломает -------------------------------------
def test_missing_binary_falls_back_to_python(monkeypatch):
    monkeypatch.setenv(native_miner.MINER_ENV, "/несуществующий/bhydra_miner")
    native_miner.reset()
    try:
        assert native_miner.default() is None
        block = _block(target=genesis_target_for(2))
        assert block.mine_block() == block.calculate_hash()
    finally:
        native_miner.reset()


def test_native_miner_can_be_switched_off(monkeypatch, binary):
    """`BHYDRA_MINER=off` — чтобы сравнить скорость и обойти сбойный бинарник."""
    monkeypatch.setenv(native_miner.MINER_ENV, "off")
    native_miner.reset()
    try:
        assert native_miner.find() is None
        assert native_miner.default() is None
    finally:
        native_miner.reset()


def test_a_binary_that_fails_selftest_is_not_used(tmp_path, monkeypatch):
    """Чужой файл с тем же именем не должен молча стать майнером."""
    impostor = tmp_path / "bhydra_miner"
    impostor.write_text("#!/bin/sh\necho не json\n", encoding="utf-8")
    impostor.chmod(0o755)
    monkeypatch.setenv(native_miner.MINER_ENV, str(impostor))
    native_miner.reset()
    try:
        assert native_miner.find() == str(impostor)   # найден…
        assert native_miner.default() is None         # …но не принят
    finally:
        native_miner.reset()


# --- Прерывание работает и через нативный путь ---------------------------------
def test_native_mining_can_be_abandoned(miner):
    """Всё, ради чего переписывался цикл, обязано пережить уход в C++.

    Нативный майнер работает СРЕЗАМИ по времени именно поэтому: отдай ему
    управление насовсем — и узел снова оглох бы на время майнинга.
    """
    fast = native_miner.NativeMiner(miner.path, slice_seconds=0.2)
    block = _block(target=1)                       # недостижимая цель
    started = time.monotonic()
    assert block.mine_block(should_stop=lambda: True, miner=fast) is None
    assert time.monotonic() - started < 30
    assert block.mining_attempts > 0


def test_progress_is_reported_from_the_native_path(miner):
    fast = native_miner.NativeMiner(miner.path, slice_seconds=0.2)
    block = _block(target=1)
    seen = []
    calls = {"n": 0}

    def stop():
        calls["n"] += 1
        return calls["n"] >= 2                    # два среза и хватит

    block.mine_block(should_stop=stop, miner=fast,
                     on_progress=lambda attempts, rate: seen.append((attempts, rate)))
    assert seen and all(a > 0 and r > 0 for a, r in seen)


# --- Скорость (мягко: тест бегает на разном железе) ----------------------------
def test_native_miner_uses_more_than_one_core(miner):
    """Смысл нативного майнера — параллелизм, которого у Python нет из-за GIL."""
    single = miner.benchmark(seconds=1.0, threads=1)
    many = miner.benchmark(seconds=1.0, threads=0)   # 0 = все ядра
    assert single > 0 and many > 0
    if (os.cpu_count() or 1) > 1:
        assert many > single * 1.3, f"1 поток {single:.0f}/с, все {many:.0f}/с"


def test_native_miner_is_faster_than_pure_python(miner):
    """Против режима по умолчанию (чистый SHA на Python) разрыв огромный."""
    from b_hydra import hashing

    previous = hashing.is_pure()
    hashing.use_pure_sha(True)
    try:
        block = _block(target=1)
        started = time.monotonic()
        block.mine_block(max_attempts=3000)
        python_rate = block.mining_attempts / (time.monotonic() - started)
    finally:
        hashing.use_pure_sha(previous)
    assert miner.benchmark(seconds=1.0) > python_rate * 10
