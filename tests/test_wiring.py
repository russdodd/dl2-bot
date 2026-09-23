"""Wiring tests: the LIVE bot's decision math now values trades with dl2model's
exact formulas. These exercise the PURE decision functions with synthetic state
(no screen, no game). They enforce the owner's hard rules:

  * the exact-model EV is an ADDED column alongside the observed price, never a
    replacement — the headline/observed number is always still present;
  * a delivery-failure probability is priced into the shipping ranking;
  * a rumor conditions tomorrow's price above the ordinary decay;
  * old state JSON with no rank/rumors/no_scent still runs (back-compat).
"""
import json
import os

import pytest

import optimizer
import decay
import dl2sweep
import shiprates
from dl2model import prices, shipping, constants as C

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)


# --- the canonical hand-computed spike (from the task doc) ------------------
# observed $60,000, M=5100 (Cocaine in a x1.00 city), n=1  ->  EV ≈ $43,616.5
SPIKE_OBS = 60000
SPIKE_EV = 43616.5


def test_hand_computed_ev_matches_model():
    assert prices.expected_price_after_days(SPIKE_OBS, 5100, 1) == pytest.approx(SPIKE_EV)
    # normal_mean gives that 5100 for Cocaine in a x1.00 city
    assert C.normal_mean("Cocaine", "New York") == 5100
    # decay.expected_sell_price (the wiring entry point) agrees
    assert decay.expected_sell_price(SPIKE_OBS, "Cocaine", "New York", n=1) == \
        pytest.approx(SPIKE_EV)


def _spike_state():
    """Buy Cocaine cheap in Boston, sell into a $60k New York spike."""
    return {
        "current_city": "Boston",
        "cash": 100_000_000,
        "capacity": 100000,
        "market": {"Cocaine": {"price": 5000, "qty": 100}},
        "prices": {
            "Boston": {"Cocaine": 5000},
            "New York": {"Cocaine": SPIKE_OBS},
        },
    }


def test_optimizer_exposes_ev_matching_model():
    plans = optimizer.optimize(_spike_state())
    ny = next(p for p in plans if p.destination == "New York")
    load = next(l for l in ny.buys if l.drug == "Cocaine")
    # observed/headline price is UNCHANGED and still present (hard rule)
    assert load.unit_sell == SPIKE_OBS
    # EV is the exact-model value, carried ALONGSIDE the observed price
    assert load.unit_sell_ev == pytest.approx(SPIKE_EV)
    # ev_profit uses the decayed price; headline profit still uses the observed one
    assert load.profit == (SPIKE_OBS - 5000) * load.qty
    assert load.ev_profit == pytest.approx((SPIKE_EV - 5000) * load.qty)


def test_combined_destinations_exposes_ev_matching_model():
    st = _spike_state()
    matrix = st["prices"]
    dests = dl2sweep.combined_destinations(
        "Boston", matrix, st["market"], {}, ship_rates={},
        cash=st["cash"], carry=100000)
    ny = next(r for r in dests if r["city"] == "New York")
    # headline arb keeps the OBSERVED sell price (nothing dropped)
    d, units, buy, sell, profit = ny["arb"][0]
    assert d == "Cocaine" and sell == SPIKE_OBS
    # the EV mirror row decays it to the exact-model value
    ed, eunits, ebuy, ev_sell, ev_profit = ny["arb_ev"][0]
    assert ev_sell == pytest.approx(SPIKE_EV)
    assert ev_profit == pytest.approx(profit - units * (SPIKE_OBS - SPIKE_EV))
    # headline combined preserved; EV combined is strictly lower for a spike
    assert ny["ev_combined"] < ny["combined"]


def test_headline_price_never_dropped():
    """Every observed price survives into the output unchanged (hard rule)."""
    plans = optimizer.optimize(_spike_state())
    ny = next(p for p in plans if p.destination == "New York")
    # projected_cash uses the observed price; ev_cash is the added figure
    assert ny.projected_cash > ny.ev_cash                 # spike decays under EV
    load = ny.buys[0]
    assert load.unit_sell == SPIKE_OBS                    # observed intact
    assert load.unit_sell_ev is not None and load.unit_sell_ev < load.unit_sell


# --- integration point 2: delivery-failure probability priced in ------------
def test_shipping_ev_prices_in_failure_probability():
    origin, dest, units, unit_value = "Boston", "New York", 100, 10_000
    ranking = dl2sweep.shipper_ev_ranking(origin, dest, units, unit_value, days=1)
    ev = {shipper: v for shipper, p, v in ranking}
    # the 0.99 shipper beats the 0.50 shipper on the same trade
    assert ev["International Couriers"] > ev["Quicker Shipper"]
    # and it agrees with the exact model's expected_ship_value
    assert ev["International Couriers"] == pytest.approx(
        shipping.expected_ship_value(origin, dest, units, unit_value,
                                     "International Couriers", 1))
    # ranking is sorted best-EV first
    assert ranking[0][2] == max(ev.values())


def test_shipper_advisory_is_advisory_only():
    # a low-value trade where the cheap 0.50 shipper actually wins on EV: the
    # advisory SURFACES it but the default (International Couriers) is unchanged.
    adv = dl2sweep.ship_advisory("Vancouver", "Sydney", units=1000, unit_value=20)
    assert adv is not None
    assert adv["default"][0] == dl2sweep.DEFAULT_SHIPPER      # default never changes
    # the ranking is still exposed for the human to see
    assert len(adv["ranking"]) == len(C.SHIPPERS)


def test_best_net_plan_adds_failure_priced_ev():
    market = {"Cocaine": {"price": 5000, "qty": 100}}
    matrix = {"New York": {"Cocaine": 20000}}
    rates = {"New York": 30.0}
    plans = dl2sweep.best_net_plan(market, matrix, rates, origin="Boston")
    ny = next(p for p in plans if p["city"] == "New York")
    assert "ship_ev" in ny                                 # added alongside 'total'
    assert ny["total"] > 0                                 # headline unchanged
    # EV (0.99 delivery) is a touch under the headline gross (failure + fee)
    assert ny["ship_ev"] < ny["total"] + 1


# --- integration point 4: rumor conditioning --------------------------------
def test_rumor_scarce_values_above_ordinary_decay():
    obs = 5000                                             # near-normal price
    ordinary = decay.expected_sell_price(obs, "Cocaine", "New York", n=1)
    scarce = decay.expected_sell_price(obs, "Cocaine", "New York", n=1,
                                       rumor={"direction": "scarce"})
    assert scarce > ordinary                               # rumor lifts it
    # without a rumor it's exactly the ordinary decay value
    assert ordinary == pytest.approx(prices.expected_next_price(obs, 5100))


def test_rumor_flows_through_combined_destinations():
    market = {"Cocaine": {"price": 1000, "qty": 100}}
    matrix = {"Boston": {"Cocaine": 1000}, "New York": {"Cocaine": 5000}}
    base = dl2sweep.combined_destinations("Boston", matrix, market, {}, {},
                                          cash=10_000_000, carry=100000)
    rumored = dl2sweep.combined_destinations(
        "Boston", matrix, market, {}, {}, cash=10_000_000, carry=100000,
        rumors=[{"drug": "Cocaine", "city": "New York", "direction": "scarce"}])
    ny_base = next(r for r in base if r["city"] == "New York")
    ny_rum = next(r for r in rumored if r["city"] == "New York")
    # the scarce rumor raises the EV valuation of that destination
    assert ny_rum["ev_net"] > ny_base["ev_net"]
    # headline (observed) is identical either way — rumor only touches EV
    assert ny_rum["arb"] == ny_base["arb"]


# --- integration point 3: remote stock estimate -----------------------------
def test_remote_stock_estimate():
    # Cocaine in New York, M=5100; at the observed price == M, the stock target
    # is half the rank capacity (Dealer -> 100 units).
    est = dl2sweep.estimate_remote_stock(5100, "Cocaine", "New York", "Dealer")
    assert est == 50
    # unknown drug/city -> None (clean back-compat, never raises)
    assert dl2sweep.estimate_remote_stock(5100, "Ecstasy", "New York", "Dealer") is None


# --- integration point 5: carry-vs-ship channel advisory --------------------
def test_channel_advice_reports_detection_prob_not_a_guess():
    adv = dl2sweep.channel_advice(units=500, unit_value=10000, no_scent=5,
                                  origin="Boston", dest="New York")
    assert adv is not None
    carry = adv["carry"]
    # 5 cans fully cover 500 units -> near-floor detection probability
    assert carry["detection_prob"] == pytest.approx(0.0099, abs=1e-4)
    # combat-loss magnitude is UNKNOWN -> reported as unknown, never invented
    assert carry["bust_loss_known"] is False
    assert carry["bust_loss"] is None
    # ship side prices the delivery-failure in
    assert adv["ship"]["expected_value"] is not None


# --- shiprates cross-check / fill (measured value stays authoritative) ------
def test_shiprates_model_cross_check_and_fill():
    model = shiprates.model_per_unit_rate("Boston", "New York")
    assert model == pytest.approx(
        shipping.per_unit_rate("Boston", "New York", "International Couriers", 1))
    # a measured rate far from the model is FLAGGED, not overridden
    disc = shiprates.rate_discrepancy("Boston", "New York", measured=model + 1000)
    assert disc and disc["measured"] == pytest.approx(model + 1000)
    assert disc["model"] == pytest.approx(model)
    # a matching measured rate produces no discrepancy
    assert shiprates.rate_discrepancy("Boston", "New York", measured=model) is None
    # fill supplies model rates for un-measured destinations
    filled = shiprates.fill_missing_from_model("Boston", ["New York", "Moscow"])
    assert set(filled) == {"New York", "Moscow"}


# --- back-compat: old state JSON with no rank/rumors/no_scent ---------------
def test_old_state_json_still_runs():
    st = json.load(open(os.path.join(REPO, "sample_state.json")))
    assert "rank" not in st and "rumors" not in st        # genuinely old shape
    plans = optimizer.optimize(st)
    assert plans                                          # produced a ranking
    # every observed sell price is still present on the loads (nothing dropped);
    # unknown drugs (e.g. the OCR misspelling "Ecstasy") simply carry no EV.
    for p in plans:
        for l in p.buys:
            assert l.unit_sell is not None
    # a totally minimal state (no market/ship/rumors/no_scent) also runs
    minimal = {"current_city": "New York", "cash": 5000, "capacity": 10,
               "prices": {"New York": {"Cocaine": 5000},
                          "London": {"Cocaine": 9000}},
               "market": {"Cocaine": {"price": 5000, "qty": 10}}}
    assert optimizer.optimize(minimal)
