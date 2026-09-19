"""Обновление B-hydra Core: проверка, подпись релиза, замена файла.

⚠️ ГЛАВНОЕ, ЧТО ЗДЕСЬ ПРОВЕРЯЕТСЯ, — ЧТО ОБНОВЛЯТЕЛЬ ОТКАЗЫВАЕТСЯ СТАВИТЬ.
Механизм обновления в кошельке — это дверь, через которую на машину человека
приезжает исполняемый код. Обычные тесты «скачалось и поставилось» проверяют
как раз ту половину, которая и так работает; опасна вторая — та, где файл
подменили. Поэтому почти весь файл про отказы: неверная подпись, подменённый
файл, откат на старую версию, понижение до HTTP, запуск из исходников.

Сеть здесь НЕ ИСПОЛЬЗУЕТСЯ: GitHub подменяется поддельным, иначе тесты зависели
бы от чужого сервера и от того, какой релиз выпущен сегодня.
"""

import hashlib
import json
import os
import re
import stat

import pytest

from b_hydra import release, release_key, rsa, updater
from b_hydra.updater import UpdateError
from b_hydra.version import VERSION, format_version, is_newer, parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="module")
def key():
    """Ключ релизов. 2048 бит — минимум, который принимает наш rsa."""
    return rsa.generate(2048)


class FakeGitHub:
    """Поддельный GitHub: отдаёт ровно то, что ему велели.

    Считает обращения — по ним видно, что при отказе мы не качаем сборку.
    """

    def __init__(self, version="v9.9.9", key=None, binary="новая сборка".encode("utf-8"),
                 manifest_version=None, break_signature=False,
                 break_binary=False, with_manifest=True, raw_manifest=None):
        self.version = version
        self.binary = binary
        self.calls = []
        name = updater.asset_name()
        base = f"https://github.com/{updater.REPO}/releases/download/{version}"

        self.assets = {name: f"{base}/{name}"}
        self.bodies = {
            f"{base}/{name}": binary if not break_binary else binary + "хвост".encode("utf-8"),
        }

        if with_manifest and key is not None:
            digest = hashlib.sha512(binary).hexdigest()
            manifest = {"version": format_version(manifest_version or version),
                        "files": {name: digest}}
            raw = raw_manifest or updater.manifest_payload(manifest)
            signature = rsa.sign_pss(key, updater.manifest_payload(manifest))
            if break_signature:
                signature = bytes(signature[:-1]) + bytes([signature[-1] ^ 0x01])
            for item, payload in ((updater.MANIFEST_NAME, raw),
                                  (updater.SIGNATURE_NAME, signature)):
                self.assets[item] = f"{base}/{item}"
                self.bodies[f"{base}/{item}"] = payload

        self.release_json = json.dumps({
            "tag_name": version,
            "body": "что нового: всё",
            "published_at": "2026-09-01T00:00:00Z",
            "html_url": f"https://github.com/{updater.REPO}/releases/tag/{version}",
            "assets": [{"name": n, "browser_download_url": u}
                       for n, u in self.assets.items()],
        }).encode("utf-8")

    def fetch(self, url, timeout=None, limit=None, accept=None):
        self.calls.append(url)
        if url == updater.API_LATEST:
            return self.release_json
        if url in self.bodies:
            return self.bodies[url]
        raise UpdateError(f"поддельный GitHub не знает адреса {url}")

    @property
    def downloaded_binary(self):
        return any(updater.asset_name() in url for url in self.calls)


@pytest.fixture
def installed(monkeypatch):
    """Как будто у нас старая сборка."""
    monkeypatch.setattr(updater, "VERSION", "0.0.5")
    return "0.0.5"


@pytest.fixture
def binary(tmp_path):
    """Файл, который играет роль запущенной сборки."""
    path = tmp_path / "B-hydra-Core"
    path.write_bytes("старая сборка".encode("utf-8"))
    path.chmod(0o755)
    return str(path)


@pytest.fixture
def trusted(monkeypatch, key):
    """Ключ релизов заведён — установка разрешена."""
    monkeypatch.setattr(release_key, "RELEASE_PUBLIC_KEY",
                        rsa.public_to_pem(key.public(), spki=True))
    return key


# --- Сравнение версий ----------------------------------------------------------
def test_the_real_tags_of_this_repository_compare_correctly():
    """⚠️ Теги писались ПО-РАЗНОМУ: `v0.0.7`, `v.0.0.8`, `v.0.0.9`, `v0.1.0`.

    Это не выдуманный случай, а то, что лежит в релизах прямо сейчас. Сочти
    разбор «v.0.0.9» другой схемой — и обновление либо не предложится, либо
    предложится в обратную сторону.
    """
    tags = ["v0.0.1", "v0.0.2", "v0.0.3", "v0.0.4", "v0.0.5", "v0.0.6",
            "v0.0.7", "v.0.0.8", "v.0.0.9", "v0.1.0"]
    for older, newer in zip(tags, tags[1:]):
        assert is_newer(newer, older), f"{newer} должен быть новее {older}"
        assert not is_newer(older, newer), f"{older} новее {newer} быть не может"


def test_the_same_version_is_not_an_update():
    """⚠️ Строгое сравнение — это защита, а не педантизм.

    Разреши установку «той же» версии — и под её именем можно подсунуть другой
    файл, не меняя номера вовсе.
    """
    assert not is_newer("v0.1.0", "0.1.0")
    assert not is_newer("0.1", "0.1.0")        # 0.1 и 0.1.0 — одно и то же


def test_unparsable_versions_never_count_as_newer():
    """Мусор вместо версии — это не «версия 0», а отказ.

    Считай мы неразобранное нулём, любой мусор от сервера выглядел бы как
    предложение обновиться.
    """
    for junk in ["мусор", "", "latest", None, "v", "v.", "версия 2", "0x10"]:
        assert not is_newer(junk, "0.1.0"), junk
    assert parse("мусор") is None


def test_extra_version_fields_are_not_swallowed():
    """⚠️ Найдено этим же файлом: версия ОБРЕЗАЛАСЬ до четырёх полей.

    `1.2.3.4` и `1.2.3.5` схлопывались в одну, то есть обновление между ними не
    предложилось бы вовсе. Дополнять нулями нужно (чтобы «0.1» равнялось
    «0.1.0»), а обрезать — нельзя.
    """
    assert is_newer("1.2.3.5", "1.2.3.4")
    assert parse("0.1") == parse("0.1.0") == parse("0.1.0.0")


def test_a_prerelease_is_older_than_the_release():
    assert is_newer("1.0.0", "1.0.0-rc1")
    assert not is_newer("1.0.0-rc1", "1.0.0")
    assert is_newer("1.0.0-rc2", "1.0.0-rc1")


def test_the_package_version_has_one_source():
    """⚠️ Версия была В ДВУХ местах и разъехалась: `__init__` говорил 0.0.2,
    когда в релизах уже лежал v0.1.0. Теперь источник один."""
    import b_hydra

    assert b_hydra.__version__ == VERSION
    text = open(os.path.join(ROOT, "b_hydra", "__init__.py"), encoding="utf-8").read()
    assert not re.search(r'^__version__\s*=\s*["\']', text, re.M), \
        "версия снова прибита гвоздями в __init__.py"


# --- Отказы: без них обновление опаснее его отсутствия -------------------------
def test_without_a_release_key_nothing_is_installed(monkeypatch, installed, binary):
    """⚠️ КЛЮЧА НЕТ — УСТАНОВКИ НЕТ, и сборка даже не качается.

    Контрольная сумма, взятая оттуда же, откуда файл, не доказывает ничего:
    угнавший учётную запись правит и файл, и сумму рядом с ним. Поэтому пока
    подпись проверить нечем, обновлятель обязан только сообщать о новой версии.
    """
    monkeypatch.setattr(release_key, "RELEASE_PUBLIC_KEY", None)
    github = FakeGitHub()
    before = open(binary, "rb").read()

    _, message = updater.update(fetch=github.fetch, target=binary)

    assert "ВЫКЛЮЧЕНА" in message and "вручную" in message
    assert open(binary, "rb").read() == before, "файл подменён без проверки подписи"
    assert not github.downloaded_binary, "сборка скачана, хотя ставить её нельзя"


def test_a_forged_signature_stops_the_update(trusted, installed, binary):
    """Подпись не сходится — установка прекращается, сборка не качается."""
    github = FakeGitHub(key=trusted, break_signature=True)
    before = open(binary, "rb").read()

    with pytest.raises(UpdateError, match="ПОДПИСЬ"):
        updater.update(fetch=github.fetch, target=binary)

    assert open(binary, "rb").read() == before
    assert not github.downloaded_binary


def test_a_tampered_binary_is_refused_even_with_a_valid_signature(trusted, installed,
                                                                 binary):
    """⚠️ Подпись честная, а файл подменён — ровно то, что делает взломщик.

    Манифест подписан настоящим ключом, но сборку по дороге заменили. Совпадать
    обязан ХЕШ ФАЙЛА, иначе подпись защищала бы только список имён.
    """
    github = FakeGitHub(key=trusted, break_binary=True)
    before = open(binary, "rb").read()

    with pytest.raises(UpdateError, match="ХЕШ"):
        updater.update(fetch=github.fetch, target=binary)

    assert open(binary, "rb").read() == before


def test_an_old_signed_manifest_cannot_roll_the_user_back(trusted, monkeypatch,
                                                          binary):
    """⚠️ ОТКАТ: подпись на старом манифесте НАСТОЯЩАЯ, и в этом вся хитрость.

    Предъяви атакующий прошлогодний релиз с честной подписью — человек уехал бы
    на сборку с уже известной дырой, и ни одна проверка подписи этого бы не
    заметила. Спасает только требование «строго новее установленного».
    """
    monkeypatch.setattr(updater, "VERSION", "5.0.0")
    github = FakeGitHub(version="v1.0.0", key=trusted)

    _, message = updater.update(fetch=github.fetch, target=binary)
    assert "последняя версия" in message
    assert not github.downloaded_binary


def test_a_manifest_signed_for_another_version_is_refused(trusted, installed, binary):
    """Манифест подписан под одну версию, а приложен к другой — отказ.

    Иначе к свежему релизу можно приложить честно подписанный манифест от
    другого и увести проверку хеша на чужие файлы.
    """
    github = FakeGitHub(version="v9.9.9", key=trusted, manifest_version="v9.9.8")
    with pytest.raises(UpdateError, match="манифест подписан под версию"):
        updater.update(fetch=github.fetch, target=binary)
    assert not github.downloaded_binary


def test_a_release_without_a_manifest_does_not_install(trusted, installed, binary):
    """Нет подписанного манифеста — нет установки."""
    github = FakeGitHub(with_manifest=False)
    _, message = updater.update(fetch=github.fetch, target=binary)
    assert "манифест" in message.lower() and "вручную" in message
    assert not github.downloaded_binary


def test_a_non_canonical_manifest_is_not_accepted(trusted, installed, binary):
    """⚠️ Подпись считается от БАЙТОВ, а разбор идёт отдельно.

    Значит запись обязана быть канонической: иначе к подписанному манифесту
    дописывают дублирующий ключ, подпись остаётся верной для исходных байтов,
    а `json.loads` берёт ПОСЛЕДНЕЕ значение — и проверяется одно, а ставится
    другое. Тот же приём, что порождает уязвимости разбора JSON.
    """
    name = updater.asset_name()
    binary_payload = "новая сборка".encode("utf-8")
    digest = hashlib.sha512(binary_payload).hexdigest()
    honest = {"version": "9.9.9", "files": {name: digest}}
    raw = updater.manifest_payload(honest)
    # То же самое, но с пробелами — байты другие, смысл тот же.
    padded = json.dumps(honest, sort_keys=True, indent=2).encode("utf-8")
    assert padded != raw

    signature = rsa.sign_pss(trusted, padded)     # подпись честная ДЛЯ padded
    with pytest.raises(UpdateError, match="неканонично"):
        updater.verify_manifest(padded, signature)


def test_the_updater_refuses_plain_http():
    """Скачивать то, что будет ЗАПУЩЕНО, по открытому каналу нельзя."""
    with pytest.raises(UpdateError, match="не HTTPS"):
        updater._fetch("http://example.invalid/B-hydra-Core")


def test_a_redirect_down_to_http_is_refused():
    """⚠️ urllib по умолчанию послушно идёт с https на http.

    Сервер отвечает перенаправлением — и файл, который мы собираемся запустить,
    едет открытым каналом. Человек при этом видит прежнюю команду и прежний
    адрес.
    """
    handler = updater._HttpsOnlyRedirect()
    with pytest.raises(UpdateError, match="незащищённый"):
        handler.redirect_request(None, None, 302, "Found", {},
                                 "http://example.invalid/файл")


def test_an_endless_response_is_cut_off(monkeypatch):
    """Сервер может лить байты бесконечно — мы держим их в памяти.

    Без потолка один ответ занимает всю память машины.
    """
    class Endless:
        def read(self, size):
            return b"x" * size

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(updater, "_opener", lambda: type(
        "O", (), {"open": lambda self, *a, **k: Endless()})())
    with pytest.raises(UpdateError, match="больше допустимого"):
        updater._fetch("https://example.invalid/поток", limit=1 << 20)


# --- Запуск из исходников: самое опасное место ---------------------------------
def test_running_from_source_is_not_a_binary_to_replace():
    """⚠️ САМАЯ ОПАСНАЯ ОШИБКА, КОТОРУЮ ЗДЕСЬ МОЖНО ДОПУСТИТЬ.

    Из исходников `sys.executable` — это ИНТЕРПРЕТАТОР PYTHON. Обновлятель, не
    проверивший `sys.frozen`, положил бы сборку B-hydra поверх `/usr/bin/python3`
    и снёс бы человеку всю систему, а не только кошелёк. Тесты идут из
    исходников, поэтому ответ обязан быть None.
    """
    assert updater.running_binary() is None


def test_from_source_the_update_tells_you_to_use_git(installed, monkeypatch):
    """И сообщение должно быть по делу, а не «не удалось обновиться»."""
    monkeypatch.setattr(release_key, "RELEASE_PUBLIC_KEY", None)
    github = FakeGitHub()
    _, message = updater.update(fetch=github.fetch)      # target не задан
    assert "ИСХОДНИКОВ" in message and "git pull" in message
    assert not github.downloaded_binary


# --- Установка -----------------------------------------------------------------
def test_a_verified_update_is_installed(trusted, installed, binary):
    """Счастливый путь: подпись верна, хеш сошёлся — файл заменён."""
    github = FakeGitHub(key=trusted, binary="свежая сборка".encode("utf-8"))
    _, message = updater.update(fetch=github.fetch, target=binary)

    assert open(binary, "rb").read() == "свежая сборка".encode("utf-8")
    assert "Перезапустите" in message


def test_the_installed_file_stays_executable(trusted, installed, binary):
    """⚠️ Без права на запуск «обновлённая» программа просто не стартует,
    и починить это из интерфейса нечем."""
    github = FakeGitHub(key=trusted)
    updater.update(fetch=github.fetch, target=binary)
    assert os.stat(binary).st_mode & stat.S_IXUSR


def test_a_failed_install_leaves_the_old_build_alone(binary):
    """Обрыв при записи не должен оставить человека без работающей программы."""
    before = open(binary, "rb").read()
    with pytest.raises(UpdateError):
        updater.install("новое".encode("utf-8"), os.path.join(binary, "внутрь-файла", "нельзя"))
    assert open(binary, "rb").read() == before


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0,
                    reason="root игнорирует права каталога — проверять нечего")
def test_installing_without_write_permission_explains_itself(tmp_path):
    """Каталог только для чтения — внятный отказ, а не traceback.

    Сборка нередко лежит в /usr/local/bin или Program Files, куда обычному
    пользователю писать нельзя, и упереться в это он должен сообщением, а не
    трассировкой стека.
    """
    directory = tmp_path / "только-чтение"
    directory.mkdir()
    target = directory / "B-hydra-Core"
    target.write_bytes("старое".encode("utf-8"))
    directory.chmod(0o500)
    try:
        with pytest.raises(UpdateError, match="нет права записи"):
            updater.install("новое".encode("utf-8"), str(target))
        assert target.read_bytes() == "старое".encode("utf-8")
    finally:
        directory.chmod(0o700)          # иначе tmp_path не уберётся


def test_leftovers_from_a_previous_update_are_cleaned_up(binary):
    """`.old` остаётся на Windows (занятый файл удалить нельзя) — убираем потом."""
    open(binary + ".old", "wb").write("позапрошлая сборка".encode("utf-8"))
    assert updater.cleanup_backups(binary) == 1
    assert not os.path.exists(binary + ".old")


# --- Подпись релиза: инструмент владельца --------------------------------------
def test_sign_and_verify_go_together(tmp_path, key):
    """Полный круг: подписали файлы → проверили вшитым ключом."""
    build = tmp_path / updater.asset_name()
    build.write_bytes("сборка для релиза".encode("utf-8"))
    private = tmp_path / "release.key"
    private.write_text(rsa.private_to_pem(key, pkcs8=True), encoding="utf-8")
    manifest_path = tmp_path / updater.MANIFEST_NAME
    signature_path = tmp_path / updater.SIGNATURE_NAME

    code = release.main(["sign", "--key", str(private), "--version", "v1.2.3",
                         "--manifest", str(manifest_path),
                         "--signature", str(signature_path), str(build)])
    assert code == 0

    manifest = updater.verify_manifest(
        manifest_path.read_bytes(), signature_path.read_bytes(),
        rsa.public_to_pem(key.public(), spki=True))
    assert manifest["version"] == "1.2.3"
    assert manifest["files"][updater.asset_name()] == \
        hashlib.sha512("сборка для релиза".encode("utf-8")).hexdigest()


def test_a_manifest_signed_by_a_stranger_is_refused(tmp_path, key):
    """Подпись ДРУГИМ ключом — не подпись. Иначе кейринг ничего не значит."""
    stranger = rsa.generate(2048)
    manifest = {"version": "1.2.3", "files": {updater.asset_name(): "00"}}
    raw = updater.manifest_payload(manifest)
    signature = rsa.sign_pss(stranger, raw)

    with pytest.raises(UpdateError, match="ПОДПИСЬ"):
        updater.verify_manifest(raw, signature,
                                rsa.public_to_pem(key.public(), spki=True))


def test_the_private_key_is_written_locked_down(tmp_path, monkeypatch, key):
    """⚠️ Права 0600 ставятся ПРИ СОЗДАНИИ файла, а не после записи.

    Создать ключ всем на чтение и сузить права следующей строкой — значит
    оставить окно, в котором его успевает прочитать кто угодно на машине.
    Генерация подменяется готовым ключом: здесь проверяются права, а не RSA.
    """
    monkeypatch.setattr(rsa, "generate", lambda *a, **kw: key)
    out = tmp_path / "release.key"

    assert release.main(["keygen", "--out", str(out)]) == 0
    assert stat.S_IMODE(os.stat(out).st_mode) == 0o600
    assert "PRIVATE KEY" in out.read_text(encoding="utf-8")

    # Существующий ключ не затирается без --force: потеря ключа релизов
    # означает, что подписывать новые выпуски будет нечем.
    assert release.main(["keygen", "--out", str(out)]) == 1


# --- Согласованность с остальным репозиторием ----------------------------------
def test_asset_names_match_what_the_workflow_actually_builds():
    """⚠️ Обновлятель ищет файл ПО ИМЕНИ. Разойдись имена со сборкой — он
    честно доложит «в релизе нет файла», и обновление сломается у всех сразу."""
    workflow = open(os.path.join(ROOT, ".github/workflows/build.yml"),
                    encoding="utf-8").read()
    built = set(re.findall(r"asset:\s*(\S+)", workflow))
    assert built, "в сборке не нашлось имён файлов релиза"
    assert set(updater.ASSETS.values()) == built, (
        f"обновлятель ждёт {sorted(updater.ASSETS.values())}, "
        f"а собирается {sorted(built)}")


@pytest.mark.parametrize("system,expected", [
    ("Windows", "B-hydra-Core-windows.exe"),
    ("Darwin", "B-hydra-Core-macos"),
    ("Linux", "B-hydra-Core-linux"),
])
def test_each_system_gets_its_own_file(system, expected):
    assert updater.asset_name(system) == expected


def test_an_unknown_system_says_so_instead_of_guessing():
    """Догадка здесь означала бы скачать сборку для чужой системы."""
    with pytest.raises(UpdateError, match="сборок не выпускается"):
        updater.asset_name("HaikuOS")


def test_the_shipped_key_slot_is_empty_and_documented():
    """⚠️ В репозитории ключа быть не должно — там только ОТКРЫТАЯ половина,
    и пока её нет, это обязано быть написано словами, а не подразумеваться."""
    assert release_key.RELEASE_PUBLIC_KEY is None
    assert not release_key.have_key()
    text = open(os.path.join(ROOT, "b_hydra", "release_key.py"), encoding="utf-8").read()
    assert "PRIVATE KEY" not in text, "в репозиторий попал закрытый ключ"


def test_no_private_key_is_committed_anywhere():
    """Прямая проверка: закрытого ключа не должно быть ни в одном файле.

    ⚠️ Маркеры СКЛЕИВАЮТСЯ из частей намеренно. Напиши их целиком — и первым,
    кого поймает проверка, окажется она сама: полный литерал будет лежать в её
    же исходнике. Так и вышло при первом прогоне. Исключать себя из обхода
    нельзя — тестовый файл ровно то место, куда проще всего случайно вставить
    настоящий ключ, отлаживая подпись.
    """
    import subprocess

    head_marker = b"-----BEGIN "
    markers = [head_marker + tail for tail in (
        b"RSA PRIVATE KEY-----",
        b"PRIVATE KEY-----",
        b"EC PRIVATE KEY-----",
        b"OPENSSH PRIVATE KEY-----",
    )]

    # ⚠️ `-z`, а не `.split()`. Без него git ЭКРАНИРУЕТ имена с не-ASCII и
    # берёт их в кавычки («забытый.key» → "\320\267\320\260…"), а имена с
    # пробелами `.split()` рвёт пополам. И то и другое даёт молчаливый
    # пропуск: файл не находится на диске, проверка его тихо перешагивает.
    # Поймано подбросом настоящего ключа под русским именем — первая версия
    # этой проверки его не заметила.
    raw = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, check=True,
                         capture_output=True).stdout
    listing = [name.decode("utf-8", "surrogateescape")
               for name in raw.split(b"\0") if name]
    assert len(listing) > 100, "git ls-files вернул подозрительно мало файлов"

    for name in listing:
        path = os.path.join(ROOT, name)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "rb") as handle:
                blob = handle.read()
        except OSError:
            continue
        for marker in markers:
            assert marker not in blob, \
                f"в {name} лежит закрытый ключ ({marker.decode()}…)"

