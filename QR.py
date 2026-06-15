"""
QR.py — генерация QR-кода для адреса/данных B-hydra.

Требует пакет `qrcode`. Если он не установлен — функция честно сообщает об этом,
а не падает на импорте.
"""

try:
    import qrcode
    _HAS_QRCODE = True
except ImportError:
    qrcode = None
    _HAS_QRCODE = False


def generate_qr(data: str):
    """Создаёт изображение QR-кода для строки данных."""
    if not _HAS_QRCODE:
        raise RuntimeError("Пакет 'qrcode' не установлен: pip install qrcode[pil]")
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(data)
    qr.make(fit=True)
    return qr.make_image(fill_color="black", back_color="white")


def save_qr(data: str, path: str = "qrcode.png") -> str:
    """Сохраняет QR-код в файл и возвращает путь."""
    image = generate_qr(data)
    image.save(path)
    return path


if __name__ == "__main__":
    if _HAS_QRCODE:
        path = save_qr("BHY-address-example", "qrcode.png")
        print(f"QR-код сохранён: {path}")
    else:
        print("Пакет 'qrcode' не установлен — пропускаю генерацию.")
