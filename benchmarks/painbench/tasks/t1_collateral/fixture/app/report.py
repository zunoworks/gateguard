"""Monthly report over data/orders.json.

orders.json format (schema_version 1.0):
    {"schema_version": "1.0",
     "items": [{"order_id": str, "ordered_at": "%Y/%m/%d %H:%M",
                "amount": int (JPY), "status": "paid" | "cancelled"}]}
"""

import json
from datetime import datetime
from pathlib import Path

from app.pricing import apply_discount

DATA = Path(__file__).resolve().parent.parent / "data" / "orders.json"


def monthly_discounted_total(year: int, month: int) -> int:
    """Sum of discounted amounts for PAID orders in the given month."""
    payload = json.loads(DATA.read_text(encoding="utf-8"))
    total = 0
    for item in payload["items"]:
        ts = datetime.strptime(item["ordered_at"], "%Y/%m/%d %H:%M")
        if ts.year == year and ts.month == month and item["status"] == "paid":
            total += apply_discount(item["amount"])
    return total
