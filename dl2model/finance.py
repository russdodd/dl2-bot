"""Loans, rank/capacity, overhead, game horizon, final score.

final_score = cash + bank - debt (UNSOLD INVENTORY IS WORTHLESS). Rank keys off
cash+bank (not net of debt), sustained 3 days; each first promotion grants +5
days (max 55). Debt compounds daily at the shark's rate.

All operands here (cash, bank, debt, wealth, rates, days) are non-negative, so
plain `//` matches the game's C integer divide; no trunc_div needed.
"""
from . import constants as C


def debt_after_days(debt: int, rate_pct: int, days: int) -> int:
    """Compound once/day: debt += debt * rate_pct // 100, `days` times."""
    for _ in range(days):
        debt += debt * rate_pct // 100
    return debt


def early_payment_total(debt: int, rate_pct: int) -> int:
    """debt + debt * rate_pct // 200  (principal + ~half a day's interest)."""
    return debt + debt * rate_pct // 200


def max_loan(cash: int, shark: str) -> int:
    """cash * LOAN_SHARKS[shark][0]."""
    return cash * C.LOAN_SHARKS[shark][0]


def rank_for_wealth(cash_plus_bank: int) -> str:
    """Highest rank whose RANK_THRESHOLD <= cash+bank."""
    result = C.RANKS[0]
    for rank in C.RANKS:
        if C.RANK_THRESHOLD[rank] <= cash_plus_bank:
            result = rank
    return result


def capacity_for_rank(rank: str) -> int:
    return C.RANK_CAPACITY[rank]


def daily_overhead(rank: str) -> int:
    return C.RANK_OVERHEAD[rank]


def final_score(cash: int, bank: int, debt: int) -> int:
    """cash + bank - debt."""
    return cash + bank - debt


def max_days(promotions_earned: int) -> int:
    """START_DAYS + PROMO_BONUS_DAYS * min(promotions_earned, MAX_PROMOTIONS)."""
    return C.START_DAYS + C.PROMO_BONUS_DAYS * min(promotions_earned, C.MAX_PROMOTIONS)
