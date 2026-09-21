"""Нативная генерация листьев XMSS: те же байты, что у чистого Python.

Публичный ключ XMSS ЕСТЬ корень дерева над всеми 2^h листьями, поэтому каждый
лист обязан быть посчитан хотя бы раз — обход BDS удешевил ПОДПИСЬ, а не
генерацию. Лист стоит 1073 хеша (P256) или 2097 (P512), и на чистом Python
дерево высоты 16 строится пять часов. В C++ уехали ТОЛЬКО листья: они —
чистая функция от (seed, index), у неё нет состояния и расходиться негде.
Обход, путь включения и расписание treehash остались в Python одной копией.

⚠️ ГЛАВНОЕ ЗДЕСЬ — НЕ СКОРОСТЬ, А КОРЕНЬ. Он же публичный ключ, он же часть
гибридного адреса (версия `0x2f`). Ядро, считающее листья хоть немного иначе,
дало бы ДРУГОЙ адрес — и монеты ушли бы туда, откуда их не достанет никто.
Поэтому здесь проверяется не «подпись валидна», а побайтовое совпадение и
равенство корней с нативным путём и без него.

Тесты ПРОПУСКАЮТСЯ, если библиотека не собрана, — как тесты Bluetooth без
адаптера. Собрать:

    g++ -O2 -std=c++17 -pthread -shared -fPIC -I cpp \
        -o libbhydra_xmss.so cpp/bhydra_xmss_lib.cpp
"""

import hashlib

import pytest

from b_hydra import hashing, native_xmss, pqcrypto as pq

_library = native_xmss.default()

нужна_библиотека = pytest.mark.skipif(
    _library is None, reason="libbhydra_xmss не собрана")


@pytest.fixture(autouse=True)
def свежий_бэкенд():
    """Каждый тест начинает с чистого выбора бэкенда."""
    pq.reset_backend()
    yield
    pq.reset_backend()


def дерево(height, seed, params, нативно):
    """Дерево, построенное с нативным путём или без него."""
    pq.reset_backend()
    if не_нативно := not нативно:
        pq._NATIVE[params["name"]] = None       # бэкенд отключён точечно
    signer = pq.MerkleSigner(height=height, seed=seed, params=params)
    assert не_нативно or pq.backend(params) == "native (свой C++)"
    return signer


# --- Байты ---------------------------------------------------------------------
@нужна_библиотека
@pytest.mark.parametrize("params", [pq.P256, pq.P512], ids=["sha256", "sha512"])
@pytest.mark.parametrize("index", [0, 1, 2, 7, 255, 65535])
def test_a_native_leaf_matches_pure_python_byte_for_byte(params, index):
    """⚠️ ГЛАВНЫЙ ТЕСТ: лист обязан совпасть до последнего бита."""
    seed = bytes(range(32))
    батч = native_xmss.leaves(_library, seed, params["name"], index, 1, threads=1)
    assert батч is not None
    assert батч[0] == pq._pure_leaf(seed, index, params)


@нужна_библиотека
@pytest.mark.parametrize("seed", [b"", b"\x00" * 32, b"\xff" * 64, bytes(range(40))],
                         ids=["пустой", "нули", "64Б", "40Б"])
def test_the_seed_length_does_not_change_the_answer(seed):
    """Длина seed произвольна: он склеивается, а не кладётся в поле."""
    батч = native_xmss.leaves(_library, seed, "sha256", 0, 2, threads=1)
    assert батч == [pq._pure_leaf(seed, i, pq.P256) for i in (0, 1)]


@нужна_библиотека
def test_a_batch_starts_where_it_was_asked_to():
    """⚠️ Смещение проверяется ОТДЕЛЬНО, и не зря.

    Считай библиотека всегда от нуля, тест с `start=0` этого бы не заметил, а
    дерево вышло бы из 2^h одинаковых листьев — и корень был бы стабильным,
    то есть поломка выглядела бы как исправность.
    """
    seed = b"\x05" * 32
    батч = native_xmss.leaves(_library, seed, "sha256", 100, 4, threads=1)
    assert батч == [pq._pure_leaf(seed, i, pq.P256) for i in range(100, 104)]


@нужна_библиотека
@pytest.mark.parametrize("threads", [1, 2, 3, 8])
def test_the_thread_count_never_changes_the_bytes(threads):
    """Разбиение по потокам — деталь исполнения, а не часть схемы.

    ⚠️ Здесь это важнее, чем кажется: у майнера параллельный перебор НАМЕРЕННО
    недетерминирован (годится любой валидный nonce), и на генезисе это уже
    раскалывало сеть. У листьев такой свободы нет вовсе — лист ровно один.
    """
    seed = b"\x06" * 32
    эталон = [pq._pure_leaf(seed, i, pq.P256) for i in range(16)]
    assert native_xmss.leaves(_library, seed, "sha256", 0, 16, threads) == эталон


# --- Корень и адрес ------------------------------------------------------------
@нужна_библиотека
@pytest.mark.parametrize("params", [pq.P256, pq.P512], ids=["sha256", "sha512"])
@pytest.mark.parametrize("height", [1, 2, 3, 4])
def test_the_root_is_the_same_with_and_without_the_native_path(params, height):
    """⚠️ САМОЕ ВАЖНОЕ: корень — это публичный ключ, он меняться не вправе."""
    seed = bytes([height]) * 32
    assert (дерево(height, seed, params, True).public_key
            == дерево(height, seed, params, False).public_key)


@нужна_библиотека
def test_the_hybrid_address_is_unchanged():
    """Гибридный адрес (0x2f) выводится из корня — значит тоже не меняется."""
    from b_hydra.wallet import Wallet, hybrid_address
    ecdsa = Wallet.from_private_hex("11" * 32)
    seed = b"\x0a" * 32
    адреса = {hybrid_address(ecdsa.public_key_bytes,
                             дерево(3, seed, pq.P256, n).public_key)
              for n in (True, False)}
    assert len(адреса) == 1


@нужна_библиотека
def test_signatures_from_a_natively_built_tree_verify():
    """Дерево собрано нативно — подписи проверяются обычным кодом."""
    signer = дерево(3, b"\x0b" * 32, pq.P256, True)
    for k in range(6):
        сообщение = f"перевод {k} BHY".encode()
        подпись = signer.sign(сообщение)
        assert pq.MerkleSigner.verify(signer.public_key, сообщение, подпись)
        assert not pq.MerkleSigner.verify(signer.public_key,
                                          "другое".encode(), подпись)


@нужна_библиотека
def test_the_traversal_state_survives_the_native_path():
    """⚠️ Обход BDS остался в Python — проверяем, что он не поехал.

    Путь включения строится инкрементально, и ошибка в нём даёт подписи,
    которые выглядят нормально, но не сходятся с корнем. Поймать это можно
    только подписав ПОДРЯД, а не один раз.
    """
    signer = дерево(4, b"\x0c" * 32, pq.P256, True)
    assert all(pq.MerkleSigner.verify(signer.public_key, f"m{k}".encode(),
                                      signer.sign(f"m{k}".encode()))
               for k in range(signer.n_leaves))


# --- Отказы и откаты -----------------------------------------------------------
@нужна_библиотека
def test_a_lying_core_is_rejected(monkeypatch):
    """⚠️ Проверено МУТАЦИЕЙ: ядро, промахнувшееся на бит, не включается.

    Это и есть смысл сверки. Молча принять такое ядро значило бы выдавать
    кошельки с адресом, от которого ни у кого нет ключа.
    """
    честные = native_xmss.leaves

    def врущие(library, seed, alg, start, count, threads=None):
        батч = честные(library, seed, alg, start, count, threads)
        if батч is None:
            return None
        порча = bytearray(батч[0]); порча[0] ^= 1        # ровно один бит
        return [bytes(порча)] + батч[1:]

    monkeypatch.setattr(native_xmss, "leaves", врущие)
    pq.reset_backend()
    assert pq._native_for(pq.P256) is None
    assert pq.backend(pq.P256) == "pure-python"


@нужна_библиотека
def test_a_core_that_ignores_the_offset_is_rejected(monkeypatch):
    """Ядро, всегда считающее от нуля, обязано отсеяться сверкой."""
    честные = native_xmss.leaves
    monkeypatch.setattr(
        native_xmss, "leaves",
        lambda library, seed, alg, start, count, threads=None:
            честные(library, seed, alg, 0, count, threads))
    pq.reset_backend()
    assert pq._native_for(pq.P256) is None


def test_without_the_library_everything_still_works(monkeypatch):
    """Не собрана — не беда: дерево строится чистым Python.

    Тест НЕ пропускается без библиотеки: это ровно тот случай, который он и
    проверяет.
    """
    monkeypatch.setattr(native_xmss, "default", lambda: None)
    pq.reset_backend()
    signer = pq.MerkleSigner(height=2, seed=b"\x0d" * 32)
    assert pq.backend(pq.P256) == "pure-python"
    сообщение = "без ускорителя".encode()
    assert pq.MerkleSigner.verify(signer.public_key, сообщение,
                                  signer.sign(сообщение))


@нужна_библиотека
def test_a_failure_midway_falls_back_instead_of_losing_the_tree(monkeypatch):
    """⚠️ Отказ ПОСРЕДИ работы не имеет права уронить генерацию.

    Драйвер отвалился, память кончилась — досчитать обязан Python. Уронить
    генерацию значило бы потерять кошелёк из-за сбоя ускорителя, без которого
    всё прекрасно работает.
    """
    эталон = дерево(3, b"\x0e" * 32, pq.P256, False).public_key

    pq.reset_backend()
    assert pq._native_for(pq.P256) is not None     # сверка проходит до порчи
    честные = native_xmss.leaves
    состояние = {"вызовов": 0}

    def падает(library, seed, alg, start, count, threads=None):
        состояние["вызовов"] += 1
        return None if count > 1 else честные(library, seed, alg, start, count,
                                              threads)

    monkeypatch.setattr(native_xmss, "leaves", падает)
    assert pq.MerkleSigner(height=3, seed=b"\x0e" * 32).public_key == эталон
    assert состояние["вызовов"] > 0


@нужна_библиотека
def test_an_unknown_mode_is_refused_rather_than_guessed():
    """Чужой режим — отказ, а не «посчитаем как-нибудь»."""
    assert native_xmss.leaves(_library, b"\x01" * 32, "sha384", 0, 1) is None
    assert native_xmss.leaf_size(_library, "sha384") == 0


@нужна_библиотека
def test_a_seed_longer_than_the_library_allows_is_refused():
    """Слишком длинный seed отвергается, а не переполняет буфер."""
    assert native_xmss.leaves(_library, b"\x01" * 4096, "sha256", 0, 1) is None


# --- Хеши самой библиотеки ------------------------------------------------------
@нужна_библиотека
def test_the_library_checks_its_own_hashes():
    """Библиотека проверяет себя векторами NIST до первого листа."""
    assert _library.bhydra_xmss_selftest() == 1


@нужна_библиотека
@pytest.mark.parametrize("length", [0, 1, 55, 56, 63, 64, 65, 111, 112, 127, 128, 200])
def test_the_cpp_sha256_agrees_with_ours_on_block_boundaries(length):
    """⚠️ SHA-256 написан РАДИ XMSS — его в проекте раньше не было вовсе.

    Границы блока проверяются отдельно: короткое сообщение идёт одним блоком и
    ошибку в дополнении не задевает. Именно на 55/56 и 111/112 байтах такие
    ошибки и живут.
    """
    данные = bytes(range(256))[:length] * (1 + length // 256)
    данные = данные[:length]
    assert hashing.sha256_bytes(данные) == hashlib.sha256(данные).digest()


@нужна_библиотека
def test_the_leaf_size_matches_the_parameter_set():
    """Размер листа библиотеки совпадает с `n` из набора параметров."""
    assert native_xmss.leaf_size(_library, "sha256") == pq.P256["n"]
    assert native_xmss.leaf_size(_library, "sha512") == pq.P512["n"]


# --- Прогресс -------------------------------------------------------------------
@нужна_библиотека
def test_progress_is_reported_and_ends_at_the_last_leaf():
    """Отчёт о прогрессе: окно не должно выглядеть замёрзшим минутами."""
    отчёты = []
    pq.reset_backend()
    signer = pq.MerkleSigner(height=3, seed=b"\x0f" * 32,
                             on_progress=lambda сделано, всего: отчёты.append(
                                 (сделано, всего)))
    assert отчёты, "прогресс не сообщался ни разу"
    assert отчёты[-1] == (signer.n_leaves, signer.n_leaves)
    assert [с for с, _ in отчёты] == sorted(с for с, _ in отчёты)
