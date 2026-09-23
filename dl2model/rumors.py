"""Rumor forward-signal model.

A rumor ("X will be scarce/abundant in <city> tomorrow") is shown before you
choose where to fly and names drug + city. It schedules tomorrow's event with
70% predicted / 20% nothing / 10% opposite -> realized 70.2% true, 10.2%
opposite, 19.6% none. This is the strongest route-selection signal in the game;
when a rumor is active, condition on it instead of the ordinary decay model.

STUB — implement per docs/tasks/p05-rumors.md.
"""
from dataclasses import dataclass
from . import constants as C


@dataclass
class Rumor:
    drug: str
    city: str
    direction: str        # "scarce" | "abundant"


def parse_rumor(text: str):
    """Extract a Rumor from a UI string like 'You hear a rumor that Cocaine will
    be scarce in Boston tomorrow.' Return None if it isn't a rumor line.
    Match drug names from constants.DRUGS and cities from constants.CITIES."""
    raise NotImplementedError


def outcome_probs() -> dict:
    """{'true': 0.702, 'opposite': 0.102, 'none': 0.196}."""
    raise NotImplementedError


def expected_next_price_given_rumor(price, target, M, rumor: Rumor, rng_free=None):
    """Expected tomorrow price for the rumored (city,drug), mixing:
    P_true * E[predicted-event price] + P_opposite * E[opposite-event price]
    + P_none * expected_next_price(price, target). Use event price means from
    prices.event_shortage_price / event_glut_price (expectation over their
    rand terms). Document how you take those expectations."""
    raise NotImplementedError
