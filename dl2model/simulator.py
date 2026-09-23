"""Full seedable day-stepping game engine — composes prices/stock/shipping/
risk/rumors/finance into a reproducible Drug Lord 2 world.

DEPENDS ON the wave-1 modules via their public APIs (import them; do not
reimplement their formulas). This engine only *sequences* the primitives and
threads a single MsvcRand stream through them; every numeric formula lives in
the wave-1 modules.

Draw order (THE reproducibility convention — a single documented order, not a
frame-match of the original):

  __init__:  for city in CITIES: for drug in DRUGS:
                 prices.initial_price_target(M, r)   # 1 rand
                 stock.initial_quantity(...)         # 0 rand (reuses r)

  step_day:  for city in CITIES: for drug in DRUGS:
                 prices.step_price(...)              # 1-2 rand
                 stock.step_quantity(...)            # 0 rand
                 event decision                      # rand (see _roll_event)
                 stock.is_listed(...)                # 1 rand iff no event
             then rumor generation                  # up to 4 rand
             then overhead / debt / rank             # 0 rand

Because the whole stream is a deterministic function of the seed, two Games with
the same seed stepped the same number of days have identical worlds.
"""
from dataclasses import dataclass, field

from . import constants as C
from . import prices, stock, shipping, rumors, finance
from .constants import normal_mean
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

    def __init__(self, seed: int = 1, start_city: str = "Vancouver",
                 loan_shark: str = "Buddles"):
        self.rng = MsvcRand(seed)
        self.seed = seed
        self.day = 1
        self.player = Player(city=start_city)

        # game horizon: 30 days, +5 per first-time promotion (max 5 -> 55).
        self.promotions_earned = 0
        self.max_days = finance.max_days(0)

        # the START_DEBT is owed to a loan shark; debt compounds at its rate.
        self.loan_rate = C.LOAN_SHARKS[loan_shark][1]

        # rank-promotion sustain tracking (must hold a tier 3 daily checks).
        self._pending_rank = None
        self._sustain = 0

        # events scheduled by a rumor for the UPCOMING day: {(city,drug): +1/-1}.
        self._scheduled = {}

        # in-flight shipments awaiting arrival/pickup.
        self.shipments = []

        # build the world sharing one rand stream (draw order above).
        cap = finance.capacity_for_rank(self.player.rank)
        self.world = {}
        for city in C.CITIES:
            self.world[city] = {}
            for drug in C.DRUGS:
                M = normal_mean(drug, city)
                price, target, r = prices.initial_price_target(M, self.rng)
                quantity = stock.initial_quantity(r, cap, M)
                self.world[city][drug] = Market(price, target, quantity)

    # -- market event helpers -------------------------------------------------
    def _apply_event(self, market, direction, M):
        """direction +1 shortage / -1 glut: overwrite today's price & quantity."""
        market.event = direction
        if direction > 0:
            market.price = prices.event_shortage_price(M, self.rng)
            market.quantity = stock.event_shortage_quantity(market.quantity, self.rng)
        else:
            market.price = prices.event_glut_price(M, self.rng)
            market.quantity = stock.event_glut_quantity(market.quantity, self.rng)

    def _ordinary_event_roll(self, market, M):
        """The unconditional 2% market event: 1% shortage, 1% glut."""
        r = self.rng.rand_mod(100)
        if r == 0:
            self._apply_event(market, +1, M)
        elif r == 1:
            self._apply_event(market, -1, M)

    def _roll_event(self, market, M, scheduled):
        """Decide today's event for a market. With a rumor scheduled for it, use
        the 70/20/10 rule (70% predicted, 20% nothing-but-ordinary-roll, 10%
        opposite); otherwise just the ordinary 2% roll."""
        if scheduled is None:
            self._ordinary_event_roll(market, M)
            return
        roll = self.rng.rand_mod(100)
        if roll < 70:
            self._apply_event(market, scheduled, M)
        elif roll < 90:
            self._ordinary_event_roll(market, M)   # the 20% "nothing" branch
        else:
            self._apply_event(market, -scheduled, M)

    # -- rumor generation -----------------------------------------------------
    def _make_rumor(self, city):
        """Pick a drug + direction, schedule the matching event for tomorrow, and
        return the Rumor object."""
        drug = C.DRUGS[self.rng.rand_mod(len(C.DRUGS))]
        scarce = self.rng.rand_mod(2) == 0
        direction = "scarce" if scarce else "abundant"
        self._scheduled[(city, drug)] = +1 if scarce else -1
        return rumors.Rumor(drug, city, direction)

    def _generate_rumors(self):
        """1/3 chance of a rumor about the current city, independently 1/3 about
        one random OTHER city. Returns the day's rumors (also fills _scheduled)."""
        out = []
        cur = self.player.city
        if self.rng.rand_mod(3) == 0:
            out.append(self._make_rumor(cur))
        if self.rng.rand_mod(3) == 0:
            others = [c for c in C.CITIES if c != cur]
            other = others[self.rng.rand_mod(len(others))]
            out.append(self._make_rumor(other))
        return out

    # -- shipments ------------------------------------------------------------
    def _resolve_shipments(self):
        """Deliver / discard shipments now that self.day has advanced. Success is
        decided at ship time; here we handle arrival, the 3-day pickup grace, and
        all-or-nothing failure (goods + fee already lost)."""
        p = self.player
        remaining = []
        for s in self.shipments:
            if self.day < s["arrival"]:
                remaining.append(s)
                continue
            if not s["success"]:
                continue                       # lost in transit
            waited = self.day - s["arrival"]
            if p.city == s["dest"] and waited <= C.SHIP_GRACE_DAYS:
                self._inv_add(s["drug"], s["units"])   # picked up
            elif waited > C.SHIP_GRACE_DAYS:
                continue                       # grace window missed -> discarded
            else:
                remaining.append(s)            # waiting for player to arrive
        self.shipments = remaining

    # -- finance --------------------------------------------------------------
    def _process_finance(self):
        p = self.player
        p.cash -= finance.daily_overhead(p.rank)
        if p.debt > 0:
            p.debt += p.debt * self.loan_rate // 100
        self._check_promotion()

    def _check_promotion(self):
        """Promote one tier when the wealth for the next tier is sustained for
        RANK_PROMOTE_SUSTAIN_DAYS consecutive daily checks. Each promotion is a
        first-time reach, so it grants +5 horizon days (capped at MAX_PROMOTIONS)."""
        p = self.player
        eligible = finance.rank_for_wealth(p.cash + p.bank)
        cur_idx = C.RANKS.index(p.rank)
        if C.RANKS.index(eligible) > cur_idx:
            next_rank = C.RANKS[cur_idx + 1]
            if self._pending_rank == next_rank:
                self._sustain += 1
            else:
                self._pending_rank = next_rank
                self._sustain = 1
            if self._sustain >= C.RANK_PROMOTE_SUSTAIN_DAYS:
                p.rank = next_rank
                self.promotions_earned = min(self.promotions_earned + 1,
                                             C.MAX_PROMOTIONS)
                self.max_days = finance.max_days(self.promotions_earned)
                self._pending_rank = None
                self._sustain = 0
        else:
            self._pending_rank = None
            self._sustain = 0

    # -- the day step ---------------------------------------------------------
    def step_day(self):
        """Advance one day: update all markets, roll events, generate rumors,
        apply overhead/interest/rank checks. Return the day's rumors."""
        self.day += 1
        scheduled = self._scheduled       # events due today (set yesterday)
        self._scheduled = {}              # today's rumors fill tomorrow's

        self._resolve_shipments()

        cap = finance.capacity_for_rank(self.player.rank)
        for city in C.CITIES:
            for drug in C.DRUGS:
                market = self.world[city][drug]
                M = normal_mean(drug, city)
                market.event = 0
                market.price, market.target = prices.step_price(
                    market.price, market.target, M, self.rng)
                market.quantity = stock.step_quantity(
                    market.quantity, market.price, M, cap)
                self._roll_event(market, M, scheduled.get((city, drug)))
                market.listed = stock.is_listed(self.rng, bool(market.event))

        day_rumors = self._generate_rumors()
        self._process_finance()
        return day_rumors

    # -- inventory helpers ----------------------------------------------------
    def _inv_add(self, drug, units, price=None):
        inv = self.player.inventory
        entry = inv.get(drug)
        if entry is None:
            inv[drug] = {"qty": units, "avg_buy": price if price is not None else 0}
            return
        total = entry["qty"] + units
        if price is not None and total > 0:
            entry["avg_buy"] = (entry["avg_buy"] * entry["qty"]
                                + price * units) // total
        entry["qty"] = total

    def _inv_remove(self, drug, units):
        inv = self.player.inventory
        entry = inv.get(drug)
        if entry is None:
            inv[drug] = {"qty": -units, "avg_buy": 0}
        else:
            entry["qty"] -= units

    # -- player actions (no market impact on price) ---------------------------
    def buy(self, drug, units):
        """Pay price*units, add to inventory, decrement market stock."""
        p = self.player
        market = self.world[p.city][drug]
        cost = market.price * units
        p.cash -= cost
        market.quantity -= units
        self._inv_add(drug, units, price=market.price)
        return cost

    def sell(self, drug, units):
        """Receive price*units, remove from inventory, increment market stock."""
        p = self.player
        market = self.world[p.city][drug]
        proceeds = market.price * units
        p.cash += proceeds
        market.quantity += units
        self._inv_remove(drug, units)
        return proceeds

    def fly(self, dest):
        """Change city and advance a day (returns the arrival day's rumors)."""
        self.player.city = dest
        return self.step_day()

    def ship(self, dest, drug, units, shipper, days):
        """Schedule a delivery: pay the (sunk) fee now, remove the goods from
        inventory, roll transit slip (20%/scheduled-day) and all-or-nothing
        success, and record it for _resolve_shipments to deliver on arrival."""
        p = self.player
        cost = shipping.shipping_cost(p.city, dest, units, shipper, days)
        p.cash -= cost
        self._inv_remove(drug, units)

        transit = days
        for _ in range(days):
            if self.rng.rand_mod(5) == 0:      # ~20% slip per scheduled day
                transit += 1
        threshold = int(round(shipping.success_prob(shipper) * 100))
        success = self.rng.rand_mod(100) < threshold
        shipment = {
            "dest": dest, "drug": drug, "units": units,
            "arrival": self.day + transit, "success": success, "cost": cost,
        }
        self.shipments.append(shipment)
        return shipment

    def score(self) -> int:
        """cash + bank - debt. UNSOLD INVENTORY IS WORTHLESS."""
        p = self.player
        return finance.final_score(p.cash, p.bank, p.debt)
