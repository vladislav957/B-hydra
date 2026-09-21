"""B-hydra Core version and version comparison.

A module of its own rather than a constant in `__init__.py`, for one reason:
`updater.py` needs the comparison, and importing the whole package for that
would drag the blockchain, the network and the crypto into a place where two
numbers have to be compared.

⚠️ Release tags were written INCONSISTENTLY: `v0.0.7`, `v.0.0.8`, `v.0.0.9`,
`v0.1.0` — the dot after the "v" comes and goes. This is not an invented
edge case to make the parser look clever, it is what sits in the repository
right now. The comparison must treat them as one scheme, otherwise an update
from `v.0.0.9` to `v0.1.0` would either never be offered or be offered
backwards.
"""

import re

__all__ = ["VERSION", "parse", "is_newer", "format_version"]

VERSION = "0.1.0"

# How many numeric fields take part in the comparison. They are padded with
# zeros, so "0.1" and "0.1.0" are one and the same version, not two.
_FIELDS = 4

_SPLIT = re.compile(r"^[vV]?\.?(?P<numbers>\d+(?:\.\d+)*)(?:[-+.]?(?P<pre>.*))?$")

# Pre-releases come BEFORE the release: 1.0.0-rc1 can never outrank 1.0.0.
_RELEASE = 1
_PRERELEASE = 0


def parse(text):
    """Version -> a tuple that plain `<` can compare.

    Returns `None` if the string does not look like a version. `None`
    specifically, not zeros: "did not parse" and "version 0.0.0" are
    different things, and silently treating garbage as version zero means
    offering an update to just about anything.
    """
    if not isinstance(text, str):
        return None
    match = _SPLIT.match(text.strip())
    if match is None:
        return None

    numbers = [int(part) for part in match.group("numbers").split(".")]
    # ⚠️ The tail is PADDED with zeros but never TRUNCATED. The padding is
    # what makes "0.1" and "0.1.0" the same version; truncation, by contrast,
    # would silently collapse 1.2.3.4 and 1.2.3.5 into one — meaning an update
    # between them would never be offered at all. Extra fields hurt nobody:
    # tuples are compared element by element.
    if len(numbers) < _FIELDS:
        numbers = numbers + [0] * (_FIELDS - len(numbers))

    pre = (match.group("pre") or "").strip().lower()
    if not pre:
        return (tuple(numbers), _RELEASE, ())
    # Pre-releases are compared against each other piecewise: rc2 beats rc1.
    parts = tuple(int(chunk) if chunk.isdigit() else chunk
                  for chunk in re.split(r"[-.+]", pre) if chunk)
    return (tuple(numbers), _PRERELEASE, parts)


def is_newer(candidate, current=VERSION) -> bool:
    """Whether `candidate` is STRICTLY newer than `current`.

    ⚠️ STRICTLY, and that is a defence rather than pedantry: equality means
    "we already have this build", and allowing "the same" version to install
    means allowing a different file to be slipped in under its name. The same
    reasoning drives the refusal on an unparseable version — an update that
    cannot be shown to be newer does not get installed.
    """
    left, right = parse(candidate), parse(current)
    if left is None or right is None:
        return False
    try:
        return left > right
    except TypeError:
        # Pre-releases with incomparable parts ("rc" against 1) — do not risk it.
        return False


def format_version(text) -> str:
    """The version without the `v`/`v.` prefix — for showing to a person."""
    match = _SPLIT.match(str(text).strip())
    if match is None:
        return str(text).strip()
    pre = match.group("pre")
    return match.group("numbers") + (f"-{pre}" if pre else "")
