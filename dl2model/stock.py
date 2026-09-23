"""Availability / stock model + market-listing roll.

Stock is anti-correlated with price and scaled by the player's capacity tier C:
target ~ C*(1.5 - price/M). Actual stock is pulled halfway to target each day,
then an event may crush/inflate it. A drug may also simply not be LISTED (2/3
chance absent an event) — a separate concept from having zero stock.

STUB — implement per docs/tasks/p02-stock.md.
"""
from . import constants as C
from .constants import trunc_div


def stock_target(price: int, M: int, cap: int) -> int:
    """desired = cap - trunc_div((price - M//2) * cap, M).
    price=0.5M->cap, 1.0M->0.5cap, 1.5M->0, >1.5M-> clamps to >=0 later."""
    return cap - trunc_div((price - M // 2) * cap, M)


def step_quantity(quantity: int, price: int, M: int, cap: int) -> int:
    """quantity = max(trunc_div(quantity + stock_target(price,M,cap), 2), 0)."""
    target = stock_target(price, M, cap)
    return max(trunc_div(quantity + target, 2), 0)


def initial_quantity(r: int, cap: int, M: int) -> int:
    """Game-init stock: cap - trunc_div(r * cap, M), where r is the same
    rng.rand_mod(M) used by prices.initial_price_target. Clamp >=0."""
    return max(cap - trunc_div(r * cap, M), 0)


def event_shortage_quantity(quantity: int, rng) -> int:
    """quantity // (2 + rng.rand_mod(5))  (divide by 2..6)."""
    return quantity // (2 + rng.rand_mod(5))


def event_glut_quantity(quantity: int, rng) -> int:
    """quantity * (2 + rng.rand_mod(5))   (multiply by 2..6)."""
    return quantity * (2 + rng.rand_mod(5))


def is_listed(rng, event: bool) -> bool:
    """event -> always True; else rng.rand_mod(3) != 0  (2/3 listed)."""
    if event:
        return True
    return rng.rand_mod(3) != 0
