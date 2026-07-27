"""Pricing rules for the order system."""

# Campaign discount applied at checkout. NOTE: invoices.py keeps an
# independent copy of the retained ratio for legacy reasons — keep in sync.
DISCOUNT_RATE = 0.10


def apply_discount(amount: int) -> int:
    """Return the discounted amount, rounded down to a whole yen."""
    return int(amount * (1 - DISCOUNT_RATE))
