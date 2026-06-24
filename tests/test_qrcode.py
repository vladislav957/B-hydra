"""Тесты QR-генератора (структурные, без сторонних декодеров)."""

from b_hydra.qrcode_gen import qr_matrix, qr_text


def _is_finder(rows, r0, c0):
    """Проверка поискового узора 7x7 в углу (r0, c0)."""
    pat = [
        "1111111", "1000001", "1011101", "1011101",
        "1011101", "1000001", "1111111",
    ]
    for dr in range(7):
        if "".join(rows[r0 + dr][c0:c0 + 7]) != pat[dr]:
            return False
    return True


def test_matrix_is_square_and_sized():
    rows = qr_matrix("BHYDhAjTov9QXWR3nKovis28mX8cARWVUqtCn")
    assert len(rows) == len(rows[0])
    # Размер = 17 + 4*версия; версия 1..10 → 21..57.
    assert (len(rows) - 17) % 4 == 0
    assert all(set(row) <= {"0", "1"} for row in rows)


def test_finder_patterns_present():
    rows = qr_matrix("BHY")
    n = len(rows)
    assert _is_finder(rows, 0, 0)
    assert _is_finder(rows, 0, n - 7)
    assert _is_finder(rows, n - 7, 0)


def test_dark_module_present():
    rows = qr_matrix("BHY")
    n = len(rows)
    assert rows[n - 8][8] == "1"   # обязательный тёмный модуль


def test_deterministic():
    addr = "BHYDfb5fjKb6Q8mZdmLocDywyJMQE8NZgcetn"
    assert qr_matrix(addr) == qr_matrix(addr)


def test_longer_data_picks_larger_version():
    small = len(qr_matrix("BHY"))
    big = len(qr_matrix("B" * 60))
    assert big > small


def test_ascii_render_runs():
    out = qr_text("BHY")
    assert "█" in out and out.count("\n") > 5
