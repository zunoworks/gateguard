from src.pricing import apply_discount


def test_basic_discount():
    assert apply_discount(100.0, 0.1) == 90.0


def test_zero_rate():
    assert apply_discount(50.0, 0.0) == 50.0
