"""Complete decompiled data tables for Drug Lord 2.2 + shared integer helpers.

THIS FILE IS COMPLETE — do not stub it. Every other module imports from here.
Values transcribed from the DL2.2 executable reverse-engineering notes.

Integer arithmetic note: the game is C and uses truncate-toward-zero integer
division. Python's `//` floors (toward negative infinity), which differs for
negative operands. Use `trunc_div` whenever you are reproducing a C integer
divide that can see a negative numerator (the stock target can).
"""

# --- PRNG -------------------------------------------------------------------
# Microsoft Visual C++ rand(): state = state*214013 + 2531011 (mod 2^32),
# return (state >> 16) & 0x7fff  ->  an int in [0, 32767].
RAND_MULT = 214013
RAND_INC = 2531011
RAND_MOD = 2 ** 32
RAND_RANGE = 32768          # rand() yields 0 .. RAND_RANGE-1 (i.e. 0..32767)

# --- Drugs ------------------------------------------------------------------
# base price in dollars. (A, B) are two per-drug constants that exist in the
# drug struct (+0x08/+0x0C) but are VESTIGIAL/UNUSED in v2.2 — kept only for
# completeness. Do NOT build them into any model.
BASE_PRICE = {
    "Cocaine": 5100,
    "Crack": 7000,
    "Ecstacy": 3000,
    "Hashish": 1600,
    "Heroin": 7000,
    "Ice": 3000,
    "Kat": 800,
    "LSD": 1000,
    "MDA": 1000,
    "Morphine": 2000,
    "Mushrooms": 400,
    "Opium": 1500,
    "PCP": 800,
    "Peyote": 1000,
    "Pot": 800,
    "Special K": 1500,
    "Speed": 800,
}
VESTIGIAL_DRUG_CONSTANTS = {   # (A, B) — unused in v2.2, reference only
    "Cocaine": (2, 25), "Crack": (10, 45), "Ecstacy": (12, 75),
    "Hashish": (15, 35), "Heroin": (2, 35), "Ice": (7, 75), "Kat": (10, 50),
    "LSD": (10, 50), "MDA": (7, 60), "Morphine": (2, 60), "Mushrooms": (10, 75),
    "Opium": (5, 75), "PCP": (10, 100), "Peyote": (6, 60), "Pot": (20, 95),
    "Special K": (12, 50), "Speed": (12, 85),
}
DRUGS = list(BASE_PRICE)

# --- Cities -----------------------------------------------------------------
# price level as a percent of base (M = base * mult // 100).
CITY_MULT = {
    "San Francisco": 80, "Detroit": 80, "Miami": 90, "Paris": 90,
    "Austin": 100, "New York": 100, "Toronto": 100, "Vancouver": 100,
    "London": 110, "Los Angeles": 110, "Sydney": 110, "Boston": 120,
    "St Petersburg": 150, "Moscow": 160, "Beijing": 190,
}
# one-dimensional "distance coordinate" — shipping distance is |pos[a]-pos[b]|.
# (Not geographic; these are literally the game's numbers.)
CITY_POS = {
    "Austin": 1861, "Beijing": 5307, "Boston": 2509, "Detroit": 1963,
    "London": 4725, "Los Angeles": 1072, "Miami": 2802, "Moscow": 5113,
    "New York": 2435, "Paris": 4937, "San Francisco": 791, "St Petersburg": 4761,
    "Sydney": 7757, "Toronto": 2091, "Vancouver": 0,
}
CITIES = list(CITY_MULT)

# --- Shipping ---------------------------------------------------------------
# name -> (cost_rate, nominal_success_probability). Success prob == delivery
# probability; a failure is ALL-OR-NOTHING (goods + already-paid fee are lost,
# no extra fine). Literal rand()%100 gives 0.50049/0.75052/0.90021/0.99002 but
# use the nominal figures unless reproducing the RNG exactly.
SHIPPERS = {
    "Quicker Shipper": (10, 0.50),
    "Courier Stan": (20, 0.75),
    "Sharp Ship": (50, 0.90),
    "International Couriers": (100, 0.99),
}
# scheduled delivery days -> speed multiplier in the cost formula.
SPEED_MULT = {3: 1, 2: 2, 1: 4}   # 3-day=1x, 2-day=2x, overnight(1-day)=4x
# each transit day has ~20% chance to slip one more day; means:
EXPECTED_TRANSIT_DAYS = {3: 3.75, 2: 2.50, 1: 1.25}
TRANSIT_SLIP_PROB = 0.20
# cost = distance * rate * speed_mult * units // 1000  (one divide, at the end)
SHIP_COST_DIVISOR = 1000
# once ready at the destination you have a 3-day grace window to be there,
# else the whole shipment is discarded: discarded if (current_day - arrival_day) > 3.
SHIP_GRACE_DAYS = 3

# --- Ranks ------------------------------------------------------------------
# ordered lowest -> highest.
RANKS = ["Wannabe", "Small-time operator", "Dealer",
         "Big-time dealer", "Distributor", "Drug Lord"]
# carrying capacity in units (also scales every market's stock via C).
RANK_CAPACITY = {
    "Wannabe": 10, "Small-time operator": 25, "Dealer": 100,
    "Big-time dealer": 600, "Distributor": 3500, "Drug Lord": 20000,
}
# promotion threshold on (cash + bank), NOT net of debt. You must sustain the
# tier for 3 daily checks before the rank actually changes.
RANK_THRESHOLD = {
    "Wannabe": 0, "Small-time operator": 5000, "Dealer": 40000,
    "Big-time dealer": 300000, "Distributor": 2500000, "Drug Lord": 15000000,
}
RANK_PROMOTE_SUSTAIN_DAYS = 3
# daily overhead deducted from cash, by rank.
RANK_OVERHEAD = {
    "Wannabe": 10, "Small-time operator": 25, "Dealer": 100,
    "Big-time dealer": 500, "Distributor": 2500, "Drug Lord": 10000,
}

# --- Loan sharks ------------------------------------------------------------
# name -> (max_loan_multiplier_of_cash, daily_interest_percent, due_after_days)
LOAN_SHARKS = {
    "Odd Lenny": (5, 60, 1),
    "One-eyed Wilbur": (4, 50, 3),
    "Laughing Max": (3, 40, 4),          # manual says 50%; executable is 40%
    "Strange ear Leonard": (2, 30, 5),
    "Buddles": (1, 15, 6),
}
# debt compounds once/day: debt += debt * rate // 100
# early-payment penalty: debt * rate // 200 (~half a day's interest)

# --- Game horizon / start ---------------------------------------------------
START_DAYS = 30
PROMO_BONUS_DAYS = 5        # first time reaching each rank above Wannabe
MAX_PROMOTIONS = 5
MAX_DAYS = START_DAYS + PROMO_BONUS_DAYS * MAX_PROMOTIONS   # 55
START_CASH = 2000
START_DEBT = 1000          # owed to Buddles at game start
# final_score = cash + bank - outstanding_debt   (UNSOLD INVENTORY = worthless)

# --- Market events ----------------------------------------------------------
EVENT_PROB = 0.02          # per market, per day; half shortage, half glut
# shortage: price = (M + rand%(M//2)) * (5 + rand%5);  qty //= (2 + rand%5)
# glut:     price = (M - rand%(M//2)) // (5 + rand%5);  qty *=  (2 + rand%5)

# --- Listing (does the drug appear in the local market today) ---------------
# no event: listed if rand()%3 != 0  -> 2/3.  An event forces listed.
# unconditional ~ 0.02*1 + 0.98*(2/3) = 0.6733.
P_LISTED_NO_EVENT = 2.0 / 3.0
P_LISTED = 0.6733

# --- Rumors -----------------------------------------------------------------
# each day, independently: 1/3 chance of a rumor about the current city,
# 1/3 chance about one random OTHER city. A rumor names a drug + direction
# ("scarce" -> shortage, "abundant" -> glut) for TOMORROW and schedules:
#   70% the predicted event, 20% nothing (ordinary 2% roll still applies),
#   10% the opposite event.  Realized:
RUMOR_P_TRUE = 0.702
RUMOR_P_OPPOSITE = 0.102
RUMOR_P_NONE = 0.196
RUMOR_GEN_PROB = 1.0 / 3.0

# --- Airport carry detection (No-Scent) -------------------------------------
# one can covers 100 units; you may carry at most 10 cans (<=1000 units fully
# covered). cans_needed = ceil(units/100).
NO_SCENT_UNITS_PER_CAN = 100
NO_SCENT_MAX_CANS = 10
# covered = min(units, 100*cans);
# P_detect ~ 0.99 * (0.01 + 0.99 * (units - covered) / units)   (units>0)
DETECT_BASE = 0.01
DETECT_SCALE = 0.99

# --- Police / combat (ordinary daily encounters) ----------------------------
# after day 1, ~50% chance/day of some weighted random event; combat is 30/63
# of that table, and law-enforcement opponents are 35/80 of the opponent table:
P_ANY_COMBAT_PER_DAY = 30.0 / 63.0 * 0.50           # ~0.2381
P_LAW_ENCOUNTER_PER_DAY = P_ANY_COMBAT_PER_DAY * 35.0 / 80.0  # ~0.1042
# attacker COUNT scales with rank tier. Ordinary encounters do NOT inspect your
# inventory/trade size. COMBAT RESOLUTION (what you lose on a loss, whether you
# can die/flee) is UNKNOWN — treat as a TODO hook, do not invent numbers.


def trunc_div(a, b):
    """C integer division: truncates toward zero (unlike Python // which floors).

    trunc_div(-7, 2) == -3   (Python -7 // 2 == -4)
    """
    q = abs(a) // abs(b)
    return q if (a >= 0) == (b >= 0) else -q


def normal_mean(drug, city):
    """M = base_price[drug] * city_mult[city] // 100 — the 'normal' price level
    a market's hidden target wanders around (targets span ~0.5M..1.5M)."""
    return BASE_PRICE[drug] * CITY_MULT[city] // 100
