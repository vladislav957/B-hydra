"""
gpu_miner.py — перебор nonce на видеокарте через OpenCL.

ПОЧЕМУ ИМЕННО GPU И ИМЕННО ЗДЕСЬ. Перебор nonce — единственное место проекта,
где считается по-настоящему много и где попытки НЕ ЗАВИСЯТ друг от друга.
Именно такая работа и раскладывается на видеокарту: тысячи ядер берут по
своему nonce, а обмениваться им между собой не нужно вообще. Всё остальное в
B-hydra (подпись, проверка цепочки, сеть) на GPU не ложится — там короткие
зависимые цепочки операций, и видеокарта там медленнее процессора.

ПОЧЕМУ OpenCL, А НЕ CUDA. CUDA работает только на NVIDIA и требует
установленного `nvcc`. OpenCL один и тот же на NVIDIA, AMD и Intel, а ядро
компилирует драйвер прямо во время запуска — собирать заранее ничего не нужно.

ПОЧЕМУ ctypes, А НЕ pyopencl. У проекта ноль обязательных зависимостей, и это
правило. `libOpenCL` есть в системе у всех, у кого есть драйвер видеокарты,
поэтому библиотека грузится напрямую — ровно как в `native_ec.py`.
⚠️ У ctypes ОБЯЗАТЕЛЬНЫ `argtypes`/`restype`: handle'ы OpenCL — указатели, и
без объявления ctypes обрежет их до int. Это не отказ, а молчаливая порча.

ИНТЕРФЕЙС ТОТ ЖЕ, ЧТО У `native_miner.py` (`selftest`/`mine`/`benchmark`),
поэтому `Block.mine_block(miner=…)` принимает этот майнер без единой правки, а
вместе с ним достаются даром и срезы по времени (право бросить блок), и
ГЛАВНОЕ — проверка результата: `Block._mine_native` пересчитывает хеш своим
кодом и сверяет с порогом. Видеокарте на слово не верят.

⚠️ ЧЕГО ЗДЕСЬ НЕ ПРОВЕРЕНО. Настоящей видеокарты в контейнере разработки нет —
ядро гоняется на CPU-устройстве OpenCL (POCL). Поэтому проверена ПРАВИЛЬНОСТЬ
(хеш совпадает с Python байт-в-байт, найденный nonce настоящий), но не скорость
на живом железе и не особенности драйверов конкретных вендоров.
"""

import ctypes
import os
import time

from . import sha2

#: Явный выбор устройства: `off` выключает GPU совсем, число — номер устройства
#: в общем списке, строка — часть имени («nvidia», «radeon»).
GPU_ENV = "BHYDRA_GPU"

#: Сколько секунд длится один срез перебора. Как и у нативного майнера: меньше —
#: быстрее реакция на чужой блок, больше — меньше накладных расходов.
SLICE_SECONDS = 1.0

#: Сколько nonce берёт на себя один work-item за запуск ядра. Запуск ядра стоит
#: десятки микросекунд, поэтому одна попытка на work-item — это почти чистые
#: накладные расходы.
NONCES_PER_ITEM = 64

#: Сколько work-item'ов запускается разом. Видеокарта тем быстрее, чем больше
#: независимой работы ей дать: тысячи ядер должны быть заняты все сразу.
#: ⚠️ Подбиралось на CPU-устройстве OpenCL (настоящей видеокарты в контейнере
#: разработки нет), поэтому это разумное умолчание, а не выверенный оптимум для
#: конкретного железа. Меняется параметром конструктора.
DEFAULT_WORK_SIZE = 1 << 16

#: nonce в ядре 64-битный. Python допускает сколь угодно большой, поэтому при
#: переполнении перебор заворачивается в ноль ЯВНО.
#: ⚠️ Без этого `ctypes.c_ulonglong(2**64)` молча даёт 0 — не отказ, а тихая
#: подмена: устройство искало бы с нуля, пока Python считает, что идёт дальше.
NONCE_CEILING = 1 << 64

_KERNEL_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "gpu", "bhydra_miner.cl")

# --- Константы OpenCL (cl.h) ---------------------------------------------------
CL_SUCCESS = 0
CL_TRUE = 1
CL_DEVICE_TYPE_CPU = 1 << 1
CL_DEVICE_TYPE_GPU = 1 << 2
CL_DEVICE_TYPE_ALL = 0xFFFFFFFF
CL_PLATFORM_NAME = 0x0902
CL_DEVICE_NAME = 0x102B
CL_DEVICE_TYPE = 0x1000
CL_DEVICE_MAX_COMPUTE_UNITS = 0x1002
CL_DEVICE_MAX_WORK_GROUP_SIZE = 0x1004
CL_DEVICE_ENDIAN_LITTLE = 0x1026
CL_MEM_READ_WRITE = 1 << 0
CL_MEM_READ_ONLY = 1 << 2
CL_MEM_COPY_HOST_PTR = 1 << 5
CL_PROGRAM_BUILD_LOG = 0x1183

_LIBRARY_NAMES = ("libOpenCL.so.1", "libOpenCL.so", "OpenCL.dll",
                  "/System/Library/Frameworks/OpenCL.framework/OpenCL")


class GPUError(Exception):
    """Видеокарта или OpenCL не отработали. Всегда означает откат на CPU."""


def _load_library():
    """Загружает libOpenCL и объявляет типы. None — если её в системе нет."""
    library = None
    for name in _LIBRARY_NAMES:
        try:
            library = ctypes.CDLL(name)
            break
        except OSError:
            continue
    if library is None:
        return None

    p, u, z = ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t
    pp, pu, pz = ctypes.POINTER(p), ctypes.POINTER(u), ctypes.POINTER(z)
    pi = ctypes.POINTER(ctypes.c_int)
    try:
        library.clGetPlatformIDs.argtypes = [u, pp, pu]
        library.clGetPlatformIDs.restype = ctypes.c_int
        library.clGetPlatformInfo.argtypes = [p, u, z, ctypes.c_void_p, pz]
        library.clGetPlatformInfo.restype = ctypes.c_int
        library.clGetDeviceIDs.argtypes = [p, ctypes.c_ulonglong, u, pp, pu]
        library.clGetDeviceIDs.restype = ctypes.c_int
        library.clGetDeviceInfo.argtypes = [p, u, z, ctypes.c_void_p, pz]
        library.clGetDeviceInfo.restype = ctypes.c_int
        library.clCreateContext.argtypes = [ctypes.c_void_p, u, pp,
                                            ctypes.c_void_p, ctypes.c_void_p, pi]
        library.clCreateContext.restype = p
        library.clCreateCommandQueue.argtypes = [p, p, ctypes.c_ulonglong, pi]
        library.clCreateCommandQueue.restype = p
        library.clCreateProgramWithSource.argtypes = [
            p, u, ctypes.POINTER(ctypes.c_char_p), pz, pi]
        library.clCreateProgramWithSource.restype = p
        library.clBuildProgram.argtypes = [p, u, pp, ctypes.c_char_p,
                                           ctypes.c_void_p, ctypes.c_void_p]
        library.clBuildProgram.restype = ctypes.c_int
        library.clGetProgramBuildInfo.argtypes = [p, p, u, z, ctypes.c_void_p, pz]
        library.clGetProgramBuildInfo.restype = ctypes.c_int
        library.clCreateKernel.argtypes = [p, ctypes.c_char_p, pi]
        library.clCreateKernel.restype = p
        library.clSetKernelArg.argtypes = [p, u, z, ctypes.c_void_p]
        library.clSetKernelArg.restype = ctypes.c_int
        library.clCreateBuffer.argtypes = [p, ctypes.c_ulonglong, z,
                                           ctypes.c_void_p, pi]
        library.clCreateBuffer.restype = p
        library.clEnqueueWriteBuffer.argtypes = [p, p, u, z, z, ctypes.c_void_p,
                                                 u, pp, pp]
        library.clEnqueueWriteBuffer.restype = ctypes.c_int
        library.clEnqueueReadBuffer.argtypes = [p, p, u, z, z, ctypes.c_void_p,
                                                u, pp, pp]
        library.clEnqueueReadBuffer.restype = ctypes.c_int
        library.clEnqueueNDRangeKernel.argtypes = [p, p, u, pz, pz, pz, u, pp, pp]
        library.clEnqueueNDRangeKernel.restype = ctypes.c_int
        library.clFinish.argtypes = [p]
        library.clFinish.restype = ctypes.c_int
        for name in ("clReleaseMemObject", "clReleaseKernel", "clReleaseProgram",
                     "clReleaseCommandQueue", "clReleaseContext"):
            getattr(library, name).argtypes = [p]
            getattr(library, name).restype = ctypes.c_int
    except AttributeError:
        return None            # библиотека есть, но это не OpenCL
    return library


def _info(library, getter, handle, code, size=256):
    buffer = ctypes.create_string_buffer(size)
    written = ctypes.c_size_t()
    if getter(handle, code, size, buffer, ctypes.byref(written)) != CL_SUCCESS:
        return b""
    return buffer.raw[:max(written.value - 1, 0)]


def devices():
    """Список устройств OpenCL: [{index, name, platform, kind, units}, …].

    Пустой список — не ошибка: у машины просто нет ни видеокарты, ни драйвера,
    и майнинг пойдёт как раньше.
    """
    library = _load_library()
    if library is None:
        return []
    count = ctypes.c_uint()
    if library.clGetPlatformIDs(0, None, ctypes.byref(count)) != CL_SUCCESS \
            or count.value == 0:
        return []
    platforms = (ctypes.c_void_p * count.value)()
    library.clGetPlatformIDs(count.value, platforms, None)

    found = []
    for platform in platforms:
        name = _info(library, library.clGetPlatformInfo, platform,
                     CL_PLATFORM_NAME).decode("utf-8", "replace")
        number = ctypes.c_uint()
        if library.clGetDeviceIDs(platform, CL_DEVICE_TYPE_ALL, 0, None,
                                  ctypes.byref(number)) != CL_SUCCESS:
            continue
        handles = (ctypes.c_void_p * number.value)()
        library.clGetDeviceIDs(platform, CL_DEVICE_TYPE_ALL, number.value,
                               handles, None)
        for handle in handles:
            kind = ctypes.c_ulonglong()
            library.clGetDeviceInfo(handle, CL_DEVICE_TYPE, 8,
                                    ctypes.byref(kind), None)
            units = ctypes.c_uint()
            library.clGetDeviceInfo(handle, CL_DEVICE_MAX_COMPUTE_UNITS, 4,
                                    ctypes.byref(units), None)
            little = ctypes.c_uint()
            library.clGetDeviceInfo(handle, CL_DEVICE_ENDIAN_LITTLE, 4,
                                    ctypes.byref(little), None)
            found.append({
                "index": len(found),
                "name": _info(library, library.clGetDeviceInfo, handle,
                              CL_DEVICE_NAME).decode("utf-8", "replace"),
                "platform": name,
                "kind": "gpu" if kind.value & CL_DEVICE_TYPE_GPU else
                        ("cpu" if kind.value & CL_DEVICE_TYPE_CPU else "other"),
                "units": units.value,
                "little_endian": bool(little.value),
                "_platform": platform,
                "_device": handle,
            })
    return found


def _pick(wanted=None):
    """Выбирает устройство: явное указание → первая видеокарта → что есть.

    ⚠️ Настоящая видеокарта предпочитается CPU-устройству OpenCL: последнее
    существует (POCL, драйверы Intel), но считает те же хеши медленнее, чем
    наш собственный нативный майнер на тех же ядрах, — смысла в нём нет.
    """
    available = devices()
    if not available:
        return None
    if wanted is not None and str(wanted).strip() != "":
        text = str(wanted).strip()
        if text.isdigit():
            index = int(text)
            return available[index] if 0 <= index < len(available) else None
        lowered = text.lower()
        for device in available:
            if lowered in device["name"].lower() \
                    or lowered in device["platform"].lower():
                return device
        return None
    for device in available:
        if device["kind"] == "gpu":
            return device
    return available[0]


class GPUMiner:
    """Перебор nonce на устройстве OpenCL.

    Интерфейс намеренно совпадает с `native_miner.NativeMiner`, поэтому
    `Block.mine_block(miner=…)` работает с обоими одинаково.
    """

    def __init__(self, device=None, work_size=DEFAULT_WORK_SIZE,
                 per_item=NONCES_PER_ITEM, slice_seconds=SLICE_SECONDS):
        self.library = _load_library()
        if self.library is None:
            raise GPUError("libOpenCL не найдена")
        self.device = device if isinstance(device, dict) else _pick(device)
        if self.device is None:
            raise GPUError("нет ни одного устройства OpenCL")
        # ⚠️ Слова состояния и цели кладутся в буферы в порядке хоста, поэтому
        # big-endian устройство прочитало бы их наизнанку и считало бы ЧУЖОЙ
        # хеш. Отказ здесь честнее молчаливо неверного PoW; на практике таких
        # видеокарт нет, но проверка стоит одного запроса.
        if not self.device.get("little_endian", True):
            raise GPUError(f"{self.device['name']}: устройство big-endian")
        self.work_size = int(work_size)
        self.per_item = int(per_item)
        self.slice_seconds = float(slice_seconds)
        self.name = self.device["name"]
        self.kind = self.device["kind"]
        self._context = None
        self._queue = None
        self._kernel = None
        self._program = None
        self._build()

    # --- Подготовка -----------------------------------------------------------
    def _build(self):
        """Контекст, очередь и скомпилированное драйвером ядро."""
        cl = self.library
        status = ctypes.c_int()
        handle = ctypes.c_void_p(self.device["_device"])
        self._context = cl.clCreateContext(None, 1, ctypes.byref(handle),
                                           None, None, ctypes.byref(status))
        if status.value != CL_SUCCESS or not self._context:
            raise GPUError(f"clCreateContext: {status.value}")
        self._queue = cl.clCreateCommandQueue(self._context, handle, 0,
                                              ctypes.byref(status))
        if status.value != CL_SUCCESS or not self._queue:
            raise GPUError(f"clCreateCommandQueue: {status.value}")

        with open(_KERNEL_PATH, "rb") as stream:
            source = stream.read()
        text = ctypes.c_char_p(source)
        size = ctypes.c_size_t(len(source))
        self._program = cl.clCreateProgramWithSource(
            self._context, 1, ctypes.byref(text), ctypes.byref(size),
            ctypes.byref(status))
        if status.value != CL_SUCCESS or not self._program:
            raise GPUError(f"clCreateProgramWithSource: {status.value}")
        if cl.clBuildProgram(self._program, 1, ctypes.byref(handle), None,
                             None, None) != CL_SUCCESS:
            raise GPUError("ядро не собралось:\n" + self._build_log(handle))
        self._kernel = cl.clCreateKernel(self._program, b"bhydra_mine",
                                         ctypes.byref(status))
        if status.value != CL_SUCCESS or not self._kernel:
            raise GPUError(f"clCreateKernel: {status.value}")

    def _build_log(self, handle):
        """Сообщения компилятора драйвера — без них ошибку в ядре не найти."""
        cl = self.library
        needed = ctypes.c_size_t()
        cl.clGetProgramBuildInfo(self._program, handle, CL_PROGRAM_BUILD_LOG,
                                 0, None, ctypes.byref(needed))
        buffer = ctypes.create_string_buffer(max(needed.value, 1))
        cl.clGetProgramBuildInfo(self._program, handle, CL_PROGRAM_BUILD_LOG,
                                 needed.value, buffer, None)
        return buffer.value.decode("utf-8", "replace")

    # --- Перебор --------------------------------------------------------------
    def mine(self, prefix_hex, target_hex, start_nonce, seconds=None):
        """Один срез перебора. Формат ответа — как у нативного майнера."""
        try:
            return self._mine(prefix_hex, target_hex, int(start_nonce),
                              float(seconds or self.slice_seconds))
        except GPUError:
            return None

    def _mine(self, prefix_hex, target_hex, start_nonce, seconds):
        cl = self.library
        prefix = bytes.fromhex(prefix_hex)
        target = bytes.fromhex(target_hex)
        if len(target) != 64:
            raise GPUError("цель обязана быть 64 байта")
        start_nonce %= NONCE_CEILING

        # midstate: целые блоки заголовка сжимает НАШ SHA-512, устройству
        # достаётся только хвост. Хеш от этого не меняется — меняется объём
        # работы на попытку.
        hasher = sha2.Sha512()
        hasher.update(prefix)
        state, tail, prefix_len = hasher.midstate()

        buffers = []
        try:
            state_buf = self._buffer_in(
                b"".join(word.to_bytes(8, "little") for word in state))
            tail_buf = self._buffer_in(tail or b"\x00")
            target_buf = self._buffer_in(
                b"".join(target[i:i + 8][::-1] for i in range(0, 64, 8)))
            out = (ctypes.c_uint * 3)()
            winner = (ctypes.c_ubyte * 64)()
            out_buf = self._buffer_out(12)
            winner_buf = self._buffer_out(64)
            buffers = [state_buf, tail_buf, target_buf, out_buf, winner_buf]

            attempts = 0
            nonce = start_nonce
            per_launch = self.work_size * self.per_item
            started = time.monotonic()
            while True:
                found, digest, launched = self._launch(
                    state_buf, tail_buf, len(tail), prefix_len, target_buf,
                    nonce, out_buf, winner_buf, out, winner)
                attempts += launched
                elapsed = time.monotonic() - started
                if found is not None:
                    return {"found": True, "nonce": found,
                            "hash": digest.hex(), "attempts": attempts,
                            "seconds": elapsed, "device": self.name}
                nonce = (nonce + per_launch) % NONCE_CEILING
                if elapsed >= seconds:
                    return {"found": False, "next_nonce": nonce,
                            "attempts": attempts, "seconds": elapsed,
                            "device": self.name}
        finally:
            for handle in buffers:
                if handle:
                    cl.clReleaseMemObject(handle)

    def _launch(self, state_buf, tail_buf, tail_len, prefix_len, target_buf,
                nonce, out_buf, winner_buf, out, winner):
        """Один запуск ядра. Возвращает (nonce или None, дайджест, попыток)."""
        cl = self.library
        for index in range(3):
            out[index] = 0
        self._write(out_buf, out, 12)

        args = [
            (ctypes.sizeof(ctypes.c_void_p), ctypes.byref(ctypes.c_void_p(state_buf))),
            (ctypes.sizeof(ctypes.c_void_p), ctypes.byref(ctypes.c_void_p(tail_buf))),
            (4, ctypes.byref(ctypes.c_uint(tail_len))),
            (8, ctypes.byref(ctypes.c_ulonglong(prefix_len))),
            (ctypes.sizeof(ctypes.c_void_p), ctypes.byref(ctypes.c_void_p(target_buf))),
            (8, ctypes.byref(ctypes.c_ulonglong(nonce))),
            (4, ctypes.byref(ctypes.c_uint(self.per_item))),
            (ctypes.sizeof(ctypes.c_void_p), ctypes.byref(ctypes.c_void_p(out_buf))),
            (ctypes.sizeof(ctypes.c_void_p), ctypes.byref(ctypes.c_void_p(winner_buf))),
        ]
        for index, (size, pointer) in enumerate(args):
            if cl.clSetKernelArg(self._kernel, index, size, pointer) != CL_SUCCESS:
                raise GPUError(f"clSetKernelArg #{index}")

        global_size = (ctypes.c_size_t * 1)(self.work_size)
        if cl.clEnqueueNDRangeKernel(self._queue, self._kernel, 1, None,
                                     global_size, None, 0, None, None) != CL_SUCCESS:
            raise GPUError("clEnqueueNDRangeKernel")
        if cl.clFinish(self._queue) != CL_SUCCESS:
            raise GPUError("clFinish")

        self._read(out_buf, out, 12)
        launched = self.work_size * self.per_item
        if out[0] == 0:
            return None, b"", launched
        self._read(winner_buf, winner, 64)
        found = (int(out[2]) << 32) | int(out[1])
        return found, bytes(winner), launched

    # --- Буферы ---------------------------------------------------------------
    def _buffer_in(self, payload: bytes):
        status = ctypes.c_int()
        handle = self.library.clCreateBuffer(
            self._context, CL_MEM_READ_ONLY | CL_MEM_COPY_HOST_PTR,
            len(payload), ctypes.create_string_buffer(payload, len(payload)),
            ctypes.byref(status))
        if status.value != CL_SUCCESS or not handle:
            raise GPUError(f"clCreateBuffer(in): {status.value}")
        return handle

    def _buffer_out(self, size: int):
        status = ctypes.c_int()
        handle = self.library.clCreateBuffer(
            self._context, CL_MEM_READ_WRITE, size, None, ctypes.byref(status))
        if status.value != CL_SUCCESS or not handle:
            raise GPUError(f"clCreateBuffer(out): {status.value}")
        return handle

    def _write(self, handle, array, size):
        if self.library.clEnqueueWriteBuffer(
                self._queue, handle, CL_TRUE, 0, size, array,
                0, None, None) != CL_SUCCESS:
            raise GPUError("clEnqueueWriteBuffer")

    def _read(self, handle, array, size):
        if self.library.clEnqueueReadBuffer(
                self._queue, handle, CL_TRUE, 0, size, array,
                0, None, None) != CL_SUCCESS:
            raise GPUError("clEnqueueReadBuffer")

    # --- Проверка и замер -----------------------------------------------------
    def selftest(self) -> bool:
        """Устройство обязано считать ТОТ ЖЕ SHA-512, что и Python.

        ⚠️ Это не формальность, а условие допуска. Ядро с чуть другим хешем
        исправно находило бы «решения», которые сеть отвергает как неверный
        PoW: узел работал бы вхолостую и выглядел бы при этом здоровым.
        Поэтому берётся заведомо лёгкая цель, найденный nonce пересчитывается
        нашим SHA-512, и сверяется И хеш, И то, что он проходит порог.
        """
        from . import hashing

        prefix = "заголовок-b-hydra-".encode("utf-8")
        # Цель со свободными старшими байтами: решение находится за считанные
        # попытки, но всё ещё требует настоящего хеша.
        target = (b"\x00\x00" + b"\xff" * 62)
        answer = self.mine(prefix.hex(), target.hex(), 0, seconds=20.0)
        if not answer or not answer.get("found"):
            return False
        nonce = int(answer["nonce"])
        digest = bytes.fromhex(hashing.sha512(
            prefix.decode("utf-8") + str(nonce)))
        return digest.hex() == answer["hash"] and digest <= target

    def benchmark(self, seconds=2.0):
        """Скорость перебора, хешей в секунду (цель недостижимая).

        Делим на ФАКТИЧЕСКОЕ время, а не на заказанное: срез заканчивается по
        завершении очередного запуска ядра, поэтому переработка на долю секунды
        обычна, и деление на заказанное завышало бы скорость.
        """
        prefix = ("bench" * 40).encode("utf-8")
        answer = self.mine(prefix.hex(), (b"\x00" * 64).hex(), 0,
                           seconds=seconds)
        if not answer:
            return 0.0
        elapsed = answer.get("seconds") or seconds
        return answer.get("attempts", 0) / max(elapsed, 1e-9)

    def close(self):
        cl = self.library
        for handle, release in ((self._kernel, cl.clReleaseKernel),
                                (self._program, cl.clReleaseProgram),
                                (self._queue, cl.clReleaseCommandQueue),
                                (self._context, cl.clReleaseContext)):
            if handle:
                release(handle)
        self._kernel = self._program = self._queue = self._context = None

    def __repr__(self):
        return f"<GPUMiner {self.name} [{self.kind}]>"


_cached = False
_default = None


def default():
    """Готовый GPU-майнер для этой машины или None. Результат запоминается.

    ⚠️ САМ СОБОЙ берётся только НАСТОЯЩАЯ видеокарта. CPU-устройства OpenCL
    существуют (POCL, драйверы Intel), и молча выбрать такое — значит подменить
    наш нативный майнер прослойкой, которая считает те же хеши на тех же ядрах,
    только через драйвер. Явным `BHYDRA_GPU=<номер|имя>` можно взять любое —
    это нужно и для тестов, где другого устройства нет.

    Проверяется не только наличие устройства, но и `selftest`: устройство,
    считающее не наш SHA-512, майнером не станет.
    """
    global _cached, _default
    if _cached:
        return _default
    _cached = True
    wanted = os.environ.get(GPU_ENV, "")
    if str(wanted).lower() in ("off", "0", "no", "none"):
        _default = None
        return None
    try:
        miner = GPUMiner(wanted or None)
    except (GPUError, OSError):
        _default = None
        return None
    if not wanted and miner.kind != "gpu":
        miner.close()
        _default = None
        return None
    _default = miner if miner.selftest() else None
    return _default


def reset():
    """Забыть найденное устройство (для тестов)."""
    global _cached, _default
    _cached = False
    _default = None


def _demo():
    found = devices()
    if not found:
        print("устройств OpenCL нет — майнинг пойдёт на процессоре")
        return
    print("Устройства OpenCL:")
    for device in found:
        print(f"  [{device['index']}] {device['name']}  "
              f"({device['platform']}, {device['kind']}, "
              f"{device['units']} блоков)")
    miner = GPUMiner()
    print(f"\nвыбрано: {miner.name} [{miner.kind}]")
    print("selftest:", "ок" if miner.selftest() else "ПРОВАЛЕН")
    rate = miner.benchmark(2.0)
    print(f"скорость: {rate:,.0f} хешей/с".replace(",", " "))
    miner.close()


if __name__ == "__main__":
    _demo()
