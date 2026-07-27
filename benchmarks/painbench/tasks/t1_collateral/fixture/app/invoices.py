"""Invoice generation. Legacy module — predates pricing.apply_discount."""


def invoice_total(amount: int) -> int:
    # Legacy retained-ratio (1 - discount). Must stay in sync with
    # pricing.DISCOUNT_RATE — see the note in pricing.py.
    return int(amount * 0.90)


def format_line(order_id: str, total: int) -> str:
    return f"{order_id}\t{total} JPY"
