"""
axml.py — бинарный AndroidManifest.xml (AXML) с нуля, без Android SDK.

Чтобы собрать APK, манифест нужен не текстовым, а в двоичном формате Android
(ресурсные чанки со строковым пулом). Обычно его делает `aapt2` из Android SDK.
SDK — это ~500 МБ с `dl.google.com`, и он нужен ровно ради одного этого шага:
компилятор Java есть в JDK, дексер (`dx`) лежит на Maven Central, подпись
делает `jarsigner` из того же JDK, а ZIP умеет стандартная библиотека.

Поэтому формат реализован здесь — как в проекте уже сделаны свой SHA, своя
ECDSA, свой QR, свой PNG и свой X.509. Структура описана в
`ResourceTypes.h` из AOSP:

    RES_XML_TYPE(0x0003)
      RES_STRING_POOL_TYPE(0x0001)        строковый пул (UTF-16)
      RES_XML_RESOURCE_MAP_TYPE(0x0180)   имя атрибута → его ресурсный id
      RES_XML_START_NAMESPACE_TYPE(0x0100)
      RES_XML_START_ELEMENT_TYPE(0x0102)  … вложенность …
      RES_XML_END_ELEMENT_TYPE(0x0103)
      RES_XML_END_NAMESPACE_TYPE(0x0101)

⚠️ Атрибуты пространства `android:` опознаются НЕ по имени, а по 32-битному
ресурсному id в карте ресурсов. Имя в пуле — для человека; система смотрит
только id. Неверный id — атрибут молча игнорируется, и приложение,
например, окажется без доступа в интернет. Поэтому таблица ниже точная, а
результат сверяется независимым разборщиком в tests/test_axml.py.

⚠️ Строки пишутся в UTF-16: у UTF-8 в этом формате своя запись длины
(два варинта, и для длинных строк она отличается), а выигрыш в размере на
манифесте в полсотни строк нулевой.
"""

from __future__ import annotations

import struct
import xml.etree.ElementTree as ET

ANDROID_NS = "http://schemas.android.com/apk/res/android"

# Типы чанков.
_RES_STRING_POOL = 0x0001
_RES_XML = 0x0003
_RES_XML_START_NAMESPACE = 0x0100
_RES_XML_END_NAMESPACE = 0x0101
_RES_XML_START_ELEMENT = 0x0102
_RES_XML_END_ELEMENT = 0x0103
_RES_XML_RESOURCE_MAP = 0x0180

# Типы значений атрибутов (Res_value::dataType).
TYPE_REFERENCE = 0x01
TYPE_STRING = 0x03
TYPE_INT_DEC = 0x10
TYPE_INT_HEX = 0x11
TYPE_INT_BOOLEAN = 0x12

NO_ENTRY = 0xFFFFFFFF

# Ресурсные id атрибутов android:*. Значения — из public.xml платформы; они
# зафиксированы навсегда, потому что по ним читают уже собранные приложения.
ATTRIBUTE_IDS = {
    "theme": 0x01010000,
    "label": 0x01010001,
    "icon": 0x01010002,
    "name": 0x01010003,
    "permission": 0x01010006,
    "process": 0x01010011,
    "debuggable": 0x0101000F,
    "exported": 0x01010010,
    "enabled": 0x0101000E,
    "configChanges": 0x0101001F,
    "screenOrientation": 0x0101001E,
    "value": 0x01010024,
    "resource": 0x01010025,
    "minSdkVersion": 0x0101020C,
    "versionCode": 0x0101021B,
    "versionName": 0x0101021C,
    "targetSdkVersion": 0x01010270,
    "allowBackup": 0x01010280,
    "required": 0x0101028E,
    "hardwareAccelerated": 0x010102D3,
    "supportsRtl": 0x010103AF,
    "compileSdkVersion": 0x01010572,
    "compileSdkVersionCodename": 0x01010573,
    "usesCleartextTraffic": 0x0101076E,
    "networkSecurityConfig": 0x0101081F,
    "roundIcon": 0x0101052C,
    "appComponentFactory": 0x0101057A,
}


class _StringPool:
    """Пул строк. Порядок важен: имена атрибутов идут ПЕРВЫМИ.

    Карта ресурсов — это массив id, стоящий параллельно началу пула: id №k
    относится к строке №k. Если порядок сбить, каждый атрибут получит чужой
    ресурсный id, и манифест «разберётся», но будет означать не то.
    """

    def __init__(self):
        self._items = []
        self._index = {}

    def add(self, text) -> int:
        if text is None:
            return -1
        if text not in self._index:
            self._index[text] = len(self._items)
            self._items.append(text)
        return self._index[text]

    def index(self, text) -> int:
        return self._index.get(text, -1) if text is not None else -1

    def __len__(self):
        return len(self._items)

    def encode(self) -> bytes:
        offsets, data = [], bytearray()
        for text in self._items:
            offsets.append(len(data))
            raw = text.encode("utf-16-le")
            # Длина В СИМВОЛАХ UTF-16, затем данные и нулевой терминатор.
            data += struct.pack("<H", len(raw) // 2) + raw + b"\x00\x00"
        while len(data) % 4:                       # чанки выравниваются по 4
            data += b"\x00"
        header = 28
        strings_start = header + len(offsets) * 4
        chunk = struct.pack("<IIIII", len(self._items), 0, 0,
                            strings_start, 0)
        body = chunk + b"".join(struct.pack("<I", off) for off in offsets) + bytes(data)
        return (struct.pack("<HHI", _RES_STRING_POOL, header,
                            8 + len(body)) + body)


def _chunk(kind: int, header_size: int, body: bytes) -> bytes:
    return struct.pack("<HHI", kind, header_size, 8 + len(body)) + body


def _typed_value(text: str, pool: _StringPool):
    """Определяет тип значения атрибута по его записи.

    Строка — это ПОСЛЕДНЕЕ средство: числа и «true/false» Android ждёт
    числами, а строку в поле версии просто не поймёт.
    """
    lowered = text.strip().lower()
    if lowered in ("true", "false"):
        return TYPE_INT_BOOLEAN, 0xFFFFFFFF if lowered == "true" else 0
    if lowered.startswith("0x"):
        try:
            return TYPE_INT_HEX, int(lowered, 16)
        except ValueError:
            pass
    try:
        value = int(text)
        if -2 ** 31 <= value < 2 ** 32:
            return TYPE_INT_DEC, value & 0xFFFFFFFF
    except ValueError:
        pass
    return TYPE_STRING, pool.index(text)


def encode(xml_text: str) -> bytes:
    """Текстовый манифест → бинарный AXML."""
    root = ET.fromstring(xml_text)
    pool = _StringPool()

    # 1. Имена атрибутов android:* — первыми и в порядке появления, чтобы
    #    карта ресурсов встала параллельно.
    resource_ids = []
    for element in root.iter():
        for key in element.attrib:
            if key.startswith(f"{{{ANDROID_NS}}}"):
                short = key.split("}", 1)[1]
                if short not in ATTRIBUTE_IDS:
                    raise ValueError(f"неизвестный атрибут android:{short} — "
                                     f"нужен его ресурсный id")
                if pool.index(short) < 0:
                    pool.add(short)
                    resource_ids.append(ATTRIBUTE_IDS[short])

    # 2. Остальные строки: имена без пространства имён, теги, значения.
    for element in root.iter():
        for key, value in element.attrib.items():
            if not key.startswith("{"):
                pool.add(key)
            if _typed_value(value, pool)[0] == TYPE_STRING:
                pool.add(value)
    for element in root.iter():
        pool.add(element.tag.split("}")[-1])
    prefix_index = pool.add("android")
    uri_index = pool.add(ANDROID_NS)

    # 3. Значения-строки могли добавиться после — индексы берём уже готовыми.
    body = bytearray()
    body += _chunk(_RES_XML_RESOURCE_MAP, 8,
                   b"".join(struct.pack("<I", rid) for rid in resource_ids))
    namespace = struct.pack("<IIII", 1, NO_ENTRY, prefix_index, uri_index)
    body += _chunk(_RES_XML_START_NAMESPACE, 16, namespace)

    def write_element(element, depth=1):
        out = bytearray()
        attributes = bytearray()
        count = 0
        for key, value in element.attrib.items():
            if key.startswith(f"{{{ANDROID_NS}}}"):
                ns_index = uri_index
                name_index = pool.index(key.split("}", 1)[1])
            elif key.startswith("{"):
                raise ValueError(f"чужое пространство имён: {key}")
            else:
                ns_index = -1
                name_index = pool.index(key)
            kind, data = _typed_value(value, pool)
            raw = pool.index(value) if kind == TYPE_STRING else -1
            attributes += struct.pack("<IIIHBBI",
                                      ns_index & 0xFFFFFFFF,
                                      name_index & 0xFFFFFFFF,
                                      raw & 0xFFFFFFFF,
                                      8, 0, kind, data & 0xFFFFFFFF)
            count += 1
        head = struct.pack("<IIIIHHHHHH", depth, NO_ENTRY, NO_ENTRY,
                           pool.index(element.tag.split("}")[-1]),
                           20, 20, count, 0, 0, 0)
        out += _chunk(_RES_XML_START_ELEMENT, 16, head + bytes(attributes))
        for child in element:
            out += write_element(child, depth + 1)
        tail = struct.pack("<IIII", depth, NO_ENTRY, NO_ENTRY,
                           pool.index(element.tag.split("}")[-1]))
        out += _chunk(_RES_XML_END_ELEMENT, 16, tail)
        return out

    body += write_element(root)
    body += _chunk(_RES_XML_END_NAMESPACE, 16, namespace)
    return _chunk(_RES_XML, 8, pool.encode() + bytes(body))


if __name__ == "__main__":
    sample = f"""<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="{ANDROID_NS}" package="io.bhydra.wallet">
  <uses-sdk android:minSdkVersion="24" android:targetSdkVersion="29"/>
  <uses-permission android:name="android.permission.INTERNET"/>
  <application android:label="B-hydra" android:usesCleartextTraffic="true">
    <activity android:name=".MainActivity" android:exported="true"/>
  </application>
</manifest>"""
    data = encode(sample)
    print(f"AXML: {len(data)} байт, сигнатура {data[:4].hex()}")
