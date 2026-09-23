"""Wiring test: the live bot (dl2sweep) consumes dl2model.combat.

Screen-free — exercises the pure helpers that decide_main prints from, so it
runs without the game. Covers the combat integration:
  * channel_advice now gets a real airport bust-loss (was UNKNOWN/None),
  * recommended_deposit drives the banking advisory.
"""
import dl2sweep
from dl2model import combat


def test_channel_advice_has_real_bust_loss():
    chan = dl2sweep.channel_advice(200, 5000, no_scent=0,
                                   origin="Boston", dest="New York")
    assert chan is not None
    carry = chan["carry"]
    # detected -> carried goods lost in ~every resolution => ~units*unit_value
    assert carry["bust_loss_known"] is True
    assert carry["bust_loss"] == 200 * 5000
    # 0 cans on 200 units -> ~full detection
    assert carry["detection_prob"] > 0.9


def test_channel_advice_full_noscent_low_detection():
    # 200 units fully covered by 2 cans -> ~1% detection
    chan = dl2sweep.channel_advice(200, 5000, no_scent=2,
                                   origin="Boston", dest="New York")
    assert chan["carry"]["detection_prob"] < 0.02


def test_banking_surplus():
    # cash beyond the planned trade spend should be recommended for the bank
    assert combat.recommended_deposit(271291, 250000) == 21291
    assert combat.recommended_deposit(1000, 1000) == 0


def test_channel_advice_unknown_city_is_safe():
    # unknown city -> None, never raises (advisory degrades silently)
    assert dl2sweep.channel_advice(10, 100, 0, "Nowhere", "Alsonowhere") is None
