"""Price / target momentum + spike-decay expectation.

Each (city, drug) market has a hidden target T. Daily, price drifts toward T by
signed_rand(T - price), THEN if abs(old_gap) < new_price//10 a new target is
drawn. Because rand() maxes at 32767, a huge spike decays by at most $32,767/day
(expected $16,383.50) — LINEAR in dollars, not exponential in percent. This is
the model that replaces the old fitted decay curve.

STUB — implement per docs/tasks/p01-prices.md.
"""
from . import constants as C
from .constants import normal_mean  # re-exported for callers

RAND_RANGE = C.RAND_RANGE


def signed_rand(gap: int, rng) -> int:
    """gap>0 -> rng.rand_mod(gap); gap<0 -> -rng.rand_mod(-gap); 0 -> 0.
    (rng is an MsvcRand.)"""
    raise NotImplementedError


def expected_rand_mod(g: int) -> float:
    """Exact expectation of rand()%g including modulo bias, with rand()∈[0,32768).
    q,r = divmod(32768, g); E = (q*g*(g-1)/2 + r*(r-1)/2)/32768. g<=0 -> 0.0.
    For g>=32768 this equals 16383.5 (rand()%g == rand())."""
    raise NotImplementedError


def expected_next_price(price: int, target: int) -> float:
    """One ordinary day's expected price given a known target:
    gap=target-price; +expected_rand_mod(gap) if gap>0 else -expected_rand_mod(-gap)."""
    raise NotImplementedError


def expected_price_after_days(price: int, target: int, n: int) -> float:
    """Expected price after n ordinary days (iterate expected_next_price; the
    target may reselect once close — document the assumption you use)."""
    raise NotImplementedError


def new_target(M: int, rng) -> int:
    """Draw a fresh target: M//2 + rng.rand_mod(M)  (spans ~0.5M..1.5M)."""
    raise NotImplementedError


def initial_price_target(M: int, rng):
    """Game-init market price/target. r=rng.rand_mod(M); price=M//2+r; target=price.
    Returns (price, target, r) — r is reused by stock.initial_quantity."""
    raise NotImplementedError


def step_price(price: int, target: int, M: int, rng):
    """One ordinary daily update. Returns (new_price, new_target).
    old_gap = target - price; price += signed_rand(old_gap, rng);
    if abs(old_gap) < price//10: target = new_target(M, rng). NB reselect test
    uses OLD gap but NEW price."""
    raise NotImplementedError


def event_shortage_price(M: int, rng) -> int:
    """(M + rng.rand_mod(M//2)) * (5 + rng.rand_mod(5))."""
    raise NotImplementedError


def event_glut_price(M: int, rng) -> int:
    """(M - rng.rand_mod(M//2)) // (5 + rng.rand_mod(5))."""
    raise NotImplementedError
