"""
apkbuild.py — сборка APK кошелька БЕЗ Android SDK.

Обычный путь к APK — Android Studio или `gradle assembleDebug`, а это ~500 МБ
SDK с `dl.google.com`. Здесь показано, что APK — это просто ZIP с четырьмя
понятными частями, и все они собираются доступными средствами:

    AndroidManifest.xml   двоичный AXML   → b_hydra/axml.py (свой, с нуля)
    classes.dex           байт-код Dalvik → dx с Maven Central (официальный)
    META-INF/*            подпись JAR     → jarsigner из обычного JDK
    сам архив             ZIP             → zipfile из стандартной библиотеки

Компилирует javac из того же JDK, а заголовки Android берутся из старого
артефакта `com.google.android:android` на Maven Central.

⚠️ Ресурсов (`resources.arsc`) здесь нет: их собирает `aapt2`, а он только в
SDK. Поэтому в манифесте не должно быть ссылок вида `@mipmap/ic_launcher` —
такие атрибуты сборщик выбрасывает и говорит об этом. Приложение получает
системную иконку по умолчанию. Сборка через Gradle (у неё aapt2 есть) иконку
поставит.

⚠️ Подпись — схемы v1 (JAR). Начиная с targetSdk 30 Android требует v2, а её
делает `apksigner` из SDK. Поэтому в манифесте targetSdk 29: этого достаточно
для установки на современные телефоны вручную и не требует SDK. Для магазина
приложений нужен полноценный SDK и подпись v2.

Запуск:
    python -m b_hydra.apkbuild --out bhydra-wallet.apk
"""

from __future__ import annotations

import hashlib
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
import zlib

if __name__ == "__main__" and __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    __package__ = "b_hydra"

from .axml import ANDROID_NS, encode

MAVEN = "https://repo1.maven.org/maven2"
# Заголовки Android для компиляции. Версия древняя (API 15), но всё, чем
# пользуется оболочка — Activity, WebView, SharedPreferences — там уже есть,
# а на Maven Central это единственный доступный android.jar.
ANDROID_JAR = (f"{MAVEN}/com/google/android/android/4.1.1.4/android-4.1.1.4.jar",
               "android-4.1.1.4.jar")
# Официальный дексер Dalvik, перепакованный для Maven Central.
DX_JAR = (f"{MAVEN}/com/jakewharton/android/repackaged/dalvik-dx/16.0.1/"
          "dalvik-dx-16.0.1.jar", "dalvik-dx-16.0.1.jar")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT = os.path.join(_ROOT, "android", "app", "src", "main")


def _download(url: str, name: str, cache: str) -> str:
    """Скачивает инструмент в кэш (или берёт уже скачанный)."""
    os.makedirs(cache, exist_ok=True)
    path = os.path.join(cache, name)
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return path
    print(f"  качаю {name}…", flush=True)
    with urllib.request.urlopen(url, timeout=300) as source, \
            open(path + ".part", "wb") as target:
        shutil.copyfileobj(source, target)
    os.replace(path + ".part", path)
    return path


def _tool(name: str) -> str:
    found = shutil.which(name)
    if not found:
        raise RuntimeError(f"нужен {name} из JDK (проверьте PATH)")
    return found


def strip_resource_references(xml_text: str):
    """Убирает атрибуты со ссылками на ресурсы (`@mipmap/...`).

    Без resources.arsc такая ссылка — битый указатель: система не сможет её
    разрешить. Молча оставлять её нельзя, поэтому список выброшенного
    возвращается наружу и печатается.
    """
    root = ET.fromstring(xml_text)
    dropped = []
    for element in root.iter():
        for key in list(element.attrib):
            if element.attrib[key].startswith("@"):
                dropped.append(f"{key.split('}')[-1]}={element.attrib[key]}")
                del element.attrib[key]
    ET.register_namespace("android", ANDROID_NS)
    return ET.tostring(root, encoding="unicode"), dropped


def build(out_path: str, cache: str = None, keystore: str = None,
          storepass: str = "bhydra", alias: str = "bhydra") -> str:
    """Собирает и подписывает APK. Возвращает путь к нему."""
    cache = cache or os.path.join(tempfile.gettempdir(), "bhydra-apk-tools")
    android_jar = _download(ANDROID_JAR[0], ANDROID_JAR[1], cache)
    dx_jar = _download(DX_JAR[0], DX_JAR[1], cache)

    with tempfile.TemporaryDirectory() as work:
        classes = os.path.join(work, "classes")
        os.makedirs(classes)

        sources = []
        for folder, _dirs, files in os.walk(os.path.join(PROJECT, "java")):
            sources += [os.path.join(folder, f) for f in files if f.endswith(".java")]
        if not sources:
            raise RuntimeError("не найдено ни одного .java")

        print("  компилирую java…", flush=True)
        # --release 8: dx не понимает invokedynamic из Java 8+, поэтому в
        # исходниках нет лямбд, а версия байт-кода берётся пониже.
        subprocess.run([_tool("javac"), "--release", "8", "-nowarn",
                        "-classpath", android_jar, "-d", classes] + sources,
                       check=True, capture_output=True, text=True)

        print("  собираю classes.dex…", flush=True)
        dex = os.path.join(work, "classes.dex")
        subprocess.run([_tool("java"), "-cp", dx_jar, "com.android.dx.command.Main",
                        "--dex", f"--output={dex}", classes],
                       check=True, capture_output=True, text=True)

        with open(os.path.join(PROJECT, "AndroidManifest.xml"), encoding="utf-8") as f:
            manifest_text = f.read()
        manifest_text, dropped = strip_resource_references(manifest_text)
        for item in dropped:
            print(f"  ⚠ выброшена ссылка на ресурс: {item} (нет resources.arsc)")
        manifest = encode(manifest_text)

        unsigned = os.path.join(work, "unsigned.apk")
        with zipfile.ZipFile(unsigned, "w", zipfile.ZIP_DEFLATED) as apk:
            # Манифест кладём ПЕРВЫМ: так делают все сборщики, и некоторые
            # разборщики ищут его в начале архива.
            apk.writestr("AndroidManifest.xml", manifest)
            apk.write(dex, "classes.dex")

        keystore = keystore or os.path.join(cache, "debug.keystore")
        if not os.path.exists(keystore):
            print("  создаю ключ подписи…", flush=True)
            subprocess.run([_tool("keytool"), "-genkeypair", "-keystore", keystore,
                            "-storepass", storepass, "-keypass", storepass,
                            "-alias", alias, "-keyalg", "RSA", "-keysize", "2048",
                            "-validity", "10000", "-dname",
                            "CN=B-hydra, OU=Wallet, O=B-hydra, C=RU"],
                           check=True, capture_output=True, text=True)

        print("  подписываю…", flush=True)
        shutil.copy(unsigned, out_path)
        subprocess.run([_tool("jarsigner"), "-keystore", keystore,
                        "-storepass", storepass, "-keypass", storepass,
                        "-digestalg", "SHA-256", "-sigalg", "SHA256withRSA",
                        out_path, alias],
                       check=True, capture_output=True, text=True)
    return out_path


def _uleb128(data: bytes, offset: int):
    """LEB128 без знака — так DEX хранит длины строк."""
    result = shift = 0
    while True:
        byte = data[offset]
        offset += 1
        result |= (byte & 0x7F) << shift
        shift += 7
        if not byte & 0x80:
            return result, offset


def dex_references(dex: bytes) -> dict:
    """Разбирает таблицы DEX: строки, типы и ССЫЛКИ НА МЕТОДЫ.

    Читаем формат сами, а не глазами дексера: `dx` сообщает об успехе, даже
    если из исходника пропал вызов, и «собралось» ничего не говорит о том, ЧТО
    собралось. Здесь видно ровно то, что окажется на телефоне: например,
    `WebSettings->setDomStorageEnabled` — без него кошелёк терял бы приватный
    ключ при каждом закрытии, а сборка проходила бы как ни в чём не бывало.
    """
    if dex[:4] != b"dex\n":
        raise ValueError("это не DEX")

    def u32(offset):
        return struct.unpack_from("<I", dex, offset)[0]

    string_count, string_off = u32(56), u32(60)
    type_count, type_off = u32(64), u32(68)
    method_count, method_off = u32(88), u32(92)

    strings = []
    for i in range(string_count):
        start = u32(string_off + 4 * i)
        _length, start = _uleb128(dex, start)
        strings.append(dex[start:dex.index(b"\0", start)].decode("utf-8", "replace"))
    types = [strings[u32(type_off + 4 * i)] for i in range(type_count)]

    methods = set()
    for i in range(method_count):
        class_idx, _proto = struct.unpack_from("<HH", dex, method_off + 8 * i)
        name = strings[u32(method_off + 8 * i + 4)]
        methods.add(f"{types[class_idx]}->{name}")
    return {"strings": strings, "types": types, "methods": methods}


#: Вызовы, без которых оболочка перестаёт быть кошельком (см. dex_references).
REQUIRED_CALLS = (
    "Landroid/webkit/WebSettings;->setJavaScriptEnabled",   # без JS страницы нет
    "Landroid/webkit/WebSettings;->setDomStorageEnabled",   # в localStorage ключ
    "Landroid/webkit/WebView;->loadUrl",                    # чем открывается кошелёк
    "Landroid/content/SharedPreferences;->getString",       # свой адрес узла
    "Landroid/content/SharedPreferences$Editor;->putString",
)


def verify(apk_path: str) -> dict:
    """Проверяет собранный APK тем, что есть под рукой."""
    report = {}
    with zipfile.ZipFile(apk_path) as apk:
        names = apk.namelist()
        report["entries"] = names
        report["has_manifest"] = "AndroidManifest.xml" in names
        report["has_dex"] = "classes.dex" in names
        report["signed"] = any(n.startswith("META-INF/") and
                               n.endswith((".RSA", ".DSA", ".EC")) for n in names)
        dex = apk.read("classes.dex")
        report["dex_magic"] = dex[:8] == b"dex\n035\x00"
        report["dex_adler32"] = zlib.adler32(dex[12:]) == struct.unpack_from("<I", dex, 8)[0]
        report["dex_sha1"] = hashlib.sha1(dex[32:]).digest() == dex[12:32]
        refs = dex_references(dex)
        report["missing_calls"] = [call for call in REQUIRED_CALLS
                                   if call not in refs["methods"]]
        report["calls_ok"] = not report["missing_calls"]
    check = subprocess.run([_tool("jarsigner"), "-verify", apk_path],
                           capture_output=True, text=True)
    report["jarsigner"] = check.stdout.strip().splitlines()[0] if check.stdout else ""
    report["jarsigner_ok"] = check.returncode == 0
    report["size"] = os.path.getsize(apk_path)
    return report


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Сборка APK кошелька B-hydra без Android SDK")
    parser.add_argument("--out", default="bhydra-wallet.apk")
    parser.add_argument("--cache", help="куда класть скачанные инструменты")
    parser.add_argument("--keystore", help="свой keystore (иначе отладочный)")
    args = parser.parse_args()

    print(f"Сборка {args.out}")
    path = build(args.out, cache=args.cache, keystore=args.keystore)
    report = verify(path)
    print(f"\nГотово: {path} ({report['size']} байт)")
    print(f"  содержимое : {', '.join(report['entries'][:6])}")
    print(f"  DEX         : {'да' if report['dex_magic'] else 'НЕТ'}"
          f", свои суммы {'сходятся' if report['dex_adler32'] and report['dex_sha1'] else 'НЕ СХОДЯТСЯ'}")
    print(f"  вызовы      : {'все на месте' if report['calls_ok'] else 'ПОТЕРЯНЫ: ' + ', '.join(report['missing_calls'])}")
    print(f"  подпись     : {'да' if report['signed'] else 'НЕТ'} "
          f"({report['jarsigner']})")
    if not args.keystore:
        print("\n⚠ Подписано ОТЛАДОЧНЫМ ключом — для раздачи сделайте свой:")
        print("  keytool -genkeypair -keystore mine.jks -keyalg RSA -keysize 4096 \\")
        print("          -validity 10000 -alias bhydra")


if __name__ == "__main__":
    main()
