"""Loan tests: the OCR loan-window parser + the PURE loan advice (repay-by-day and
loan-to-promote). All screen-free — the parser runs against a saved fixture OCR
JSON, the advice against synthetic state (pure finance/strategy calls).

The borrow/repay screen actions are verified live with the owner (audit-logged),
not here.
"""
import json
import os

import pytest

import parse as parser
import dl2sweep
from dl2model import constants as C, finance

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURE = os.path.join(HERE, "fixtures", "loan_window.json")


# --- OCR loan-window parser --------------------------------------------------
@pytest.fixture
def loan_ocr():
    return json.load(open(FIXTURE))


def test_parse_loan_window_reads_current_loan(loan_ocr):
    loans = parser.parse_loan_window(loan_ocr)
    assert loans["lender"] == "Buddles"
    assert loans["principal"] == 1000        # the game start: $1,000 owed to Buddles
    assert loans["rate"] == 15
    assert loans["due_in"] == 6


def test_parse_loan_window_reads_every_shark(loan_ocr):
    sharks = parser.parse_loan_window(loan_ocr)["sharks"]
    assert set(sharks) == set(parser.CANON_SHARKS)
    # each OCR-read rate/term matches the decoded model table (the cross-check
    # the live reader logs on any discrepancy)
    for name, (mult, rate, due) in C.LOAN_SHARKS.items():
        assert sharks[name]["rate"] == rate
        assert sharks[name]["due"] == due


def test_clean_shark_fuzzy_matches_ocr_noise():
    assert parser._clean_shark("Buddles") == "Buddles"
    assert parser._clean_shark("to Buddles") == "Buddles"
    assert parser._clean_shark("0dd Lenny") == "Odd Lenny"     # OCR O->0
    assert parser._clean_shark("Cocaine") is None


def test_parse_loan_window_empty_is_safe():
    loans = parser.parse_loan_window({"boxes": []})
    assert loans == {"sharks": {}, "lender": None, "principal": None,
                     "rate": None, "due_in": None}


# --- repay-by-day advice (pure) ---------------------------------------------
def _status(cash=30000, bank=5000, debt=1000, day=10, total=30, rank="Dealer"):
    return {"cash": cash, "bank": bank, "debt": debt, "day": day,
            "total_days": total, "rank": rank}


def test_repay_projection_matches_finance():
    loans = {"lender": "Buddles", "rate": 15, "due_in": 6, "sharks": {}}
    la = dl2sweep.loan_advisory(_status(debt=1000), loans, value_per_extra_day=0)
    r = la["repay"]
    assert r["clear_now"] == finance.early_payment_total(1000, 15)
    assert r["debt_at_horizon"] == finance.debt_after_days(1000, 15, 6)
    assert r["debt_at_horizon"] > 1000           # it compounds


def test_no_debt_means_no_repay_advice():
    la = dl2sweep.loan_advisory(_status(debt=0), None, value_per_extra_day=0)
    assert la["repay"] is None


def test_end_of_game_repay_warning():
    loans = {"lender": "Buddles", "rate": 15, "due_in": 6, "sharks": {}}
    la = dl2sweep.loan_advisory(_status(debt=1000, day=28, total=30), loans, 0)
    assert any("repay before the end" in ln for ln in la["lines"])


def test_unknown_rate_still_advises():
    la = dl2sweep.loan_advisory(_status(debt=500), {"rate": None}, 0)
    assert la["repay"]["rate"] is None
    assert any("rate unknown" in ln for ln in la["lines"])


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
    st = _status(cash=30000, bank=5000, debt=0)
    st["promotions"] = ["Dealer"]
    la = dl2sweep.loan_advisory(st, None, value_per_extra_day=100000,
                                promotions=["Dealer"])
    assert la["promote"]["take"] is False
    assert "already earned" in la["promote"]["reason"]
