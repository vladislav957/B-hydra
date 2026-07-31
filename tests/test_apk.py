"""APK кошелька: двоичный манифест и сборка без Android SDK.

Главная часть — свой кодировщик AXML. Ошибку в нём легко не заметить: манифест
«почти правильной» структуры Android разберёт, но атрибут с чужим ресурсным id
молча пропустит — и приложение окажется, например, без доступа в интернет.
Поэтому результат читается ОБРАТНО независимым разборщиком (pyaxmlparser), а не
нашим же кодом.

Сборка APK проверяется, если под рукой JDK и уже скачанные инструменты
(BHYDRA_APK_TOOLS или кэш по умолчанию) — качать их в тестах не станем.
"""

import os
import shutil
import struct
import subprocess
import tempfile
import zipfile
import zlib

import pytest

from b_hydra import apkbuild
from b_hydra.axml import (ANDROID_NS, ATTRIBUTE_IDS, TYPE_INT_BOOLEAN,
                          TYPE_INT_DEC, TYPE_INT_HEX, TYPE_STRING, _StringPool,
                          _typed_value, encode)

MANIFEST = f"""<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="{ANDROID_NS}" package="io.bhydra.wallet"
          android:versionCode="7" android:versionName="1.2">
  <uses-sdk android:minSdkVersion="24" android:targetSdkVersion="29"/>
  <uses-permission android:name="android.permission.INTERNET"/>
  <uses-permission android:name="android.permission.CAMERA"/>
  <application android:label="B-hydra Wallet" android:allowBackup="false"
               android:usesCleartextTraffic="true">
    <activity android:name="io.bhydra.wallet.MainActivity"
              android:exported="true" android:configChanges="0x4a0">
      <intent-filter>
        <action android:name="android.intent.action.MAIN"/>
        <category android:name="android.intent.category.LAUNCHER"/>
      </intent-filter>
    </activity>
  </application>
</manifest>"""


# --- Кодировщик AXML ----------------------------------------------------------
def test_encoded_manifest_has_the_right_chunk_header():
    data = encode(MANIFEST)
    kind, header_size, size = struct.unpack("<HHI", data[:8])
    assert kind == 0x0003          # RES_XML_TYPE
    assert header_size == 8
    assert size == len(data)       # длина в заголовке = длина файла
    pool_kind, = struct.unpack("<H", data[8:10])
    assert pool_kind == 0x0001     # строковый пул идёт первым


def test_resource_map_is_parallel_to_the_string_pool():
    """Карта ресурсов — массив id, стоящий параллельно НАЧАЛУ пула.

    Если порядок сбить, каждый атрибут получит чужой ресурсный id: манифест
    разберётся, но будет означать не то.
    """
    data = encode(MANIFEST)
    pool_size, = struct.unpack("<I", data[12:16])
    strings, = struct.unpack("<I", data[16:20])
    map_start = 8 + pool_size
    map_kind, map_header, map_size = struct.unpack("<HHI", data[map_start:map_start + 8])
    assert map_kind == 0x0180
    ids = struct.unpack(f"<{(map_size - 8) // 4}I", data[map_start + 8:map_start + map_size])
    # Уникальных атрибутов android:* в манифесте — ровно столько же.
    used = {key.split("}")[1] for key in (
        "}versionCode", "}versionName", "}minSdkVersion", "}targetSdkVersion",
        "}name", "}label", "}allowBackup", "}usesCleartextTraffic",
        "}exported", "}configChanges")}
    assert len(ids) == len(used)
    assert set(ids) == {ATTRIBUTE_IDS[name] for name in used}
    assert len(ids) <= strings


def test_unknown_android_attribute_is_refused():
    """Неизвестный атрибут — ОШИБКА, а не «запишем без id».

    Без ресурсного id система его просто не увидит, и сборка молча дала бы
    приложение с потерянной настройкой.
    """
    bad = MANIFEST.replace('android:allowBackup="false"',
                           'android:allowBackup="false" android:выдумка="1"')
    with pytest.raises(ValueError, match="выдумка|неизвестн"):
        encode(bad)


def test_values_are_typed_not_all_strings():
    """Числа и «true/false» Android ждёт числами: строку в поле версии он не
    поймёт."""
    pool = _StringPool()
    pool.add("текст")
    assert _typed_value("true", pool) == (TYPE_INT_BOOLEAN, 0xFFFFFFFF)
    assert _typed_value("false", pool) == (TYPE_INT_BOOLEAN, 0)
    assert _typed_value("29", pool) == (TYPE_INT_DEC, 29)
    assert _typed_value("0x4a0", pool) == (TYPE_INT_HEX, 0x4A0)
    assert _typed_value("текст", pool) == (TYPE_STRING, 0)


def test_string_pool_deduplicates():
    pool = _StringPool()
    assert pool.add("a") == 0
    assert pool.add("b") == 1
    assert pool.add("a") == 0                  # повтор не растит пул
    assert len(pool) == 2


def test_resource_references_are_stripped_and_reported():
    """Без resources.arsc ссылка `@mipmap/...` — битый указатель.

    Молча оставлять её нельзя, поэтому выброшенное возвращается наружу.
    """
    text, dropped = apkbuild.strip_resource_references(
        MANIFEST.replace('android:label="B-hydra Wallet"',
                         'android:label="B-hydra Wallet" android:icon="@mipmap/ic"'))
    assert any("@mipmap/ic" in item for item in dropped)
    assert "@mipmap" not in text
    encode(text)                                # и результат кодируется


def test_project_manifest_encodes():
    """Настоящий манифест проекта должен собираться (после снятия ссылок)."""
    path = os.path.join(apkbuild.PROJECT, "AndroidManifest.xml")
    with open(path, encoding="utf-8") as handle:
        text, _dropped = apkbuild.strip_resource_references(handle.read())
    assert len(encode(text)) > 200


# --- Обратный разбор независимым парсером -------------------------------------
def test_independent_parser_reads_our_manifest():
    """Наш AXML читается ЧУЖИМ разборщиком — и читается верно.

    Проверять своим же кодом бессмысленно: он повторит собственную ошибку.
    """
    printer = pytest.importorskip(
        "pyaxmlparser.axmlprinter", reason="нет pyaxmlparser").AXMLPrinter
    etree = pytest.importorskip("lxml.etree", reason="нет lxml")
    root = printer(encode(MANIFEST)).get_xml_obj()
    android = f"{{{ANDROID_NS}}}"

    assert root.get("package") == "io.bhydra.wallet"
    assert root.get(android + "versionName") == "1.2"
    sdk = root.find("uses-sdk")
    assert sdk.get(android + "minSdkVersion") == "24"
    assert sdk.get(android + "targetSdkVersion") == "29"
    permissions = {p.get(android + "name") for p in root.findall("uses-permission")}
    assert permissions == {"android.permission.INTERNET",
                           "android.permission.CAMERA"}
    activity = root.find("application/activity")
    assert activity.get(android + "name") == "io.bhydra.wallet.MainActivity"
    assert activity.get(android + "exported") == "true"
    assert activity.find("intent-filter/action").get(android + "name") == \
        "android.intent.action.MAIN"
    assert etree is not None


# --- Сборка APK ---------------------------------------------------------------
def _tools_ready():
    """Инструменты уже скачаны? Качать их в тестах не будем."""
    if not (shutil.which("javac") and shutil.which("jarsigner")
            and shutil.which("keytool")):
        return None
    cache = os.environ.get("BHYDRA_APK_TOOLS") or os.path.join(
        tempfile.gettempdir(), "bhydra-apk-tools")
    needed = [apkbuild.ANDROID_JAR[1], apkbuild.DX_JAR[1]]
    if all(os.path.exists(os.path.join(cache, name)) for name in needed):
        return cache
    return None


@pytest.mark.skipif(_tools_ready() is None,
                    reason="нет JDK или не скачаны android.jar/dx.jar")
def test_apk_builds_and_verifies(tmp_path):
    """Сквозная сборка: javac → dex → AXML → ZIP → подпись."""
    out = str(tmp_path / "wallet.apk")
    apkbuild.build(out, cache=_tools_ready())
    report = apkbuild.verify(out)
    assert report["has_manifest"] and report["has_dex"]
    assert report["signed"] and report["jarsigner_ok"]
    assert report["dex_magic"]


@pytest.mark.skipif(_tools_ready() is None,
                    reason="нет JDK или не скачаны android.jar/dx.jar")
def test_built_dex_passes_its_own_checksums(tmp_path):
    """DEX хранит собственные контрольные суммы — они обязаны сойтись.

    Это ловит и порчу при упаковке в ZIP, и обрезанный файл.
    """
    out = str(tmp_path / "wallet.apk")
    apkbuild.build(out, cache=_tools_ready())
    dex = zipfile.ZipFile(out).read("classes.dex")
    checksum, = struct.unpack("<I", dex[8:12])
    assert zlib.adler32(dex[12:]) == checksum
    import hashlib
    assert hashlib.sha1(dex[32:]).digest() == dex[12:32]
    assert struct.unpack("<I", dex[40:44])[0] == 0x12345678   # порядок байтов


@pytest.mark.skipif(_tools_ready() is None,
                    reason="нет JDK или не скачаны android.jar/dx.jar")
def test_built_apk_is_read_by_an_independent_parser(tmp_path):
    apk_module = pytest.importorskip("pyaxmlparser", reason="нет pyaxmlparser")
    out = str(tmp_path / "wallet.apk")
    apkbuild.build(out, cache=_tools_ready())
    apk = apk_module.APK(out)
    assert apk.get_package() == "io.bhydra.wallet"
    assert apk.get_main_activity() == "io.bhydra.wallet.MainActivity"
    assert "android.permission.INTERNET" in apk.get_permissions()
    assert apk.is_signed_v1() is True


@pytest.mark.skipif(_tools_ready() is None,
                    reason="нет JDK или не скачаны android.jar/dx.jar")
def test_built_dex_really_contains_the_wallet_shell_calls(tmp_path):
    """В байт-коде обязаны быть вызовы, без которых оболочка не кошелёк.

    «Собралось» ничего не доказывает: пропади из исходника
    `setDomStorageEnabled`, javac и dx отработают молча, APK установится — и
    приложение будет ТЕРЯТЬ приватный ключ при каждом закрытии, потому что он
    живёт в localStorage. Поэтому читаем таблицы самого DEX.
    """
    out = str(tmp_path / "wallet.apk")
    apkbuild.build(out, cache=_tools_ready())
    refs = apkbuild.dex_references(zipfile.ZipFile(out).read("classes.dex"))

    for call in apkbuild.REQUIRED_CALLS:
        assert call in refs["methods"], call
    # Тот же вывод должен давать и verify() — им пользуется сборщик.
    assert apkbuild.verify(out)["calls_ok"]
    # Негативный контроль: набор не «содержит что угодно».
    assert "Landroid/webkit/WebSettings;->setДомStorage" not in refs["methods"]


@pytest.mark.skipif(_tools_ready() is None,
                    reason="нет JDK или не скачаны android.jar/dx.jar")
def test_built_dex_has_no_androidx_and_no_invokedynamic(tmp_path):
    """Запрет androidx и лямбд проверяем по РЕЗУЛЬТАТУ, а не по тексту.

    Исходник читает соседний тест, но он смотрит на один файл; сюда попадает
    всё, что реально сдексовано, включая случайно затянутые зависимости.
    """
    out = str(tmp_path / "wallet.apk")
    apkbuild.build(out, cache=_tools_ready())
    types = apkbuild.dex_references(zipfile.ZipFile(out).read("classes.dex"))["types"]
    assert not [t for t in types if t.startswith("Landroidx/")]
    # invokedynamic из Java 8 dx не понимает — его следы это Landroid/lang/invoke.
    assert not [t for t in types if t.startswith("Ljava/lang/invoke/")]
    assert "Lio/bhydra/wallet/MainActivity;" in types


def test_dex_references_refuses_something_that_is_not_a_dex():
    with pytest.raises(ValueError, match="DEX"):
        apkbuild.dex_references(b"PK\x03\x04" + b"\0" * 200)


def test_android_sources_avoid_androidx_and_resources():
    """Оболочка обязана оставаться собираемой БЕЗ Android SDK.

    androidx лежит на Google Maven, а R.string/@mipmap требуют aapt2 из SDK —
    и то и другое ломает сборку без SDK.
    """
    import re

    java = os.path.join(apkbuild.PROJECT, "java", "io", "bhydra", "wallet",
                        "MainActivity.java")
    text = open(java, encoding="utf-8").read()
    # Комментарии убираем: там эти же слова стоят в объяснении, ПОЧЕМУ их нет.
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    source = "\n".join(line.split("//")[0] for line in text.splitlines())
    assert "androidx" not in source
    # android.R.* — это ресурсы САМОЙ системы, они есть всегда и aapt2 не
    # требуют. Запрещены только ресурсы приложения (свой R).
    own = source.replace("android.R.", "")
    assert not any(ref in own for ref in ("R.string", "R.layout", "R.id",
                                          "R.mipmap", "R.drawable"))
    # Лямбд быть не должно: dx не понимает invokedynamic из Java 8.
    assert "->" not in source
    # И хранилище должно быть включено — в нём живёт приватный ключ.
    assert "setDomStorageEnabled(true)" in source


def test_target_sdk_stays_within_v1_signing():
    """targetSdk ≤ 29: с 30-й версии Android требует подпись v2, а её делает
    apksigner из SDK, которого у нас нет."""
    path = os.path.join(apkbuild.PROJECT, "AndroidManifest.xml")
    text = open(path, encoding="utf-8").read()
    import re
    found = re.search(r'targetSdkVersion="(\d+)"', text)
    assert found and int(found.group(1)) <= 29
