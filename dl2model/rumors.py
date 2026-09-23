"""Rumor forward-signal model.

A rumor ("X will be scarce/abundant in <city> tomorrow") is shown before you
choose where to fly and names drug + city. It schedules tomorrow's event with
70% predicted / 20% nothing / 10% opposite -> realized 70.2% true, 10.2%
opposite, 19.6% none. This is the strongest route-selection signal in the game;
when a rumor is active, condition on it instead of the ordinary decay model.

Realized probabilities (see constants.RUMOR_P_*): the 0.2% bumps on true and
opposite come from the ordinary 2% event roll that still fires inside the 20%
"nothing" branch (0.20 * 0.02 = 0.004, split 50/50 into shortage/glut).
"""
import re

from dataclasses import dataclass
from . import constants as C
from . import prices

# Expectation of the (5 + rand()%5) event multiplier: rand()%5 is uniform on
# 0..4 (mean 2.0), so E[5 + rand()%5] = 7.0.
E_EVENT_MULT = 7.0


@dataclass
class Rumor:
    drug: str
    city: str
    direction: str        # "scarce" | "abundant"


def _find_name(text_lower: str, names) -> str | None:
    """Return the longest name from `names` that appears in `text_lower` as a
    whole word (case-insensitive), or None. Longest-first avoids a shorter name
    matching inside a longer one."""
    for name in sorted(names, key=len, reverse=True):
        if re.search(r"\b" + re.escape(name.lower()) + r"\b", text_lower):
            return name
    return None


def parse_rumor(text: str):
    """Extract a Rumor from a UI string like 'You hear a rumor that Cocaine will
    be scarce in Boston tomorrow.' Return None if it isn't a rumor line.
    Match drug names from constants.DRUGS and cities from constants.CITIES."""
    if not text:
        return None
    low = text.lower()
    if "scarce" in low:
        direction = "scarce"
    elif "abundant" in low:
        direction = "abundant"
    else:
        return None
    drug = _find_name(low, C.DRUGS)
    city = _find_name(low, C.CITIES)
    if drug is None or city is None:
        return None
    return Rumor(drug, city, direction)


def outcome_probs() -> dict:
    """{'true': 0.702, 'opposite': 0.102, 'none': 0.196}."""
    return {
        "true": C.RUMOR_P_TRUE,
        "opposite": C.RUMOR_P_OPPOSITE,
        "none": C.RUMOR_P_NONE,
    }


def expected_shortage_price(M: int) -> float:
    """E[(M + rand()%(M//2)) * (5 + rand()%5)] with the two rand terms
    independent: (M + E[rand()%(M//2)]) * E[5 + rand()%5]."""
    return (M + prices.expected_rand_mod(M // 2)) * E_EVENT_MULT


def expected_glut_price(M: int) -> float:
    """E[(M - rand()%(M//2)) / (5 + rand()%5)] treating the divide as real and
    the terms as independent: (M - E[rand()%(M//2)]) / E[5 + rand()%5]."""
    return (M - prices.expected_rand_mod(M // 2)) / E_EVENT_MULT


def expected_next_price_given_rumor(price, target, M, rumor: Rumor, rng_free=None):
    """Expected tomorrow price for the rumored (city,drug), mixing:
    P_true * E[predicted-event price] + P_opposite * E[opposite-event price]
    + P_none * expected_next_price(price, target).

    "scarce" predicts a shortage (spike up), "abundant" predicts a glut (crash
    down); the opposite event is the other one. Event-price means are taken over
    their rand terms via expected_shortage_price / expected_glut_price."""
    if rumor.direction == "scarce":
        e_predicted = expected_shortage_price(M)
        e_opposite = expected_glut_price(M)
    else:  # "abundant"
        e_predicted = expected_glut_price(M)
        e_opposite = expected_shortage_price(M)
    p = outcome_probs()
    return (
        p["true"] * e_predicted
        + p["opposite"] * e_opposite
        + p["none"] * prices.expected_next_price(price, target)
    )
