from dl2model import rumors
from dl2model import constants as C
import pytest


def test_parse():
    r = rumors.parse_rumor("You hear a rumor that Cocaine will be scarce in Boston tomorrow.")
    assert (r.drug, r.city, r.direction) == ("Cocaine", "Boston", "scarce")
    assert rumors.parse_rumor("nothing here") is None


def test_parse_abundant_and_multiword_names():
    r = rumors.parse_rumor("Word is Special K will be abundant in San Francisco soon.")
    assert (r.drug, r.city, r.direction) == ("Special K", "San Francisco", "abundant")


def test_parse_requires_all_parts():
    # direction but no drug/city
    assert rumors.parse_rumor("something will be scarce somewhere") is None
    # drug + city but no direction word
    assert rumors.parse_rumor("Cocaine prices in Boston are steady") is None
    # empty
    assert rumors.parse_rumor("") is None


def test_probs_sum():
    p = rumors.outcome_probs()
    assert p["true"] + p["opposite"] + p["none"] == pytest.approx(1.0, abs=1e-3)


def test_probs_from_constants():
    p = rumors.outcome_probs()
    assert p["true"] == C.RUMOR_P_TRUE
    assert p["opposite"] == C.RUMOR_P_OPPOSITE
    assert p["none"] == C.RUMOR_P_NONE


def test_expected_given_rumor(monkeypatch):
    monkeypatch.setattr(rumors.prices, "expected_rand_mod", lambda g: g / 2)
    monkeypatch.setattr(rumors.prices, "expected_next_price", lambda p, t: p)
    r = rumors.Rumor("Cocaine", "Boston", "scarce")
    val = rumors.expected_next_price_given_rumor(5100, 5100, 5100, r)
    assert val > 5100      # a scarcity rumor pulls expectation well above M


def test_expected_scarce_vs_abundant(monkeypatch):
    monkeypatch.setattr(rumors.prices, "expected_rand_mod", lambda g: g / 2)
    monkeypatch.setattr(rumors.prices, "expected_next_price", lambda p, t: p)
    M = 5100
    scarce = rumors.expected_next_price_given_rumor(M, M, M, rumors.Rumor("Cocaine", "Boston", "scarce"))
    abundant = rumors.expected_next_price_given_rumor(M, M, M, rumors.Rumor("Cocaine", "Boston", "abundant"))
    # A scarcity rumor's expectation is far higher than an abundance rumor's:
    # the shortage event multiplies price (~*7) while the glut event divides it,
    # so the shortage term dominates whichever side it lands on.
    assert scarce > abundant > M


def test_expected_matches_closed_form(monkeypatch):
    monkeypatch.setattr(rumors.prices, "expected_rand_mod", lambda g: g / 2)
    monkeypatch.setattr(rumors.prices, "expected_next_price", lambda p, t: p)
    M = 5100
    e_short = (M + M // 2 / 2) * 7.0
    e_glut = (M - M // 2 / 2) / 7.0
    p = rumors.outcome_probs()
    expected = p["true"] * e_short + p["opposite"] * e_glut + p["none"] * M
    val = rumors.expected_next_price_given_rumor(M, M, M, rumors.Rumor("Cocaine", "Boston", "scarce"))
    assert val == pytest.approx(expected)
