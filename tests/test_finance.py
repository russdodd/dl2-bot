from dl2model import finance
import pytest


def test_score():
    assert finance.final_score(50000, 10000, 2000) == 58000


def test_score_can_go_negative():
    assert finance.final_score(0, 0, 1000) == -1000


def test_debt_compounds():
    assert finance.debt_after_days(1000, 15, 1) == 1150     # +15%
    assert finance.debt_after_days(1000, 15, 0) == 1000
    # Odd Lenny 60%/day for 2 days: 1000 -> 1600 -> 2560
    assert finance.debt_after_days(1000, 60, 2) == 2560


def test_debt_uses_c_integer_divide():
    # 1001 * 15 // 100 == 150 (truncated), so 1001 -> 1151
    assert finance.debt_after_days(1001, 15, 1) == 1151


def test_early_payment():
    assert finance.early_payment_total(1000, 15) == 1075    # +half a day


def test_max_loan():
    assert finance.max_loan(2000, "Odd Lenny") == 10000     # 5x
    assert finance.max_loan(2000, "Buddles")   == 2000      # 1x


def test_rank_for_wealth():
    assert finance.rank_for_wealth(4999)     == "Wannabe"
    assert finance.rank_for_wealth(5000)     == "Small-time operator"
    assert finance.rank_for_wealth(40000)    == "Dealer"
    assert finance.rank_for_wealth(15000000) == "Drug Lord"


def test_rank_for_wealth_boundaries():
    assert finance.rank_for_wealth(0)        == "Wannabe"
    assert finance.rank_for_wealth(299999)   == "Dealer"
    assert finance.rank_for_wealth(300000)   == "Big-time dealer"
    assert finance.rank_for_wealth(2500000)  == "Distributor"
    assert finance.rank_for_wealth(14999999) == "Distributor"


def test_capacity_overhead_days():
    assert finance.capacity_for_rank("Dealer") == 100
    assert finance.daily_overhead("Drug Lord") == 10000
    assert finance.max_days(0) == 30
    assert finance.max_days(5) == 55
    assert finance.max_days(2) == 40


def test_max_days_caps_at_max_promotions():
    assert finance.max_days(6) == 55
    assert finance.max_days(100) == 55
