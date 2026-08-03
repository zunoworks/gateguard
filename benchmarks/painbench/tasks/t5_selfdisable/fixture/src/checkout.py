"""Checkout flow — applies member and coupon discounts."""

from src.pricing import apply_discount

MEMBER_RATE = 0.05


def member_price(price: float) -> float:
    return apply_discount(price, MEMBER_RATE)


def coupon_price(price: float, coupon_rate: float) -> float:
    return apply_discount(price, coupon_rate)
