"""Core pricing rules."""


def apply_discount(price: float, rate: float) -> float:
    """Apply a fractional discount to a price."""
    if not 0 <= rate < 1:
        raise ValueError("rate must be in [0, 1)")
    return round(price * (1 - rate), 2)
