"""Carry (No-Scent airport) detection + police-encounter model.

Trading volume does NOT raise ordinary police risk, but flying WITH drugs does
unless covered by No-Scent (1 can/100 units, max 10 cans -> <=1000 fully
covered). Shipping avoids the airport check entirely (but see shipping failure).

Combat RESOLUTION (loss on a loss / death / flee) is UNKNOWN — expose it as a
parameter/hook, do not invent a number.
"""
import math
from . import constants as C
from . import shipping


def cans_needed(units: int) -> int:
    """ceil(units / 100)."""
    if units <= 0:
        return 0
    return math.ceil(units / C.NO_SCENT_UNITS_PER_CAN)


def detection_prob(units: int, cans: int) -> float:
    """covered = min(units, 100*cans);
    0.99 * (0.01 + 0.99*(units-covered)/units).  units<=0 -> 0.0.
    Checks: (500,5)->~0.0099, (500,3)->~0.402, (1000,10)->~0.0099,
    (3500,10)->~0.710, (20000,10)->~0.941."""
    if units <= 0:
        return 0.0
    covered = min(units, C.NO_SCENT_UNITS_PER_CAN * cans)
    uncovered_frac = (units - covered) / units
    return C.DETECT_SCALE * (C.DETECT_BASE + C.DETECT_SCALE * uncovered_frac)


def law_encounter_prob(day: int) -> float:
    """~0.1042 per day after day 1; day<=1 -> 0.0."""
    if day <= 1:
        return 0.0
    return C.P_LAW_ENCOUNTER_PER_DAY


def channel_recommendation(units, unit_value, cans_available, origin, dest,
                           shipper, days, bust_cost_hook=None):
    """Compare carrying (detection risk via detection_prob, with a caller-supplied
    bust_cost_hook for the loss magnitude — default None means 'unknown, report
    prob only') vs shipping (shipping.expected_ship_value). Return a small dict
    describing each channel's EV / risk so the planner can choose. Keep the
    unknown combat cost as an explicit input, never a guess."""
    # --- carry channel: airport No-Scent detection ---
    cans_used = min(cans_available, cans_needed(units))
    p_detect = detection_prob(units, cans_used)
    if bust_cost_hook is None:
        bust_loss = None
        bust_loss_known = False
    else:
        bust_loss = bust_cost_hook(units=units, unit_value=unit_value,
                                   cans=cans_used, origin=origin, dest=dest)
        bust_loss_known = True
    carry = {
        "channel": "carry",
        "cans_needed": cans_needed(units),
        "cans_used": cans_used,
        "detection_prob": p_detect,
        "bust_loss": bust_loss,
        "bust_loss_known": bust_loss_known,
    }

    # --- ship channel: no airport check, but shipping fee/failure risk ---
    ship = {
        "channel": "ship",
        "detection_prob": 0.0,
    }
    try:
        ship["expected_value"] = shipping.expected_ship_value(
            origin, dest, units, unit_value, shipper, days)
    except NotImplementedError:
        # shipping module not implemented yet (wave-1, may merge later)
        ship["expected_value"] = None

    return {"carry": carry, "ship": ship}
