"""Обход дерева XMSS: корень и authentication path обязаны не измениться.

⚠️ ЭТО КОНСЕНСУСНЫЕ ТЕСТЫ, и написаны они ДО рефакторинга обхода намеренно.
Подписи XMSS проверяются узлами (`Node._verify_input_auth`), а корень дерева
входит в гибридный адрес (`wallet.hybrid_address`). Сдвинься корень хоть на бит
— у существующих кошельков поменяются адреса, и монеты на них станут
недоступны навсегда. Сдвинься формат пути — узлы перестанут принимать траты.

Эталон здесь НАИВНЫЙ и посчитан явно: все листья выкладываются в список, корень
берётся `merkle_root(...)`, путь — `merkle_proof(...)`. Ровно так работала
первая реализация, и именно с ней сверяется любая последующая.

⚠️ Тесты гоняются на БЫСТРОМ бэкенде SHA. Это не послабление: он байт-в-байт
равен нашему чистому SHA (что отдельно закреплено
`test_pqcrypto.py::test_signatures_identical_on_pure_and_fast_sha`), а один лист
WOTS стоит 208 мс на чистом Python против 1,0 мс — разница в 215 раз, и на
чистом эти же тесты шли бы больше десяти минут. Отдельный тест ниже проверяет
совпадение на чистом бэкенде для маленького дерева.
"""

import sys
import time

import pytest

from b_hydra import hashing
from b_hydra.merkle import merkle_proof, merkle_root
from b_hydra.pqcrypto import (P256, P512, HybridWallet, MerkleSigner,
                              _wots_pk_hash, wots_keygen)

SEED = bytes(range(32))


@pytest.fixture
def fast_sha():
    """Быстрый бэкенд SHA на время теста — байты те же, время в 215 раз меньше."""
    original = hashing.is_pure()
    hashing.use_pure_sha(False)
    try:
        yield
    finally:
        hashing.use_pure_sha(original)


def _reference_leaves(height: int, seed: bytes, params) -> list:
    """Листья дерева «в лоб»: как их считала первая реализация.

    Секретный ключ WOTS выводится из seed || i, лист — хеш публичного ключа.
    Никакой оптимизации здесь быть не должно: это эталон.
    """
    leaves = []
    for i in range(1 << height):
        _sk, pk = wots_keygen(seed=seed + i.to_bytes(4, "big"), params=params)
        leaves.append(_wots_pk_hash(pk, params))
    return leaves


# --- 1. Эквивалентность корня --------------------------------------------------
@pytest.mark.parametrize("params", [P256, P512], ids=["sha256", "sha512"])
@pytest.mark.parametrize("height", [1, 2, 3, 4, 5, 6, 7, 8])
def test_root_matches_the_naive_reference(fast_sha, height, params):
    """Публичный ключ == корень Меркла над всеми листьями, посчитанный наивно.

    Это главный инвариант: корень входит в адрес кошелька.
    """
    signer = MerkleSigner(height=height, seed=SEED, params=params)
    expected = merkle_root(_reference_leaves(height, SEED, params))
    assert signer.public_key == expected


@pytest.mark.parametrize("params", [P256, P512], ids=["sha256", "sha512"])
def test_root_is_deterministic_from_the_seed(fast_sha, params):
    """Один seed — один корень. Иначе кошелёк не восстановить из резервной копии."""
    first = MerkleSigner(height=4, seed=SEED, params=params).public_key
    second = MerkleSigner(height=4, seed=SEED, params=params).public_key
    assert first == second
    other = MerkleSigner(height=4, seed=bytes(32), params=params).public_key
    assert other != first


def test_root_is_identical_on_pure_and_fast_sha():
    """Чистый SHA и быстрый бэкенд дают ОДИН корень.

    Держит право предыдущих тестов идти на быстром бэкенде: разойдись они,
    вся сверка выше проверяла бы не то дерево, которым живёт сеть.
    """
    original = hashing.is_pure()
    roots = []
    try:
        for pure in (True, False):
            hashing.use_pure_sha(pure)
            roots.append(MerkleSigner(height=3, seed=SEED).public_key)
    finally:
        hashing.use_pure_sha(original)
    assert roots[0] == roots[1]


# --- 2. Эквивалентность authentication path ------------------------------------
@pytest.mark.parametrize("height", [1, 2, 3, 4, 5, 6])
def test_auth_path_matches_merkle_proof(fast_sha, height):
    """Путь от подписи == `merkle_proof` по всем листьям, включая position.

    Проверяется КАЖДЫЙ индекс дерева: ошибка в обходе обычно проявляется не
    везде, а на краях уровней (последний лист, переход через середину).
    """
    leaves = _reference_leaves(height, SEED, P256)
    signer = MerkleSigner(height=height, seed=SEED, params=P256)
    for index in range(1 << height):
        signature = signer.sign(b"message")
        assert signature["index"] == index
        assert signature["auth"] == merkle_proof(leaves, index), \
            f"h={height}, индекс {index}"


@pytest.mark.parametrize("height", [1, 2, 3, 4])
def test_auth_path_length_is_the_height(fast_sha, height):
    """Шагов в пути ровно h — по одному соседу на уровень."""
    signer = MerkleSigner(height=height, seed=SEED)
    assert len(signer.sign(b"m")["auth"]) == height


def test_auth_path_positions_alternate_correctly(fast_sha):
    """position определяется чётностью индекса на каждом уровне.

    Перепутай стороны — `verify_proof` склеит хеши в обратном порядке и путь
    не сойдётся с корнем. Проверяем явно, а не только через verify.
    """
    height = 3
    signer = MerkleSigner(height=height, seed=SEED)
    for index in range(1 << height):
        auth = signer.sign(b"m")["auth"]
        idx = index
        for step in auth:
            assert step["position"] == ("right" if idx % 2 == 0 else "left")
            idx //= 2


# --- 3. Полный проход дерева ---------------------------------------------------
def test_every_index_of_the_tree_signs_and_verifies(fast_sha):
    """Все 2^h подписей подряд обязаны проходить проверку."""
    height = 6
    signer = MerkleSigner(height=height, seed=SEED)
    root = signer.public_key
    seen = set()
    for index in range(1 << height):
        message = f"перевод {index}".encode("utf-8")
        signature = signer.sign(message)
        assert signature["index"] not in seen, "индекс переиспользован"
        seen.add(signature["index"])
        assert MerkleSigner.verify(root, message, signature), index
    assert len(seen) == 1 << height
    assert signer.remaining == 0


def test_exhausted_tree_raises(fast_sha):
    """2^h + 1-я подпись — RuntimeError, а не тихое переиспользование ключа.

    Повтор одноразового ключа WOTS ослабляет подпись: по двум подписям одним
    ключом восстанавливаются звенья цепочек.
    """
    signer = MerkleSigner(height=2, seed=SEED)
    for _ in range(4):
        signer.sign(b"m")
    with pytest.raises(RuntimeError):
        signer.sign("ещё одна".encode("utf-8"))


def test_a_signature_of_another_message_is_refused(fast_sha):
    signer = MerkleSigner(height=3, seed=SEED)
    signature = signer.sign(b"original")
    assert MerkleSigner.verify(signer.public_key, b"tampered", signature) is False


def test_a_signature_against_another_root_is_refused(fast_sha):
    signer = MerkleSigner(height=3, seed=SEED)
    other = MerkleSigner(height=3, seed=bytes(32))
    signature = signer.sign(b"m")
    assert MerkleSigner.verify(other.public_key, b"m", signature) is False


# --- 4. Восстановление с середины ----------------------------------------------
def test_wallet_resumes_from_the_middle(fast_sha):
    """Сохранили после k подписей, загрузили — индексы не повторяются.

    Состояние обхода в файл НЕ пишется (формат `bhydra_hybrid.json` менять
    нельзя), поэтому оно обязано восстанавливаться из (seed, height, index).
    """
    wallet = HybridWallet(height=5, seed=SEED)
    root = wallet.pq_public_key
    used = []
    for i in range(7):
        _ecdsa, pq = wallet.sign(f"до {i}".encode("utf-8"))
        used.append(pq["index"])

    restored = HybridWallet.from_dict(wallet.to_dict())
    assert restored.pq_public_key == root, "корень обязан пережить загрузку"
    assert restored.address == wallet.address
    assert restored.remaining == wallet.remaining

    for i in range(7):
        message = f"после {i}".encode("utf-8")
        _ecdsa, pq = restored.sign(message)
        assert pq["index"] not in used, "индекс переиспользован после загрузки"
        used.append(pq["index"])
        assert MerkleSigner.verify(root, message, pq)
    assert used == sorted(used) and len(set(used)) == len(used)


def test_resumed_signatures_match_an_uninterrupted_run(fast_sha):
    """Подпись после перезагрузки — та же, что была бы без неё.

    Нонс WOTS детерминирован (seed || index), поэтому подпись обязана быть
    воспроизводимой. Разойдись она — состояние обхода восстанавливается
    неверно, и это не всплыло бы в проверке (путь-то мог сойтись).
    """
    straight = HybridWallet(height=4, seed=SEED)
    for i in range(5):
        straight.sign(f"m{i}".encode("utf-8"))
    expected = straight.sign(b"contested")

    interrupted = HybridWallet(height=4, seed=SEED)
    for i in range(5):
        interrupted.sign(f"m{i}".encode("utf-8"))
    reloaded = HybridWallet.from_dict(interrupted.to_dict())
    assert reloaded.sign(b"contested")[1] == expected[1]


def test_state_file_format_is_unchanged(fast_sha):
    """`to_dict` обязан отдавать ровно те же поля — старые файлы должны грузиться."""
    wallet = HybridWallet(height=3, seed=SEED)
    wallet.sign(b"m")
    state = wallet.to_dict()
    assert set(state) == {"ecdsa_private", "seed", "height", "alg", "index"}
    assert state["index"] == 1
    assert state["height"] == 3
    assert state["alg"] == "sha256"
    # Файл, записанный старой версией, обязан подняться.
    legacy = dict(state)
    restored = HybridWallet.from_dict(legacy)
    assert restored.pq_public_key == wallet.pq_public_key


# --- 5. Стоимость --------------------------------------------------------------
def _count_node_hashes(height, signatures):
    """(хешей узлов на генерацию, хешей узлов на `signatures` подписей).

    Считаем именно хеши ВНУТРЕННИХ УЗЛОВ дерева, а не время: время на быстром
    бэкенде тонет в стоимости самой WOTS-подписи, и тест по таймингу проходил бы
    даже на полной пересборке дерева. Счётчик врать не умеет.

    ⚠️ Подменяются ОБА имени — и `merkle._sha512d`, и `pqcrypto._sha512d`.
    Прежняя реализация звала функцию через `merkle_proof`, новая держит на неё
    прямую ссылку (`from .merkle import _sha512d`), и подмена только в `merkle`
    считала бы ноль — тест выглядел бы пройденным, ничего не проверяя. Именно
    так он и «прошёл» с первого раза.
    """
    from b_hydra import merkle, pqcrypto

    calls = [0]
    original = merkle._sha512d

    def counting(data):
        calls[0] += 1
        return original(data)

    merkle._sha512d = counting
    pqcrypto._sha512d = counting
    try:
        signer = MerkleSigner(height=height, seed=SEED)
        keygen = calls[0]
        for _ in range(signatures):
            signer.sign(b"m")
        return keygen, calls[0] - keygen
    finally:
        merkle._sha512d = original
        pqcrypto._sha512d = original


@pytest.mark.parametrize("height", [4, 5, 6, 7])
def test_signing_does_not_rebuild_the_whole_tree(fast_sha, height):
    """Подпись обязана стоить O(h) хешей узлов, а не O(2^h).

    Прежняя реализация звала `merkle_proof` по ВСЕМ листьям на КАЖДОЙ подписи,
    то есть пересобирала дерево целиком: замер дал ровно 2^h − 1 хеша узлов на
    подпись (15 при h=4, 31 при h=5, 63 при h=6), а полный обход дерева стоил
    2^h·(2^h − 1) — то есть O(4^h).

    Порог привязан к ВЫСОТЕ, а не к константе: именно так отличается O(h) от
    O(2^h). Замер после перехода на BDS — 0,65·h хешей на подпись (1,2 при h=4,
    2,7 при h=6, 6,5 при h=10), поэтому запас до h — двукратный, а прежняя
    реализация превышала порог уже при h=4 (15 против 4).
    """
    leaves = 1 << height
    _keygen, signing = _count_node_hashes(height, leaves)
    per_signature = signing / leaves
    assert per_signature <= height, (
        f"h={height}: {per_signature:.1f} хеша узлов на подпись при пороге "
        f"{height} — дерево пересобирается")


def test_state_does_not_grow_with_the_number_of_leaves(fast_sha):
    """Память состояния обязана расти как O(h²), а не как O(2^h).

    Прежняя реализация хранила ВСЕ секретные ключи WOTS и все листья: 1275 КБ
    при h=8, то есть 319 МБ при h=16 и около 5 ТБ при h=20. Именно память, а не
    время, делала большие деревья невозможными в принципе.
    """
    def footprint(height):
        signer = MerkleSigner(height=height, seed=SEED)
        signer.sign(b"m")
        total = 0
        seen = set()
        stack = [vars(signer)]
        while stack:
            item = stack.pop()
            if id(item) in seen:
                continue
            seen.add(id(item))
            total += sys.getsizeof(item)
            if isinstance(item, dict):
                stack.extend(item.values())
            elif isinstance(item, (list, tuple, set)):
                stack.extend(item)
        return total

    small = footprint(4)                    # 16 листьев
    large = footprint(8)                    # 256 листьев, в 16 раз больше
    assert large < small * 4, (
        f"состояние растёт вместе с деревом: h=4 {small} Б, h=8 {large} Б")


@pytest.mark.slow
def test_keygen_stays_linear_in_the_number_of_leaves(fast_sha):
    """Генерация обязана оставаться O(2^h) по листьям и не хуже.

    ⚠️ Именно O(2^h), а НЕ «секунды»: публичный ключ ЕСТЬ корень дерева над
    всеми 2^h листьями, поэтому каждый лист обязан быть посчитан хотя бы раз.
    Никакой алгоритм обхода этого не отменяет — обход касается подписей, а не
    генерации. Тест ловит регрессию вида «стало ещё и квадратично».
    """
    def cost(height):
        start = time.perf_counter()
        MerkleSigner(height=height, seed=SEED)
        return time.perf_counter() - start

    base = cost(6)
    bigger = cost(10)                       # в 16 раз больше листьев
    assert bigger < base * 40, f"{base=:.3f} {bigger=:.3f}"
