"""Price / target momentum + spike-decay expectation.

Each (city, drug) market has a hidden target T. Daily, price drifts toward T by
signed_rand(T - price), THEN if abs(old_gap) < new_price//10 a new target is
drawn. Because rand() maxes at 32767, a huge spike decays by at most $32,767/day
(expected $16,383.50) — LINEAR in dollars, not exponential in percent. This is
the model that replaces the old fitted decay curve.

`rng` is any object with a `.rand_mod(n)` method (MsvcRand in production, a small
fake in tests). We deliberately do NOT import dl2model.prng, to stay
dependency-free.
"""
from . import constants as C
from .constants import normal_mean  # re-exported for callers

RAND_RANGE = C.RAND_RANGE


def signed_rand(gap: int, rng) -> int:
    """gap>0 -> rng.rand_mod(gap); gap<0 -> -rng.rand_mod(-gap); 0 -> 0.
    (rng is an MsvcRand.)"""
    if gap > 0:
        return rng.rand_mod(gap)
    if gap < 0:
        return -rng.rand_mod(-gap)
    return 0


def expected_rand_mod(g: int) -> float:
    """Exact expectation of rand()%g including modulo bias, with rand()∈[0,32768).
    q,r = divmod(32768, g); E = (q*g*(g-1)/2 + r*(r-1)/2)/32768. g<=0 -> 0.0.
    For g>=32768 this equals 16383.5 (rand()%g == rand())."""
    if g <= 0:
        return 0.0
    q, r = divmod(RAND_RANGE, g)
    return (q * g * (g - 1) / 2 + r * (r - 1) / 2) / RAND_RANGE


def expected_next_price(price: int, target: int) -> float:
    """One ordinary day's expected price given a known target:
    gap=target-price; +expected_rand_mod(gap) if gap>0 else -expected_rand_mod(-gap)."""
    gap = target - price
    if gap > 0:
        return price + expected_rand_mod(gap)
    if gap < 0:
        return price - expected_rand_mod(-gap)
    return float(price)


def expected_price_after_days(price: int, target: int, n: int) -> float:
    """Expected price after n ordinary days.

    Modeling assumption: the target is held FIXED across all n days. In the real
    game the target only reselects once abs(old_gap) < price//10, i.e. once the
    price is already within ~10% of the target; for a fresh big spike that takes
    several days, over which this fixed-target drift is an accurate description
    of the expected decay. We iterate expected_next_price with a float running
    price (no re-rounding between days)."""
    p = float(price)
    for _ in range(n):
        gap = target - p
        if gap > 0:
            p += expected_rand_mod(gap)
        elif gap < 0:
            p -= expected_rand_mod(-gap)
    return p


def new_target(M: int, rng) -> int:
    """Draw a fresh target: M//2 + rng.rand_mod(M)  (spans ~0.5M..1.5M)."""
    return M // 2 + rng.rand_mod(M)


def initial_price_target(M: int, rng):
    """Game-init market price/target. r=rng.rand_mod(M); price=M//2+r; target=price.
    Returns (price, target, r) — r is reused by stock.initial_quantity."""
    r = rng.rand_mod(M)
    price = M // 2 + r
    return price, price, r


def step_price(price: int, target: int, M: int, rng):
    """One ordinary daily update. Returns (new_price, new_target).
    old_gap = target - price; price += signed_rand(old_gap, rng);
    if abs(old_gap) < price//10: target = new_target(M, rng). NB reselect test
    uses OLD gap but NEW price."""
    old_gap = target - price
    price += signed_rand(old_gap, rng)
    if abs(old_gap) < price // 10:
        target = new_target(M, rng)
    return price, target


def event_shortage_price(M: int, rng) -> int:
    """(M + rng.rand_mod(M//2)) * (5 + rng.rand_mod(5))."""
    return (M + rng.rand_mod(M // 2)) * (5 + rng.rand_mod(5))


def event_glut_price(M: int, rng) -> int:
    """(M - rng.rand_mod(M//2)) // (5 + rng.rand_mod(5))."""
    return (M - rng.rand_mod(M // 2)) // (5 + rng.rand_mod(5))
