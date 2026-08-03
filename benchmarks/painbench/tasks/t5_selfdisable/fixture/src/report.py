"""Discount summary report."""

from src.pricing import apply_discount


def discounted_total(prices, rate):
    return sum(apply_discount(p, rate) for p in prices)
