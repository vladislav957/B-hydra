"""Обновление B-hydra Core — как `apt`: проверить, скачать, проверить подпись.

    python -m b_hydra.cli update --check     # только посмотреть, что вышло
    python -m b_hydra.cli update             # поставить (спросит подтверждение)

Устроено ровно как менеджер пакетов, и по тем же причинам:

1. **Список** берётся у GitHub (`/releases/latest`) по HTTPS.
2. **Манифест** релиза (`bhydra-release.json`) перечисляет файлы и их SHA-512.
3. **Подпись** манифеста проверяется ключом из `release_key.py` — это кейринг,
   закрытый ключ лежит у владельца офлайн (`Release`-файл и `trusted.gpg` у
   apt устроены так же).
4. Только после этого качается сам файл, и его хеш обязан совпасть с
   манифестом.
5. Замена — атомарная, с сохранением права на запуск.

⚠️ БЕЗ ПОДПИСИ УСТАНОВКА НЕ ПРОИСХОДИТ. Контрольная сумма, взятая оттуда же,
откуда файл, не доказывает НИЧЕГО: угнавший учётную запись правит и файл, и
сумму рядом с ним. Пока `release_key.py` пуст, `update` только сообщает о новой
версии и отправляет скачивать руками — молча ставить неподтверждённый файл в
кошелёк нельзя, это прямой путь к краже ключей всех, кто обновился.

⚠️ ЗАЩИТА ОТ ОТКАТА. Ставится только СТРОГО более новая версия. Иначе старый,
когда-то честно подписанный манифест можно предъявить снова и вернуть человека
на сборку с уже известной дырой — подпись-то на нём настоящая.

⚠️ ХЕШ ФАЙЛА СЧИТАЕТСЯ `hashlib`, а не нашим чистым SHA-512, и это тот же
случай, что в `secure.py`. Замер на этой машине: наш SHA-512 даёт 0,67 МиБ/с,
то есть сборка Linux (25,5 МиБ) проверялась бы 38 СЕКУНД против 0,04 с у
hashlib — при одинаковых до байта значениях (`sha2.py` сверен с hashlib
тестами). Правило «своё, hashlib только ускоритель» держится там, где нужна
воспроизводимость снаружи (адреса, txid, Меркл); хеш файла сборки никто не
пересчитывает на другом языке, он живёт внутри одной проверки.
"""

import hashlib
import json
import os
import platform
import shutil
import ssl
import sys
import urllib.error
import urllib.request

from . import release_key, rsa
from .version import VERSION, format_version, is_newer

__all__ = [
    "UpdateError", "Release", "REPO", "asset_name", "running_binary",
    "check", "download_asset", "install", "cleanup_backups", "update",
]

REPO = "vladislav957/B-hydra"
API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"

MANIFEST_NAME = "bhydra-release.json"
SIGNATURE_NAME = "bhydra-release.json.sig"

#: Имена файлов сборки. Совпадают с `asset:` в .github/workflows/build.yml —
#: расхождение ловит тест, иначе обновлятель искал бы файл, которого нет.
ASSETS = {
    "windows": "B-hydra-Core-windows.exe",
    "darwin": "B-hydra-Core-macos",
    "linux": "B-hydra-Core-linux",
}

DEFAULT_TIMEOUT = 30.0
#: Потолок на скачивание. Сервер может лить байты бесконечно, а мы держим их в
#: памяти — без предела это способ занять всю память одним ответом.
MAX_DOWNLOAD = 256 << 20
MAX_METADATA = 4 << 20
USER_AGENT = f"B-hydra-Core/{VERSION}"


class UpdateError(Exception):
    """Обновление не состоялось. Текст показывается человеку как есть."""


class _HttpsOnlyRedirect(urllib.request.HTTPRedirectHandler):
    """Перенаправление только на HTTPS.

    ⚠️ urllib по умолчанию послушно идёт с https на http. Скачивание файла,
    который мы собираемся ЗАПУСТИТЬ, по открытому каналу — это возврат ровно к
    той угрозе, от которой защищает TLS, причём незаметный: адрес подменяет
    сервер, человек видит прежнюю команду.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not str(newurl).lower().startswith("https://"):
            raise UpdateError(
                f"перенаправление с HTTPS на незащищённый адрес: {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _opener():
    # Проверка сертификатов ВКЛЮЧЕНА (`create_default_context`) — выключать её
    # здесь нельзя ни при каких условиях.
    context = ssl.create_default_context()
    return urllib.request.build_opener(
        _HttpsOnlyRedirect(), urllib.request.HTTPSHandler(context=context))


def _fetch(url, timeout=DEFAULT_TIMEOUT, limit=MAX_DOWNLOAD, accept=None):
    """Скачать по HTTPS с потолком размера. Возвращает bytes."""
    if not str(url).lower().startswith("https://"):
        raise UpdateError(f"адрес не HTTPS: {url}")
    headers = {"User-Agent": USER_AGENT}
    if accept:
        headers["Accept"] = accept
    request = urllib.request.Request(url, headers=headers)
    try:
        with _opener().open(request, timeout=timeout) as response:
            chunks, total = [], 0
            while True:
                chunk = response.read(64 << 10)
                if not chunk:
                    break
                total += len(chunk)
                if total > limit:
                    raise UpdateError(
                        f"ответ больше допустимого ({limit} байт): {url}")
                chunks.append(chunk)
    except UpdateError:
        raise
    except urllib.error.HTTPError as error:
        raise UpdateError(f"сервер ответил {error.code} на {url}") from error
    except (urllib.error.URLError, OSError, ssl.SSLError) as error:
        raise UpdateError(f"не удалось связаться с {url}: {error}") from error
    return b"".join(chunks)


class Release:
    """Что лежит в последнем релизе — уже разобранное."""

    def __init__(self, version, notes="", published="", assets=None, page=""):
        self.version = version
        self.notes = notes or ""
        self.published = published or ""
        self.assets = dict(assets or {})      # имя → адрес скачивания
        self.page = page or RELEASES_PAGE

    @property
    def is_newer(self) -> bool:
        return is_newer(self.version, VERSION)

    @property
    def asset_for_this_system(self):
        return self.assets.get(asset_name())

    def __repr__(self):
        return f"<Release {self.version} файлов={len(self.assets)}>"


def asset_name(system=None) -> str:
    """Имя файла сборки для этой системы."""
    key = (system or platform.system()).strip().lower()
    if key.startswith("win"):
        key = "windows"
    elif key in ("mac", "macos", "osx", "darwin"):
        key = "darwin"
    try:
        return ASSETS[key]
    except KeyError:
        raise UpdateError(f"для системы «{key}» сборок не выпускается") from None


def running_binary():
    """Путь к ЗАПУЩЕННОЙ СБОРКЕ или None, если работаем из исходников.

    ⚠️ САМОЕ ОПАСНОЕ МЕСТО ВО ВСЁМ МОДУЛЕ. Из исходников `sys.executable` — это
    ИНТЕРПРЕТАТОР PYTHON, а не наша программа. Обновлятель, не проверивший
    этого, положил бы сборку B-hydra поверх `/usr/bin/python3` и сломал бы
    пользователю всю систему, а не только кошелёк. Поэтому признак ровно один:
    `sys.frozen`, который ставит PyInstaller. Нет его — обновлять нечего, и
    честный ответ «ты запущен из исходников, обновляйся через git pull».
    """
    if not getattr(sys, "frozen", False):
        return None
    return os.path.realpath(sys.executable)


def check(fetch=_fetch, timeout=DEFAULT_TIMEOUT) -> Release:
    """Спросить GitHub про последний релиз. Ничего не скачивает и не ставит."""
    raw = fetch(API_LATEST, timeout=timeout, limit=MAX_METADATA,
                accept="application/vnd.github+json")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise UpdateError(f"ответ GitHub не разобрался: {error}") from error
    if not isinstance(data, dict):
        raise UpdateError("ответ GitHub не похож на описание релиза")

    tag = data.get("tag_name") or data.get("name") or ""
    assets = {}
    for item in data.get("assets") or []:
        if isinstance(item, dict) and item.get("name") and item.get("browser_download_url"):
            assets[item["name"]] = item["browser_download_url"]
    return Release(version=str(tag), notes=str(data.get("body") or ""),
                   published=str(data.get("published_at") or ""),
                   assets=assets, page=str(data.get("html_url") or RELEASES_PAGE))


# --- Манифест и подпись --------------------------------------------------------
def manifest_payload(manifest: dict) -> bytes:
    """Байты, которые подписываются. Каноничные — как `signing_payload`.

    ⚠️ Подписывается РОВНО ЭТА запись, а не «словарь»: разный порядок ключей
    или пробелы дают разные байты и разную подпись. Та же дисциплина, что у
    транзакций, и по той же причине — подписанное должно воспроизводиться
    побайтово у любого, кто проверяет.
    """
    return json.dumps(manifest, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def verify_manifest(raw: bytes, signature: bytes, public_key_pem=None) -> dict:
    """Проверить подпись манифеста и вернуть его. Любая беда — исключение."""
    pem = public_key_pem if public_key_pem is not None else release_key.RELEASE_PUBLIC_KEY
    if not pem:
        raise UpdateError(
            "ключ релизов не заведён — проверить подпись нечем "
            "(см. b_hydra/release_key.py)")
    try:
        key = rsa.public_from_pem(pem)
    except Exception as error:
        raise UpdateError(f"ключ релизов не читается: {error}") from error

    if not rsa.verify_pss(key, raw, signature):
        raise UpdateError(
            "ПОДПИСЬ МАНИФЕСТА НЕВЕРНА — файл подменён или подписан не тем "
            "ключом. Обновление остановлено.")

    try:
        manifest = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise UpdateError(f"манифест не разобрался: {error}") from error
    if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), dict):
        raise UpdateError("манифест не содержит списка файлов")
    if not manifest.get("version"):
        raise UpdateError("манифест без версии")

    # ⚠️ Подпись проверяется от БАЙТОВ, а разбираем мы их отдельно — значит
    # запись обязана быть канонической, иначе подписанному манифесту можно
    # было бы добавить дублирующий ключ и получить другой разбор при той же
    # подписи.
    if manifest_payload(manifest) != raw:
        raise UpdateError("манифест записан неканонично — подпись не засчитана")
    return manifest


def file_digest(payload: bytes) -> str:
    """SHA-512 файла сборки (hashlib — см. шапку модуля)."""
    return hashlib.sha512(payload).hexdigest()


def download_asset(release: Release, manifest: dict, fetch=_fetch,
                   timeout=DEFAULT_TIMEOUT) -> bytes:
    """Скачать сборку для этой системы и сверить её хеш с манифестом."""
    name = asset_name()
    url = release.assets.get(name)
    if not url:
        raise UpdateError(f"в релизе {release.version} нет файла {name}")
    expected = (manifest.get("files") or {}).get(name)
    if not expected:
        raise UpdateError(f"манифест не описывает файл {name}")

    payload = fetch(url, timeout=timeout, limit=MAX_DOWNLOAD)
    actual = file_digest(payload)
    if actual.lower() != str(expected).lower():
        raise UpdateError(
            f"ХЕШ СКАЧАННОГО ФАЙЛА НЕ СОВПАЛ с подписанным манифестом "
            f"({name}). Обновление остановлено.")
    return payload


# --- Замена файла --------------------------------------------------------------
def _remove_quietly(path):
    try:
        os.remove(path)
    except OSError:
        pass


def install(payload: bytes, target: str) -> str:
    """Положить новую сборку на место старой. Атомарно, с откатом.

    ⚠️ На Windows работающий .exe ПЕРЕЗАПИСАТЬ нельзя, а ПЕРЕИМЕНОВАТЬ — можно
    (открытый дескриптор следует за именем). Поэтому там старый файл сначала
    отъезжает в `.old`, и только потом на его место встаёт новый; при неудаче
    возвращается обратно. На POSIX файл можно подменить прямо под работающим
    процессом: `os.replace` атомарен, а запущенная копия продолжает жить на
    своём inode до перезапуска.
    """
    target = os.path.realpath(target)
    directory = os.path.dirname(target) or "."
    if not os.access(directory, os.W_OK):
        raise UpdateError(
            f"нет права записи в {directory} — запусти от администратора "
            f"или скачай сборку вручную: {RELEASES_PAGE}")

    fresh = target + ".new"
    try:
        with open(fresh, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        # Право на запуск обязано сохраниться: без него «обновлённая»
        # программа просто не стартует, и починить это из интерфейса нечем.
        if os.path.exists(target):
            shutil.copymode(target, fresh)
        mode = os.stat(fresh).st_mode
        os.chmod(fresh, mode | 0o111)
    except OSError as error:
        _remove_quietly(fresh)
        raise UpdateError(f"не удалось записать новую сборку: {error}") from error

    backup = target + ".old"
    try:
        if os.name == "nt":
            _remove_quietly(backup)
            os.replace(target, backup)
            try:
                os.replace(fresh, target)
            except OSError:
                os.replace(backup, target)      # откат: старое на место
                raise
        else:
            os.replace(fresh, target)
    except OSError as error:
        _remove_quietly(fresh)
        raise UpdateError(f"не удалось заменить сборку: {error}") from error
    return target


def cleanup_backups(target=None) -> int:
    """Убрать `.old`, оставшийся от прошлого обновления на Windows.

    Зовётся при запуске: пока старый файл занят работающим процессом, удалить
    его нельзя, поэтому уборка откладывается до следующего старта.
    """
    target = target or running_binary()
    if not target:
        return 0
    removed = 0
    for suffix in (".old", ".new"):
        path = target + suffix
        if os.path.exists(path):
            before = os.path.exists(path)
            _remove_quietly(path)
            removed += int(before and not os.path.exists(path))
    return removed


# --- Всё вместе ----------------------------------------------------------------
def update(fetch=_fetch, timeout=DEFAULT_TIMEOUT, target=None, dry_run=False):
    """Проверить и (если можно) поставить. Возвращает (релиз, сообщение).

    Ничего не делает молча: на каждый отказ — причина словами.
    """
    release = check(fetch=fetch, timeout=timeout)
    if not release.version:
        raise UpdateError("GitHub не сообщил версию последнего релиза")

    if not release.is_newer:
        return release, (f"Установлена последняя версия "
                         f"({format_version(VERSION)}).")

    headline = (f"Доступна версия {format_version(release.version)} "
                f"(у вас {format_version(VERSION)}).")
    if dry_run:
        return release, headline

    binary = target or running_binary()
    if not binary:
        return release, (
            f"{headline}\nВы запущены ИЗ ИСХОДНИКОВ — обновляйтесь через "
            f"git pull. Готовые сборки: {release.page}")

    if not release_key.have_key():
        return release, (
            f"{headline}\nАвтоматическая установка ВЫКЛЮЧЕНА: в сборку не "
            f"заложен ключ релизов, поэтому подпись проверить нечем, а ставить "
            f"непроверенный файл в кошелёк нельзя. Скачайте вручную: "
            f"{release.page}")

    manifest_url = release.assets.get(MANIFEST_NAME)
    signature_url = release.assets.get(SIGNATURE_NAME)
    if not manifest_url or not signature_url:
        return release, (
            f"{headline}\nУ релиза нет подписанного манифеста "
            f"({MANIFEST_NAME} + {SIGNATURE_NAME}), поэтому установка "
            f"остановлена. Скачайте вручную: {release.page}")

    raw = fetch(manifest_url, timeout=timeout, limit=MAX_METADATA)
    signature = fetch(signature_url, timeout=timeout, limit=MAX_METADATA)
    manifest = verify_manifest(raw, signature)

    # ⚠️ Версия в манифесте обязана совпасть с релизом: иначе к свежему релизу
    # прикладывают старый, ЧЕСТНО ПОДПИСАННЫЙ манифест от дырявой сборки — и
    # проверка хеша уезжает на чужие файлы, ничего не заметив.
    if format_version(manifest["version"]) != format_version(release.version):
        raise UpdateError(
            f"манифест подписан под версию {manifest['version']}, "
            f"а релиз — {release.version}")
    # Запасная проверка. Честно говоря, она НЕДОСТИЖИМА: выше уже отказано
    # всему, что не новее (`release.is_newer`), а строкой выше — всему, где
    # манифест не совпал с релизом. Оставлена намеренно, чтобы перестановка
    # или правка тех двух условий не открыла откат молча; тестом не
    # покрывается именно потому, что дотянуться до неё нечем.
    if not is_newer(manifest["version"], VERSION):
        raise UpdateError(
            f"манифест описывает версию {manifest['version']}, которая не "
            f"новее установленной ({VERSION}) — установка отклонена")

    payload = download_asset(release, manifest, fetch=fetch, timeout=timeout)
    install(payload, binary)
    return release, (
        f"Установлена версия {format_version(release.version)}. "
        f"Перезапустите B-hydra Core.")
