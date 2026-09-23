"""Integration tests for the day-stepping engine. Wave 1 is merged, so these
exercise the REAL prices/stock/shipping/rumors/finance/prng modules end-to-end —
no monkeypatching, no xfail."""
from dl2model import shipping, finance
from dl2model.simulator import Game, Market, Player


# --- determinism ------------------------------------------------------------
def test_determinism_10_days():
    def run():
        g = Game(7)
        for _ in range(10):
            g.step_day()
        return g.world

    assert run() == run()


def test_determinism_includes_player_state():
    def run():
        g = Game(7)
        for _ in range(10):
            g.step_day()
        p = g.player
        return (p.cash, p.bank, p.debt, p.rank, p.city, g.day)

    assert run() == run()


def test_different_seeds_differ():
    a = Game(1)
    b = Game(2)
    for _ in range(5):
        a.step_day()
        b.step_day()
    assert a.world != b.world


# --- construction -----------------------------------------------------------
def test_world_is_fully_populated():
    from dl2model import constants as C
    g = Game(1)
    assert set(g.world) == set(C.CITIES)
    for city in C.CITIES:
        assert set(g.world[city]) == set(C.DRUGS)
        for drug in C.DRUGS:
            m = g.world[city][drug]
            assert isinstance(m, Market)
            assert m.quantity >= 0
            # initial price sits in the 0.5M .. 1.5M band, target == price.
            M = C.BASE_PRICE[drug] * C.CITY_MULT[city] // 100
            assert M // 2 <= m.price <= M // 2 + M
            assert m.target == m.price


def test_default_start_is_vancouver():
    assert Game(1).player.city == "Vancouver"
    assert Game(1, start_city="Beijing").player.city == "Beijing"


# --- buy / sell -------------------------------------------------------------
def test_buy_then_sell_conserves_quantity_and_moves_cash():
    g = Game(1, start_city="Vancouver")
    drug = "Cocaine"
    m = g.world["Vancouver"][drug]
    q0, cash0 = m.quantity, g.player.cash
    buy_price = m.price

    g.buy(drug, 3)
    assert g.player.cash == cash0 - buy_price * 3
    assert m.quantity == q0 - 3                       # buy decrements
    assert g.player.inventory[drug]["qty"] == 3

    # price moves before we sell; the round trip must move cash by (sell-buy)*qty
    m.price = buy_price + 100
    sell_price = m.price
    g.sell(drug, 3)
    assert m.quantity == q0                            # sell increments -> conserved
    assert g.player.cash == cash0 + (sell_price - buy_price) * 3
    assert g.player.inventory[drug]["qty"] == 0


def test_sell_same_price_is_cash_neutral():
    g = Game(2, start_city="Vancouver")
    drug = "Heroin"
    m = g.world["Vancouver"][drug]
    cash0 = g.player.cash
    g.buy(drug, 2)
    g.sell(drug, 2)                                    # no price move between
    assert g.player.cash == cash0


# --- score ------------------------------------------------------------------
def test_score_ignores_inventory():
    holding = Game(1)
    empty = Game(1)
    # identical money, but `holding` also sits on 500 unsold units.
    for g in (holding, empty):
        g.player.cash, g.player.bank, g.player.debt = 50000, 10000, 3000
    holding.player.inventory["Cocaine"] = {"qty": 500, "avg_buy": 5000}
    assert holding.score() == empty.score()
    assert holding.score() == finance.final_score(50000, 10000, 3000)


# --- rank promotion ---------------------------------------------------------
def test_rank_promotion_requires_three_sustained_days():
    g = Game(1)
    g.player.cash = 10000        # well past Small-time operator's 5000 threshold
    assert g.player.rank == "Wannabe"

    g.step_day()
    assert g.player.rank == "Wannabe"      # 1 day sustained -> not yet
    g.step_day()
    assert g.player.rank == "Wannabe"      # 2 days -> still not
    g.step_day()
    assert g.player.rank == "Small-time operator"   # 3rd sustained day promotes
    assert g.promotions_earned == 1
    assert g.max_days == 35                # +5 horizon days for the first promotion


def test_promotion_resets_when_wealth_drops():
    g = Game(1)
    g.player.cash = 10000
    g.step_day()                           # sustain = 1
    g.player.cash = 100                    # fell back below threshold
    g.step_day()                           # streak broken
    g.player.cash = 10000
    g.step_day()                           # sustain = 1 again
    assert g.player.rank == "Wannabe"


# --- finance side effects ---------------------------------------------------
def test_daily_overhead_and_debt_compound():
    g = Game(1)
    cash0, debt0 = g.player.cash, g.player.debt
    g.step_day()
    assert g.player.cash == cash0 - finance.daily_overhead("Wannabe")
    assert g.player.debt == debt0 + debt0 * g.loan_rate // 100


# --- rumors -----------------------------------------------------------------
def test_step_day_returns_rumor_list():
    g = Game(3)
    seen = []
    for _ in range(20):
        rs = g.step_day()
        assert isinstance(rs, list)
        seen.extend(rs)
    assert seen, "20 days should surface at least one rumor"
    for r in seen:
        assert r.direction in ("scarce", "abundant")
        assert r.drug in g.world[r.city]


# --- shipping ---------------------------------------------------------------
def test_ship_pays_fee_and_removes_inventory():
    g = Game(1, start_city="Vancouver")
    g.player.inventory["Cocaine"] = {"qty": 100, "avg_buy": 5000}
    cash0 = g.player.cash
    g.ship("Beijing", "Cocaine", 100, "Quicker Shipper", 3)
    assert g.player.inventory["Cocaine"]["qty"] == 0
    cost = shipping.shipping_cost("Vancouver", "Beijing", 100, "Quicker Shipper", 3)
    assert g.player.cash == cash0 - cost
    assert len(g.shipments) == 1


def test_fly_changes_city_and_advances_day():
    g = Game(1, start_city="Vancouver")
    day0 = g.day
    g.fly("London")
    assert g.player.city == "London"
    assert g.day == day0 + 1
