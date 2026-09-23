"""Carry (No-Scent airport) detection + police-encounter model.

Trading volume does NOT raise ordinary police risk, but flying WITH drugs does
unless covered by No-Scent (1 can/100 units, max 10 cans -> <=1000 fully
covered). Shipping avoids the airport check entirely (but see shipping failure).

Combat RESOLUTION (loss on a loss / death / flee) is UNKNOWN — expose it as a
parameter/hook, do not invent a number.

STUB — implement per docs/tasks/p04-risk.md.
"""
import math
from . import constants as C


def cans_needed(units: int) -> int:
    """ceil(units / 100)."""
    raise NotImplementedError


def detection_prob(units: int, cans: int) -> float:
    """covered = min(units, 100*cans);
    0.99 * (0.01 + 0.99*(units-covered)/units).  units<=0 -> 0.0.
    Checks: (500,5)->~0.0099, (500,3)->~0.402, (1000,10)->~0.0099,
    (3500,10)->~0.710, (20000,10)->~0.941."""
    raise NotImplementedError


def law_encounter_prob(day: int) -> float:
    """~0.1042 per day after day 1; day<=1 -> 0.0."""
    raise NotImplementedError


def channel_recommendation(units, unit_value, cans_available, origin, dest,
                           shipper, days, bust_cost_hook=None):
    """Compare carrying (detection risk via detection_prob, with a caller-supplied
    bust_cost_hook for the loss magnitude — default None means 'unknown, report
    prob only') vs shipping (shipping.expected_ship_value). Return a small dict
    describing each channel's EV / risk so the planner can choose. Keep the
    unknown combat cost as an explicit input, never a guess."""
    raise NotImplementedError
