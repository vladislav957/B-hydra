"""README обязан описывать ТОТ КОД, который лежит рядом.

Документация — единственное, что видит человек до запуска, и разойтись с кодом
она может молча: тесты её не трогают, а читатель об ошибке узнаёт, только когда
у него ничего не заработало. Здесь закреплены ровно те утверждения README,
которые уже успели разойтись с кодом хотя бы раз.

⚠️ Каждый факт проверяется ПО КОДУ, а не сверкой README с самим собой: строка
«одинарный SHA-512» ничего не стоит, если рядом не посчитан настоящий хеш
настоящего блока. Поэтому ниже сначала считается значение, и лишь потом
проверяется, что README называет его правильно.
"""

import os
import re

import pytest

from b_hydra import hashing
from b_hydra.merkle import leaf_hash
from b_hydra.node import BHydraNode
from b_hydra.transaction import Transaction
from b_hydra.wallet import generate_wallet

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="module")
def readme():
    with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as handle:
        return handle.read()


@pytest.fixture(scope="module")
def mined_block():
    """Настоящий добытый блок: факты берутся из него, а не из описания."""
    node = BHydraNode(difficulty=1)
    node.mine_pending(generate_wallet().address)
    return node.blockchain.chain[-1]


# --- Что чем хешируется --------------------------------------------------------
def test_the_block_header_is_a_single_sha512(mined_block, readme):
    """Заголовок блока — ОДИНАРНЫЙ SHA-512.

    README долго говорил просто «SHA-256/512», не различая конструкции, хотя
    они разные: двойной SHA-512 от того же заголовка даёт другое значение, и
    сторонний майнер, написанный по такому описанию, не добыл бы ни одного
    принятого блока.
    """
    data = mined_block.header_prefix() + str(mined_block.nonce)
    assert hashing.sha512(data) == mined_block.hash
    raw = data.encode("utf-8") if isinstance(data, str) else data
    assert hashing.double_sha512(raw).hex() != mined_block.hash

    assert "Заголовок блока (PoW) | одинарный SHA-512" in readme


def test_the_txid_is_a_single_sha512_not_a_double_one(mined_block, readme):
    """⚠️ `txid` — ОДИНАРНЫЙ SHA-512, а не двойной.

    Это тот случай, ради которого файл и написан: в присланном разборе README
    txid стоял в одной строке с листьями Меркла как «двойной SHA-512». Листья
    действительно двойные, а txid — нет, и перепутать их особенно легко, потому
    что оба живут в одном блоке. По неверному описанию внешний кошелёк считал
    бы чужие идентификаторы и не нашёл бы в цепочке ни одной своей транзакции.
    """
    transaction = Transaction.from_dict(mined_block.data[0])
    payload = transaction.signing_payload()
    raw = payload.encode("utf-8") if isinstance(payload, str) else payload

    assert hashing.sha512(payload) == transaction.txid
    assert hashing.double_sha512(raw).hex() != transaction.txid

    assert "`txid` (и то, что подписывается) | одинарный SHA-512" in readme
    # И ни одной строки, где txid назван двойным.
    for line in readme.splitlines():
        if "txid" in line and "двойной" in line:
            pytest.fail(f"README снова называет txid двойным: {line}")


def test_the_merkle_leaves_are_a_double_sha512(mined_block, readme):
    """Листья дерева Меркла — ДВОЙНОЙ SHA-512, в отличие от txid."""
    txid = Transaction.from_dict(mined_block.data[0]).txid
    assert leaf_hash(txid) == hashing.double_sha512(str(txid).encode("utf-8"))
    assert leaf_hash(txid) != hashing.sha512_bytes(str(txid).encode("utf-8"))

    assert "Листья и узлы дерева Меркла | двойной SHA-512" in readme


def test_sha256_stays_out_of_consensus(readme):
    """SHA-256 в консенсусе не участвует — так и написано.

    Проверяется по коду: в модулях, из которых складывается цепочка, вызовов
    SHA-256 быть не должно вовсе. `certgen`/`pqcrypto`/`rsa` — не консенсус,
    они здесь и не проверяются.
    """
    consensus = ("blockchain.py", "transaction.py", "wallet.py", "merkle.py",
                 "node.py", "hashcash.py")
    for name in consensus:
        with open(os.path.join(ROOT, "b_hydra", name), encoding="utf-8") as handle:
            source = handle.read()
        code = "\n".join(line for line in source.splitlines()
                         if not line.lstrip().startswith("#"))
        assert "sha256" not in code.lower(), f"SHA-256 попал в консенсус: {name}"

    assert "SHA-256 в консенсусе не участвует" in readme


# --- Правило выбора ветви ------------------------------------------------------
def test_the_readme_does_not_promise_the_longest_chain_rule(readme):
    """⚠️ Побеждает наибольшая РАБОТА, а не длина.

    Различие не косметическое: правило длины позволило бы вытеснить честную
    цепочку множеством лёгких блоков с заниженной сложностью. README обещал
    именно длину — то есть описывал сеть слабее той, что написана.
    """
    import inspect

    from b_hydra import node as node_module

    source = inspect.getsource(node_module.BHydraNode.replace_chain)
    assert "total_work" in source, "правило выбора ветви изменилось — правь README"

    assert "самой длинной валидной цепочки" not in readme
    assert "наибольшей накопленной работы" in readme


def test_work_is_not_the_same_thing_as_length():
    """И это не только слова в README: КОРОТКАЯ тяжёлая цепочка весит больше.

    Без этой проверки фраза «по работе, а не по длине» остаётся фразой: если бы
    `total_work` считалась пропорционально числу блоков, оба правила совпадали
    бы, и разницу нельзя было бы заметить вовсе.

    Собираются две НАСТОЯЩИЕ цепочки: короткая с трудными блоками и заметно
    более длинная с лёгкими. Работа первой обязана оказаться больше.
    """
    from b_hydra.blockchain import Blockchain

    hard = Blockchain(difficulty=3)
    for _ in range(2):
        hard.add_block([])

    easy = Blockchain(difficulty=1)
    while len(easy.chain) <= len(hard.chain) * 2:
        easy.add_block([])

    assert len(easy.chain) > len(hard.chain), "лёгкая цепочка вышла не длиннее"
    assert easy.total_work < hard.total_work, (
        "длинная лёгкая цепочка накопила больше работы, чем короткая трудная — "
        "правило по работе перестало отличаться от правила по длине")


# --- Команды, которые человек скопирует ----------------------------------------
def test_every_python_command_in_the_readme_points_at_a_real_file(readme):
    """⚠️ САМОЕ ОБИДНОЕ: `python maing.py` — файла с таким именем нет.

    В корне лежит `manig.py`, буквы переставлены. Ниже по тексту имя было
    написано верно, поэтому человек получал две разные команды и обе выглядели
    опечаткой. Здесь проверяются ВСЕ упомянутые скрипты разом.
    """
    missing = []
    for name in sorted(set(re.findall(r"python ([\w./-]+\.py)", readme))):
        if not os.path.exists(os.path.join(ROOT, name)):
            missing.append(name)
    assert not missing, f"README зовёт несуществующие файлы: {missing}"


def test_the_release_assets_are_named_as_the_workflow_builds_them(readme):
    """Имена файлов в релизе README обязан брать у сборки, а не выдумывать.

    `B-hydra-Core.exe` в релизе нет: сборки получают суффикс системы, иначе они
    конфликтовали бы в одном релизе. Человек шёл по ссылке и не находил файла,
    названного в README.
    """
    with open(os.path.join(ROOT, ".github/workflows/build.yml"),
              encoding="utf-8") as handle:
        workflow = handle.read()
    assets = set(re.findall(r"asset: (\S+)", workflow))
    assert assets, "в сборке не нашлось имён файлов релиза"

    for asset in assets:
        assert asset in readme, f"README не упоминает файл релиза {asset}"
    assert "скачайте `B-hydra-Core.exe`" not in readme


# --- Числа, которые устаревают сами --------------------------------------------
def test_the_test_count_is_not_wildly_understated(readme):
    """README обещал 102 теста при почти тысяче — цифра работала против проекта.

    Точное число тут намеренно не сверяется: оно устареет с первым же новым
    тестом. Проверяется только, что обещание не занижено в разы.
    """
    claimed = re.search(r"Автотесты \(более (\d+) тестов", readme)
    assert claimed, "в README пропала строка с числом тестов"
    promised = int(claimed.group(1))

    actual = 0
    for name in os.listdir(os.path.join(ROOT, "tests")):
        if name.startswith("test_") and name.endswith(".py"):
            with open(os.path.join(ROOT, "tests", name), encoding="utf-8") as handle:
                actual += len(re.findall(r"^def test_", handle.read(), re.M))

    assert actual >= promised, \
        f"README обещает больше {promised} тестов, а их {actual}"
    assert promised >= actual // 2, \
        f"README занижает число тестов: обещано >{promised}, есть {actual}"


def test_the_difficulty_description_matches_the_retarget_in_code(readme):
    """Сложность пересчитывается на КАЖДОМ блоке (LWMA), а не раз в окно.

    README описывал прежнюю схему Bitcoin — «каждые RETARGET_INTERVAL блоков».
    Её сменили именно потому, что при времени блока ~49 минут она давала около
    3,5 суток инерции.
    """
    from b_hydra.blockchain import RETARGET_INTERVAL

    assert "LWMA на КАЖДОМ блоке" in readme
    assert f"RETARGET_INTERVAL = {RETARGET_INTERVAL}" in readme
    assert "Каждые\nRETARGET_INTERVAL блоков target пересчитывается" not in readme


# --- Обрывки текста ------------------------------------------------------------
def test_no_sentence_is_cut_off_mid_word(readme):
    """«Надёжность и безопасность : Испол» — предложение обрывалось на полуслове.

    Ровно тот абзац, который должен убеждать, что проекту можно доверять.
    """
    assert "Надёжность и безопасность : Испол\n" not in readme
    assert "Reliability and Security: Execution Scalability" not in readme
