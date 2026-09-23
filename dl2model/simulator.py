"""Full seedable day-stepping game engine — composes prices/stock/shipping/
risk/rumors/finance into a reproducible Drug Lord 2 world.

DEPENDS ON the wave-1 modules via their public APIs (import them; do not
reimplement their formulas). Until those land you'll be calling stubs — write to
the documented contracts and keep engine-level unit tests that monkeypatch or
fake the primitives; the full integration run goes green once wave-1 merges.

STUB — implement per docs/tasks/p08-simulator.md.
"""
from dataclasses import dataclass, field
from . import constants as C
from . import prices, stock, shipping, risk, rumors, finance
from .prng import MsvcRand


@dataclass
class Market:
    price: int
    target: int
    quantity: int
    listed: bool = True
    event: int = 0            # 0 none, +1 shortage, -1 glut (this day)


@dataclass
class Player:
    city: str
    cash: int = C.START_CASH
    bank: int = 0
    debt: int = C.START_DEBT
    rank: str = "Wannabe"
    no_scent: int = 0
    inventory: dict = field(default_factory=dict)   # drug -> {qty, avg_buy}


class Game:
    """A whole game. world[city][drug] -> Market. Advancing a day updates every
    city×drug market (price, target, quantity, events), generates rumors, then
    processes loans/overhead/rank."""

    def __init__(self, seed: int = 1):
        raise NotImplementedError

    def step_day(self):
        """Advance one day: update all markets, roll events, generate rumors,
        apply overhead/interest/rank checks. Return the day's rumors."""
        raise NotImplementedError

    # player actions (no market impact on price):
    def buy(self, drug, units): raise NotImplementedError
    def sell(self, drug, units): raise NotImplementedError
    def fly(self, dest): raise NotImplementedError
    def ship(self, dest, drug, units, shipper, days): raise NotImplementedError
    def score(self) -> int: raise NotImplementedError
