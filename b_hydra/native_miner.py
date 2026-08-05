"""
native_miner.py — мост к нативному майнеру (`cpp/bhydra_miner.cpp`).

Перебор nonce — единственное место в проекте, где Python считает САМ и много:
миллионы SHA-512 подряд, и всё в один поток из-за GIL. Нативный майнер делает
то же самое на всех ядрах.

Работает СРЕЗАМИ по времени: Python просит «поищи секунду», получает результат
и решает, продолжать ли. Так сохраняется всё, ради чего переписывался цикл, —
возможность бросить блок, когда сосед нашёл свой раньше, и отчёт о скорости.
Отдать управление насовсем нельзя: тогда узел снова стал бы глухим на время
майнинга.

⚠️ Результат нативного майнера ПРОВЕРЯЕТСЯ (`Block._mine_native`): хеш
пересчитывается своим кодом и сверяется с порогом. Внешней программе на слово
здесь не верят — ошибка в ней иначе прошла бы дальше и всплыла уже как
отвергнутый сетью блок.

Не собран — не беда: `default()` вернёт None, и майнинг пойдёт на Python.
"""

import json
import os
import shutil
import subprocess

#: Путь к бинарнику можно задать явно; `off`/`0` полностью выключает нативный
#: путь (удобно для тестов и для сравнения скорости).
MINER_ENV = "BHYDRA_MINER"
BINARY_NAME = "bhydra_miner"
#: Сколько секунд длится один срез перебора. Меньше — быстрее реакция на чужой
#: блок, больше — меньше накладных расходов на запуск процесса.
SLICE_SECONDS = 1.0

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_cached = False
_default = None


class NativeMiner:
    """Запускает `bhydra_miner` и разбирает его ответ."""

    def __init__(self, path, threads=0, slice_seconds=SLICE_SECONDS):
        self.path = path
        self.threads = int(threads)
        self.slice_seconds = float(slice_seconds)

    def selftest(self) -> bool:
        return bool(self._run("selftest").get("ok"))

    def mine(self, prefix_hex, target_hex, start_nonce, seconds=None):
        """Один срез перебора. None — если бинарник не отработал."""
        answer = self._run("mine", prefix_hex, target_hex, int(start_nonce),
                           self.threads, seconds or self.slice_seconds)
        if "error" in answer or "attempts" not in answer:
            return None
        return answer

    def benchmark(self, seconds=2.0, threads=None):
        """Скорость перебора, хешей в секунду."""
        answer = self._run("bench", seconds,
                           self.threads if threads is None else threads)
        elapsed = float(answer.get("seconds") or 0)
        return (answer.get("attempts", 0) / elapsed) if elapsed else 0.0

    def _run(self, *args):
        try:
            result = subprocess.run(
                [self.path, *[str(a) for a in args]],
                capture_output=True, text=True,
                # Срез плюс щедрый запас на запуск: зависший бинарник не должен
                # останавливать узел навсегда.
                timeout=max(30.0, self.slice_seconds * 10))
        except (OSError, subprocess.SubprocessError):
            return {}
        try:
            answer = json.loads(result.stdout or "{}")
        except ValueError:
            return {}
        return answer if isinstance(answer, dict) else {}


def find(path=None):
    """Ищет бинарник: явный путь → переменная окружения → PATH → корень проекта."""
    candidate = path or os.environ.get(MINER_ENV)
    if candidate:
        if str(candidate).lower() in ("off", "0", "no", "none"):
            return None
        return candidate if os.path.exists(candidate) else None
    found = shutil.which(BINARY_NAME)
    if found:
        return found
    local = os.path.join(_ROOT, BINARY_NAME)
    return local if os.path.exists(local) else None


def default():
    """Готовый майнер для этой машины или None. Результат запоминается.

    Проверяется не только наличие файла, но и `selftest`: битый или чужой
    бинарник с тем же именем не должен молча стать майнером.
    """
    global _cached, _default
    if _cached:
        return _default
    _cached = True
    path = find()
    if path is None:
        _default = None
        return None
    miner = NativeMiner(path)
    _default = miner if miner.selftest() else None
    return _default


def reset():
    """Забыть найденный майнер (для тестов и после пересборки)."""
    global _cached, _default
    _cached = False
    _default = None
