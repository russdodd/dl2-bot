"""Loans, rank/capacity, overhead, game horizon, final score.

final_score = cash + bank - debt (UNSOLD INVENTORY IS WORTHLESS). Rank keys off
cash+bank (not net of debt), sustained 3 days; each first promotion grants +5
days (max 55). Debt compounds daily at the shark's rate.

STUB — implement per docs/tasks/p06-finance.md.
"""
from . import constants as C


def debt_after_days(debt: int, rate_pct: int, days: int) -> int:
    """Compound once/day: debt += debt * rate_pct // 100, `days` times."""
    raise NotImplementedError


def early_payment_total(debt: int, rate_pct: int) -> int:
    """debt + debt * rate_pct // 200  (principal + ~half a day's interest)."""
    raise NotImplementedError


def max_loan(cash: int, shark: str) -> int:
    """cash * LOAN_SHARKS[shark][0]."""
    raise NotImplementedError


def rank_for_wealth(cash_plus_bank: int) -> str:
    """Highest rank whose RANK_THRESHOLD <= cash+bank."""
    raise NotImplementedError


def capacity_for_rank(rank: str) -> int:
    raise NotImplementedError


def daily_overhead(rank: str) -> int:
    raise NotImplementedError


def final_score(cash: int, bank: int, debt: int) -> int:
    """cash + bank - debt."""
    raise NotImplementedError


def max_days(promotions_earned: int) -> int:
    """START_DAYS + PROMO_BONUS_DAYS * min(promotions_earned, MAX_PROMOTIONS)."""
    raise NotImplementedError
