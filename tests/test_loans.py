"""Loan tests: the OCR loan-window parser + the PURE loan advice (repay-by-day and
loan-to-promote). All screen-free — the parser runs against a REAL saved capture of
the Places->Finances window (titled "Dialog"), the advice against synthetic state.

You can owe several sharks at once, so the debt is a PORTFOLIO of loans; the fixture
captures exactly that (all five sharks outstanding, total Debt 32,580).

The borrow/repay screen actions are verified live with the owner (audit-logged).
"""
import json
import os

import pytest

import parse as parser
import dl2sweep
from dl2model import constants as C, finance

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURE = os.path.join(HERE, "fixtures", "loan_window.json")


# --- OCR loan-window parser (against the real capture) ----------------------
@pytest.fixture
def loans():
    return parser.parse_loan_window(json.load(open(FIXTURE)))


# the loan book in the captured game: shark -> owed
CAPTURED_OWED = {"Odd Lenny": 12800, "One-eyed Wilbur": 9000, "Laughing Max": 5880,
                 "Strange ear Leonard": 3380, "Buddles": 1520}


def test_parse_reads_total_debt(loans):
    assert loans["total_debt"] == 32580
    assert loans["total_debt"] == sum(CAPTURED_OWED.values())
    assert loans["principal"] == loans["total_debt"]


def test_parse_reads_every_shark_owed_and_rate(loans):
    for name, owed in CAPTURED_OWED.items():
        assert loans["sharks"][name]["owed"] == owed
        # OCR rate matches the decoded model table (the live cross-check)
        assert loans["sharks"][name]["rate"] == C.LOAN_SHARKS[name][1]


def test_parse_loan_book_is_a_portfolio(loans):
    book = loans["loans"]
    assert {L["shark"] for L in book} == set(CAPTURED_OWED)
    assert all(L["owed"] > 0 for L in book)
    # most-urgent single-loan back-compat: Odd Lenny (60%, days_left 1)
    assert loans["lender"] == "Odd Lenny"
    assert loans["rate"] == 60


def test_clean_shark_fuzzy_matches_ocr_noise():
    assert parser._clean_shark("Odd Lenny") == "Odd Lenny"
    assert parser._clean_shark("Strange ear Leonard") == "Strange ear Leonard"
    assert parser._clean_shark("Cocaine") is None


def test_parse_empty_is_safe():
    loans = parser.parse_loan_window({"boxes": []})
    assert loans["sharks"] == {}
    assert loans["total_debt"] is None
    assert loans["loans"] == []
    assert loans["lender"] is None


# --- repay-by-day advice (pure, portfolio-aware) ----------------------------
def _status(cash=271291, bank=0, debt=32580, day=10, total=30, rank="Drug Lord"):
    return {"cash": cash, "bank": bank, "debt": debt, "day": day,
            "total_days": total, "rank": rank}


def test_repay_portfolio_matches_finance(loans):
    la = dl2sweep.loan_advisory(_status(), loans, value_per_extra_day=0)
    r = la["repay"]
    expect_next = sum(o * C.LOAN_SHARKS[n][1] // 100 for n, o in CAPTURED_OWED.items())
    expect_clear = sum(finance.early_payment_total(o, C.LOAN_SHARKS[n][1])
                       for n, o in CAPTURED_OWED.items())
    assert r["next_day_interest"] == expect_next == 15774
    assert r["clear_now"] == expect_clear == 40467
    assert any("across 5 shark(s)" in ln for ln in la["lines"])
    assert any("Repay costliest first" in ln for ln in la["lines"])


def test_repay_flags_soonest_due(loans):
    la = dl2sweep.loan_advisory(_status(), loans, 0)
    # Odd Lenny reads days_left 1 -> a due-now warning naming it
    assert any("Odd Lenny due in 1d" in ln for ln in la["lines"])


def test_no_debt_means_no_repay_advice():
    la = dl2sweep.loan_advisory(_status(debt=0), None, value_per_extra_day=0)
    assert la["repay"] is None


def test_unread_book_falls_back_to_status_debt():
    # no loan-window cache, only the Status-box total: still warns to clear it
    la = dl2sweep.loan_advisory(_status(debt=5000, day=29, total=30), None, 0)
    assert la["repay"]["total_debt"] == 5000
    assert any("per-shark terms unread" in ln for ln in la["lines"])
    assert any("clear debt before the end" in ln for ln in la["lines"])


# --- loan-to-promote advice (pure) ------------------------------------------
def test_loan_to_promote_taken_when_days_are_valuable():
    # wealth 35,000 -> next threshold Dealer (40,000): a $5,000 loan vaults it.
    la = dl2sweep.loan_advisory(_status(cash=30000, bank=5000, debt=0),
                                None, value_per_extra_day=100000)
    p = la["promote"]
    assert p["take"] is True
    assert p["target_rank"] == "Dealer"
    assert p["loan"] == C.RANK_THRESHOLD["Dealer"] - 35000
    assert any("Loan-to-promote PAYS" in ln for ln in la["lines"])


def test_loan_to_promote_declined_when_days_are_cheap():
    la = dl2sweep.loan_advisory(_status(cash=30000, bank=5000, debt=0),
                                None, value_per_extra_day=1)
    assert la["promote"]["take"] is False
    assert any("No promotional loan" in ln for ln in la["lines"])


def test_loan_to_promote_skipped_when_rank_already_earned():
    la = dl2sweep.loan_advisory(_status(cash=30000, bank=5000, debt=0),
                                None, value_per_extra_day=100000, promotions=["Dealer"])
    assert la["promote"]["take"] is False
    assert "already earned" in la["promote"]["reason"]
