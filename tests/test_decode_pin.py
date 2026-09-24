"""Pin every decoded formula to its executable address (docs/decode-reference.md,
2026-09-24 decompile pass) against a deterministic fake RNG, so the model can
never silently drift from the binary again.

Each test cites the decode-reference section + address it pins. The fake RNG
feeds a scripted sequence of raw MSVC ``rand()`` values (0..32767) and applies
``% n`` on each ``rand_mod`` call, mirroring the game's ``rand()%n`` idiom; it
also counts draws so we can pin RNG *consumption* (draw order/count), not just
the arithmetic.
"""
import inspect

import pytest

from dl2model import constants as C
from dl2model import prices, stock
from dl2model.constants import normal_mean, trunc_div
from dl2model.simulator import Game, Market


class FakeRng:
    """Deterministic rand_mod: yields raw rand() values (0..32767), returns
    ``seq[i] % n`` (the MSVC ``rand()%n`` idiom), and counts consumption.

    Over-consumption raises IndexError, so a wrong draw count fails loudly."""

    def __init__(self, seq):
        self.seq = list(seq)
        self.i = 0

    @property
    def count(self):
        return self.i

    def rand_mod(self, n):
        v = self.seq[self.i]
        self.i += 1
        return v % n if n > 0 else 0


def _game_with_rng(seq):
    """A Game whose world is already built (real init RNG), then its stream
    swapped for a scripted FakeRng for the method under test."""
    g = Game(1)
    g.rng = FakeRng(seq)
    return g


# --- §2  M = base_price * city_mult // 100  (0x40534F-0x405368) -------------
def test_normal_mean_matches_decode_section_2():
    """decode-reference §2 (0x40534F-0x405368): M = base * mult // 100, trunc."""
    assert normal_mean("Cocaine", "Austin") == 5100 * 100 // 100 == 5100
    assert normal_mean("Cocaine", "Beijing") == 5100 * 190 // 100 == 9690
    assert normal_mean("Kat", "San Francisco") == 800 * 80 // 100 == 640
    assert normal_mean("Mushrooms", "Miami") == 400 * 90 // 100 == 360
    # exhaustive: every cell equals the decoded formula.
    for drug in C.DRUGS:
        for city in C.CITIES:
            assert normal_mean(drug, city) == (
                C.BASE_PRICE[drug] * C.CITY_MULT[city] // 100)


# --- §3  price drift + hidden-target reselection  (0x405343-0x4053B4) -------
def test_step_price_drift_positive_gap():
    """decode-reference §3 (0x405370): price += signed_rand(old_gap); gap>0 ->
    +rand%old_gap."""
    # old_gap=5000, drift draw 100 -> new_price=1100; 1100//10=110; 5000<110
    # is False -> no reselect (target unchanged). One draw only.
    rng = FakeRng([100])
    new_price, new_target = prices.step_price(1000, 6000, 10000, rng)
    assert (new_price, new_target) == (1100, 6000)
    assert rng.count == 1


def test_step_price_drift_negative_gap():
    """decode-reference §3 (0x401280 signed_randmod, 0x405370): gap<0 ->
    price -= rand%|old_gap|."""
    # old_gap=-1000, draw 300 -> new_price=1700; 1700//10=170; 1000<170 False.
    rng = FakeRng([300])
    new_price, new_target = prices.step_price(2000, 1000, 10000, rng)
    assert (new_price, new_target) == (1700, 1000)
    assert rng.count == 1


def test_step_price_reselect_fires_strict_less_than():
    """decode-reference §3 (0x405399): reselect iff abs(old_gap) < new_price//10
    (strict <, uses OLD gap but NEW price), target = M//2 + rand%M (0x4053B1)."""
    # old_gap=100, drift 5 -> new_price=1015; 1015//10=101; 100 < 101 -> reselect.
    # target draw 200 -> M//2 + 200 = 5200. Two draws.
    rng = FakeRng([5, 200])
    new_price, new_target = prices.step_price(1010, 1110, 10000, rng)
    assert new_price == 1015
    assert new_target == 10000 // 2 + 200 == 5200
    assert rng.count == 2


def test_step_price_reselect_fires_negative_gap():
    """decode-reference §3 (0x405399): the strict-< reselect test uses abs(old_gap)
    and applies for negative gaps too."""
    # old_gap=-50, draw 10 -> new_price=1040; 1040//10=104; abs(-50)=50 < 104 ->
    # reselect. target draw 100 -> 5100. Two draws.
    rng = FakeRng([10, 100])
    new_price, new_target = prices.step_price(1050, 1000, 10000, rng)
    assert new_price == 1040
    assert new_target == 5100
    assert rng.count == 2


def test_step_price_no_reselect_on_equality():
    """decode-reference §3 (0x405399): equality does NOT reselect (strict <)."""
    # old_gap=100, drift 0 -> new_price=1000; 1000//10=100; 100 < 100 is False ->
    # target unchanged, no reselect draw. One draw only.
    rng = FakeRng([0])
    new_price, new_target = prices.step_price(1000, 1100, 10000, rng)
    assert new_price == 1000
    assert new_target == 1100                    # unchanged: no reselect
    assert rng.count == 1                        # drift draw only, no target draw


def test_new_target_span():
    """decode-reference §3 (0x4053B1): new_target = M//2 + rand%M, spanning
    floor(M/2)..floor(3M/2)-1."""
    M = 10000
    assert prices.new_target(M, FakeRng([0])) == M // 2                # low bound
    assert prices.new_target(M, FakeRng([M - 1])) == M // 2 + (M - 1)  # high bound
    assert prices.new_target(M, FakeRng([12345])) == M // 2 + 12345 % M


# --- §4b  shock magnitude formulas + no-target-write ------------------------
def test_event_shortage_price_formula():
    """decode-reference §4b (0x405421-0x405455): (M + rand%(M//2)) * (5 + rand%5)."""
    # M=10000: (10000 + 200) * (5 + 3) = 10200 * 8 = 81600.
    rng = FakeRng([200, 3])
    assert prices.event_shortage_price(10000, rng) == 81600
    assert rng.count == 2


def test_event_glut_price_formula():
    """decode-reference §4b (0x40545C-0x40548E): (M - rand%(M//2)) // (5 + rand%5)."""
    # M=10000: (10000 - 200) // (5 + 3) = 9800 // 8 = 1225.
    rng = FakeRng([200, 3])
    assert prices.event_glut_price(10000, rng) == 1225
    assert rng.count == 2


def test_event_shortage_quantity_formula():
    """decode-reference §4b (0x405421-0x405455): quantity //= (2 + rand%5)."""
    assert stock.event_shortage_quantity(100, FakeRng([3])) == 100 // (2 + 3)  # 20


def test_event_glut_quantity_formula():
    """decode-reference §4b (0x40545C-0x40548E): quantity *= (2 + rand%5)."""
    assert stock.event_glut_quantity(100, FakeRng([3])) == 100 * (2 + 3)       # 500


def test_apply_event_does_not_write_target():
    """decode-reference §4b: neither shock branch writes market.target — the
    shock overwrites visible price/quantity only; post-shock decay drifts back
    toward the pre-existing normal target."""
    M = 10000
    # shortage: price 2 draws + quantity 1 draw = 3 draws.
    g = _game_with_rng([200, 3, 3])
    m = Market(price=5000, target=7777, quantity=100)
    g._apply_event(m, +1, M)
    assert m.target == 7777                       # UNCHANGED
    assert m.event == +1
    assert m.price == 81600                       # (10000+200)*(5+3)
    assert m.quantity == 20                        # 100 // (2+3)
    assert g.rng.count == 3

    # glut: same no-target-write invariant.
    g = _game_with_rng([200, 3, 3])
    m = Market(price=5000, target=7777, quantity=100)
    g._apply_event(m, -1, M)
    assert m.target == 7777                       # UNCHANGED
    assert m.event == -1
    assert m.price == 1225                         # (10000-200)//(5+3)
    assert m.quantity == 500                        # 100 * (2+3)
    assert g.rng.count == 3


# --- §4a  Fix 1: rumor scheduler uses rand%10 (<=6 / 7-8 / 9) ---------------
def test_roll_event_scheduler_predicted_branch():
    """decode-reference §4a (rumor_step 0x4040B6-0x4040E6) FIX 1: rand%10 in
    0..6 -> predicted event. Draws: 1 (roll) + 3 (shock)."""
    M = 10000
    for roll in (0, 6):                            # both ends of the 0..6 band
        g = _game_with_rng([roll, 200, 3, 3])
        m = Market(price=5000, target=7777, quantity=100)
        g._roll_event(m, M, scheduled=+1)
        assert m.event == +1                       # predicted
        assert g.rng.count == 4


def test_roll_event_scheduler_nothing_branch_runs_ordinary_roll():
    """decode-reference §4a FIX 1: rand%10 in 7..8 -> the 'nothing' branch, which
    still runs the ordinary roll (§4c) same-day. Here the ordinary %50 misses, so
    no event; draws: 1 (roll) + 1 (ordinary %50, no fire)."""
    M = 10000
    for roll in (7, 8):
        g = _game_with_rng([roll, 1])              # %50 -> 1, no fire
        m = Market(price=5000, target=7777, quantity=100)
        g._roll_event(m, M, scheduled=+1)
        assert m.event == 0                        # nothing scheduled fired
        assert g.rng.count == 2                    # roll + the ordinary %50 draw


def test_roll_event_scheduler_opposite_branch():
    """decode-reference §4a FIX 1: rand%10 == 9 -> the opposite event. Draws:
    1 (roll) + 3 (shock)."""
    M = 10000
    g = _game_with_rng([9, 200, 3, 3])
    m = Market(price=5000, target=7777, quantity=100)
    g._roll_event(m, M, scheduled=+1)
    assert m.event == -1                            # opposite of +1
    assert g.rng.count == 4


def test_roll_event_no_schedule_uses_ordinary_only():
    """decode-reference §4a/§4c: with no rumor scheduled, only the ordinary roll
    runs — a single %50 draw when it misses."""
    g = _game_with_rng([1])                         # %50 -> 1, no fire
    m = Market(price=5000, target=7777, quantity=100)
    g._roll_event(m, 10000, scheduled=None)
    assert m.event == 0
    assert g.rng.count == 1


# --- §4c  Fix 2: ordinary event = rand%50==0 then rand%2 --------------------
def test_ordinary_event_roll_no_fire_consumes_one_draw():
    """decode-reference §4c (0x4053F0-0x405416) FIX 2: rand%50 != 0 -> no event,
    and the direction draw (rand%2) is NOT taken. Exactly one draw."""
    g = _game_with_rng([1])                         # %50 -> 1
    m = Market(price=5000, target=7777, quantity=100)
    g._ordinary_event_roll(m, 10000)
    assert m.event == 0
    assert g.rng.count == 1                         # %50 only; no direction draw


def test_ordinary_event_roll_fires_shortage_two_gate_draws():
    """decode-reference §4c FIX 2: rand%50 == 0 fires; direction is a SECOND draw
    (rand%2 == 0 -> shortage). Draws: 1 (%50) + 1 (%2) + 3 (shock) = 5."""
    g = _game_with_rng([0, 0, 200, 3, 3])           # %50->0 fire, %2->0 shortage
    m = Market(price=5000, target=7777, quantity=100)
    g._ordinary_event_roll(m, 10000)
    assert m.event == +1
    assert g.rng.count == 5


def test_ordinary_event_roll_fires_glut_direction_second_draw():
    """decode-reference §4c FIX 2: rand%2 == 1 -> glut. The direction is only ever
    a second draw taken when the event fires."""
    g = _game_with_rng([0, 1, 200, 3, 3])           # %50->0 fire, %2->1 glut
    m = Market(price=5000, target=7777, quantity=100)
    g._ordinary_event_roll(m, 10000)
    assert m.event == -1
    assert g.rng.count == 5


# --- §5  stock daily step (trunc_div, no RNG)  (0x4053B4-0x4053EF) ----------
def test_step_quantity_positive_case():
    """decode-reference §5 (0x4053B4-0x4053EF): quantity =
    max(trunc_div(quantity + desired, 2), 0); desired = C - trunc_div((P-H)*C, M),
    H = M//2."""
    # P=H=5000 -> desired = cap - 0 = 100; (50 + 100)//2 = 75.
    assert stock.step_quantity(50, 5000, 10000, 100) == 75


def test_step_quantity_negative_intermediate_trunc_div():
    """decode-reference §5: the divide is C truncate-toward-zero (constants.trunc_div),
    NOT Python floor //. With a negative numerator in (P-H)*C, trunc_div and //
    diverge, and this pins the truncating behaviour."""
    # P=4000 < H=5000, cap=3, M=10000: (4000-5000)*3 = -3000.
    # trunc_div(-3000, 10000) = 0  (Python -3000//10000 == -1).
    assert trunc_div(-3000, 10000) == 0
    assert (-3000) // 10000 == -1                  # would-be floor result
    assert stock.stock_target(4000, 10000, 3) == 3   # cap - 0  (floor would give 4)
    # step: max(trunc_div(0 + 3, 2), 0) = 1  (floor path would give (0+4)//2 = 2).
    assert stock.step_quantity(0, 4000, 10000, 3) == 1


def test_step_quantity_consumes_no_rng():
    """decode-reference §5 (0x4053B4-0x4053EF): the stock step consumes NO RNG —
    it is a pure function of (quantity, price, M, cap)."""
    params = inspect.signature(stock.step_quantity).parameters
    assert "rng" not in params
    assert list(params) == ["quantity", "price", "M", "cap"]
    # deterministic and repeatable with no RNG object anywhere in play.
    assert stock.step_quantity(50, 5000, 10000, 100) == stock.step_quantity(
        50, 5000, 10000, 100)


# --- §6  rumor generation + 1-day lead time  (0x40405A, 0x40415E) ----------
def test_generate_rumors_current_city_gate_and_fields():
    """decode-reference §6 (0x40405A): rand%3 == 0 gates a current-city rumor;
    the rumor draws drug (rand%17) then direction (rand%2). Draws here: gate1 +
    drug + direction + gate2 = 4."""
    g = _game_with_rng([0, 0, 0, 1])   # gate1=0 fire; drug 0=Cocaine; dir 0=scarce; gate2=1 skip
    g.player.city = "Vancouver"
    out = g._generate_rumors()
    assert len(out) == 1
    assert (out[0].drug, out[0].city, out[0].direction) == ("Cocaine", "Vancouver", "scarce")
    assert g._scheduled == {("Vancouver", "Cocaine"): +1}
    assert g.rng.count == 4


def test_generate_rumors_other_city_gate_and_fields():
    """decode-reference §6 (0x40415E): independently, rand%3 == 0 gates a rumor
    about one random OTHER city (rand % 14), then drug (rand%17) + direction
    (rand%2). Draws: gate1 + gate2 + other-pick + drug + direction = 5."""
    g = _game_with_rng([1, 0, 0, 16, 1])  # gate1=1 skip; gate2=0 fire; other 0; drug 16=Speed; dir 1=abundant
    g.player.city = "Vancouver"
    out = g._generate_rumors()
    others = [c for c in C.CITIES if c != "Vancouver"]
    assert len(out) == 1
    assert (out[0].drug, out[0].city, out[0].direction) == ("Speed", others[0], "abundant")
    assert g._scheduled == {(others[0], "Speed"): -1}
    assert g.rng.count == 5


def test_generate_rumors_no_rumor_when_gates_miss():
    """decode-reference §6: both rand%3 gates non-zero -> no rumors, no scheduling,
    two draws consumed."""
    g = _game_with_rng([2, 2])
    g.player.city = "Vancouver"
    out = g._generate_rumors()
    assert out == []
    assert g._scheduled == {}
    assert g.rng.count == 2


def test_make_rumor_drug_index_and_direction():
    """decode-reference §6: drug = DRUGS[rand%17]; direction 50/50 by rand%2
    (0 -> scarce/+1, 1 -> abundant/-1)."""
    g = _game_with_rng([16, 0])            # DRUGS[16] = Speed, scarce
    r = g._make_rumor("Boston")
    assert (r.drug, r.city, r.direction) == ("Speed", "Boston", "scarce")
    assert g._scheduled[("Boston", "Speed")] == +1

    g = _game_with_rng([0, 1])             # DRUGS[0] = Cocaine, abundant
    r = g._make_rumor("Boston")
    assert (r.drug, r.city, r.direction) == ("Cocaine", "Boston", "abundant")
    assert g._scheduled[("Boston", "Cocaine")] == -1


def test_scheduled_event_has_one_day_lead_time_and_no_persistence():
    """decode-reference §4a/§6: a rumor scheduled today queues its event for
    EXACTLY the next day, once — consumed on D+1 and not re-fired on D+2."""
    g = Game(1)
    g._scheduled = {("Boston", "Cocaine"): +1}     # as if set on day D

    received_next_day = []
    g._roll_event = lambda market, M, scheduled: (
        received_next_day.append(scheduled) if scheduled is not None else None)
    g.step_day()                                   # day D+1: applied exactly once
    assert received_next_day == [+1]

    g._scheduled = {}                              # isolate: no fresh scheduling
    received_day_after = []
    g._roll_event = lambda market, M, scheduled: (
        received_day_after.append(scheduled) if scheduled is not None else None)
    g.step_day()                                   # day D+2: must NOT re-fire
    assert received_day_after == []
