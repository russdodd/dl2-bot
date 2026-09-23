from dl2model import risk
import pytest
import math


def test_cans_needed():
    assert risk.cans_needed(1) == 1
    assert risk.cans_needed(100) == 1
    assert risk.cans_needed(101) == 2


def test_detection_table():
    assert risk.detection_prob(500, 5) == pytest.approx(0.0099, abs=1e-4)
    assert risk.detection_prob(500, 3) == pytest.approx(0.402, abs=1e-3)
    assert risk.detection_prob(1000, 10) == pytest.approx(0.0099, abs=1e-4)
    assert risk.detection_prob(3500, 10) == pytest.approx(0.710, abs=1e-3)
    assert risk.detection_prob(20000, 10) == pytest.approx(0.941, abs=1e-3)
    assert risk.detection_prob(0, 0) == 0.0


def test_law_encounter():
    assert risk.law_encounter_prob(1) == 0.0
    assert risk.law_encounter_prob(5) == pytest.approx(0.1042, abs=1e-3)


def test_cans_needed_zero():
    assert risk.cans_needed(0) == 0


def test_detection_prob_negative_units():
    assert risk.detection_prob(-10, 0) == 0.0


def test_detection_prob_partial_coverage_caps_cans():
    # More cans than needed -> fully covered, same as exact coverage.
    assert risk.detection_prob(150, 10) == pytest.approx(risk.detection_prob(150, 2), abs=1e-9)


def test_channel_recommendation_no_hook_reports_prob_only(monkeypatch):
    # Stub the ship branch so it is exercised regardless of shipping's state.
    monkeypatch.setattr(risk.shipping, "expected_ship_value",
                        lambda *a, **k: 12345.0)
    rec = risk.channel_recommendation(
        units=500, unit_value=10.0, cans_available=3,
        origin="A", dest="B", shipper="fast", days=3, bust_cost_hook=None)

    carry = rec["carry"]
    assert carry["detection_prob"] == pytest.approx(0.402, abs=1e-3)
    assert carry["bust_loss"] is None
    assert carry["bust_loss_known"] is False
    assert carry["cans_used"] == 3  # min(cans_available=3, cans_needed=5)

    ship = rec["ship"]
    assert ship["expected_value"] == 12345.0
    assert ship["detection_prob"] == 0.0


def test_channel_recommendation_with_hook(monkeypatch):
    monkeypatch.setattr(risk.shipping, "expected_ship_value",
                        lambda *a, **k: 0.0)
    monkeypatch_value = 999.0

    def hook(**kwargs):
        return monkeypatch_value

    rec = risk.channel_recommendation(
        units=500, unit_value=10.0, cans_available=10,
        origin="A", dest="B", shipper="fast", days=3, bust_cost_hook=hook)
    carry = rec["carry"]
    assert carry["cans_used"] == 5  # min(10, cans_needed(500)=5)
    assert carry["bust_loss"] == 999.0
    assert carry["bust_loss_known"] is True


def test_channel_recommendation_ship_not_implemented(monkeypatch):
    def raise_ni(*a, **k):
        raise NotImplementedError

    monkeypatch.setattr(risk.shipping, "expected_ship_value", raise_ni)
    rec = risk.channel_recommendation(
        units=200, unit_value=5.0, cans_available=2,
        origin="A", dest="B", shipper="slow", days=1, bust_cost_hook=None)
    assert rec["ship"]["expected_value"] is None
