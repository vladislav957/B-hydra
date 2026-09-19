"""Версия B-hydra Core и сравнение версий.

Отдельный модуль, а не константа в `__init__.py`, по одной причине: сравнением
версий пользуется `updater.py`, а импортировать ради этого весь пакет — значит
тянуть блокчейн, сеть и крипту туда, где нужно сравнить два числа.

⚠️ Теги в релизах писались ПО-РАЗНОМУ: `v0.0.7`, `v.0.0.8`, `v.0.0.9`, `v0.1.0`
— точка после «v» то есть, то нет. Это не выдумка для красоты разбора, а то,
что лежит в репозитории прямо сейчас. Сравнение обязано считать их одной
схемой, иначе обновление с `v.0.0.9` на `v0.1.0` либо не предложится вовсе,
либо предложится в обратную сторону.
"""

import re

__all__ = ["VERSION", "parse", "is_newer", "format_version"]

VERSION = "0.1.0"

# Сколько числовых полей сравнивается. Дополняются нулями, поэтому «0.1» и
# «0.1.0» — одна и та же версия, а не разные.
_FIELDS = 4

_SPLIT = re.compile(r"^[vV]?\.?(?P<numbers>\d+(?:\.\d+)*)(?:[-+.]?(?P<pre>.*))?$")

# Предрелизы идут ПЕРЕД релизом: 1.0.0-rc1 старше 1.0.0 быть не может.
_RELEASE = 1
_PRERELEASE = 0


def parse(text):
    """Версия → кортеж, который можно сравнивать обычным `<`.

    Возвращает `None`, если строка на версию не похожа. Именно `None`, а не
    нули: «не разобралось» и «версия 0.0.0» — разные вещи, и молча считать
    мусор нулевой версией значит предложить обновление на что попало.
    """
    if not isinstance(text, str):
        return None
    match = _SPLIT.match(text.strip())
    if match is None:
        return None

    numbers = [int(part) for part in match.group("numbers").split(".")]
    # ⚠️ Хвост ДОПОЛНЯЕТСЯ нулями, но не ОБРЕЗАЕТСЯ. Дополнение нужно, чтобы
    # «0.1» и «0.1.0» считались одной версией; обрезка же молча схлопнула бы
    # 1.2.3.4 и 1.2.3.5 в одну — то есть обновление между ними не предложилось
    # бы вовсе. Лишние поля никому не мешают: кортежи сравниваются поэлементно.
    if len(numbers) < _FIELDS:
        numbers = numbers + [0] * (_FIELDS - len(numbers))

    pre = (match.group("pre") or "").strip().lower()
    if not pre:
        return (tuple(numbers), _RELEASE, ())
    # Предрелизы сравниваются между собой по частям: rc2 новее rc1.
    parts = tuple(int(chunk) if chunk.isdigit() else chunk
                  for chunk in re.split(r"[-.+]", pre) if chunk)
    return (tuple(numbers), _PRERELEASE, parts)


def is_newer(candidate, current=VERSION) -> bool:
    """Строго ли `candidate` новее `current`.

    ⚠️ СТРОГО, и это защита, а не придирка: равенство означает «у нас уже эта
    сборка», и разрешить установку «той же» версии значит разрешить подсунуть
    под её именем другой файл. Отсюда же отказ при неразобранной версии —
    обновление, про которое нельзя сказать, что оно новее, не ставится.
    """
    left, right = parse(candidate), parse(current)
    if left is None or right is None:
        return False
    try:
        return left > right
    except TypeError:
        # Предрелизы с несравнимыми частями («rc» против 1) — не рискуем.
        return False


def format_version(text) -> str:
    """Версия без префикса `v`/`v.` — для показа человеку."""
    match = _SPLIT.match(str(text).strip())
    if match is None:
        return str(text).strip()
    pre = match.group("pre")
    return match.group("numbers") + (f"-{pre}" if pre else "")
