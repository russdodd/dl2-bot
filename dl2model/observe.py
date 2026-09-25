"""State-conditioned world sampler — the novel capability for the live planner.

`montecarlo.evaluate` / `_new_game` seed a FRESH random world and only override
the *player* attributes; the market prices/quantities come from the seed, not
from what the player sees on screen. For a live receding-horizon planner that is
wrong: the day-0 world must MATCH the observed OCR. `sample_world` builds a
`simulator.Game` whose day-0 `world[city][drug]` reproduces the observed
prices/quantities exactly, and samples only the *hidden* state (each market's
hidden target, and any rumored event scheduled for tomorrow). Everything
downstream (stepping, events, listing risk) reuses the exact `simulator.Game`.

Sampling assumptions (documented, never silent — decode-reference.md is truth):

* **Observed price/quantity are ground truth (§2-§3).** For every market present
  in `state["prices"]` we overwrite `market.price` (and `market.quantity` from
  `state["stock"]` when given). Markets NOT observed — remote cities the OCR did
  not read — keep the `Game(seed)` values as a *prior*; daily re-planning with a
  fresh sweep corrects any drift there.

* **Hidden target (§2-§3).** Targets are unobservable. The normal target range is
  `[M//2, M//2 + M - 1]` (`M = normal_mean(drug, city)`; this is exactly the span
  `prices.new_target` draws from). Price drifts toward its target and is capped at
  it each day (`prices.step_price`), so under ordinary dynamics the price can
  never sit far outside that band. We therefore read the observed price as:
    - **Normal** (`EVENT_LOW_FACTOR*M <= price <= EVENT_HIGH_FACTOR*M`): the price
      is tracking a target inside the band, so we sample the target *near the
      observed price* (within ±10% of `M`, clamped to the band). Drift then keeps
      the price close to where we saw it.
    - **Spike far above the band** (`price > EVENT_HIGH_FACTOR*M`): only a shortage
      event (§4b, ~5M..13.5M) can push price above `~1.5*M`. Per §4b the shock
      overwrites *price* but NOT the target, so we set the target to a fresh
      NORMAL draw and mark `market.event = +1`. The next `step_day` then decays
      the spike back toward normal — the emergent multi-day spike-decay (§3).
    - **Crash far below the band** (`price < EVENT_LOW_FACTOR*M`): symmetric — a
      glut event (§4b, ~M/18..M/5). Fresh normal target, `market.event = -1`.
  Thresholds are the band edges `1.5*M` / `0.2*M` (= M/5, the glut ceiling).
  Perfection is not required: a mis-classified borderline price self-corrects at
  tomorrow's re-plan.

* **Active rumors (§4a).** A rumor names (city, drug, direction) for TOMORROW. We
  inject the scheduled shock into `game._scheduled[(city, drug)] = +1/-1` exactly
  as `Game._make_rumor` does, so the NEXT `step_day` honors the decoded 70/20/10
  realized reliability (§4a). This is the strongest exploitable signal.

* **Listing risk (§9).** Not sampled here — it is already the decoded 2/3 Bernoulli
  inside `stock.is_listed`, rolled fresh each `step_day`, so "the drug I meant to
  trade isn't listed on arrival" shows up naturally as downside mass in the
  rollout (an event-active drug is always listed).

Pure and screen-free: `rng` draws come from the `Game`'s own `MsvcRand`, so a
given `seed` yields a deterministic sampled world.
"""
from . import constants as C
from . import prices
from .constants import normal_mean
from .simulator import Game

# Band edges for classifying an observed price (multiples of M = normal_mean).
# Normal targets span [M//2, M//2 + M - 1] ~= [0.5*M, 1.5*M]; price is capped at
# its target by drift, so anything clearly outside the band is an event shock.
EVENT_HIGH_FACTOR = 1.5      # price > 1.5*M  -> shortage event active (§4b)
EVENT_LOW_FACTOR = 0.2       # price < 0.2*M (= M/5, glut ceiling) -> glut event

# Player attributes copied straight from the observed state (as montecarlo._new_game).
_PLAYER_ATTRS = ("city", "cash", "bank", "debt", "rank", "no_scent")


def _normalize_inventory(inv):
    """Accept either the simulator's `{drug: {qty, avg_buy}}` mapping or the live
    state's `[{"drug":..,"qty":..}, ...]` list, and return the mapping form."""
    if inv is None:
        return {}
    if isinstance(inv, dict):
        out = {}
        for drug, v in inv.items():
            if isinstance(v, dict):
                out[drug] = {"qty": int(v.get("qty", 0)),
                             "avg_buy": int(v.get("avg_buy", 0))}
            else:
                out[drug] = {"qty": int(v), "avg_buy": 0}
        return out
    out = {}
    for item in inv:
        out[item["drug"]] = {"qty": int(item.get("qty", 0)),
                             "avg_buy": int(item.get("avg_buy", 0))}
    return out


def classify_price(price, M):
    """Read an observed price as one of 'shortage' / 'glut' / 'normal' relative to
    the market mean `M`. See the module docstring for the thresholds and why the
    band edges are the exact boundaries of ordinary price dynamics."""
    if price > EVENT_HIGH_FACTOR * M:
        return "shortage"
    if price < EVENT_LOW_FACTOR * M:
        return "glut"
    return "normal"


def sample_target(price, M, rng):
    """Sample a hidden target consistent with an observed `price` (§2-§3).

    Normal price -> a target within ±M//10 of the price, clamped to the normal
    band `[M//2, M//2 + M - 1]` (drift keeps price near target). Spike/crash ->
    a fresh normal draw (`prices.new_target`), because an event overwrote the
    price without touching the target (§4b), so decay is drift back toward normal.
    Returns `(target, event)` with `event` in {+1, -1, 0}.
    """
    kind = classify_price(price, M)
    if kind == "shortage":
        return prices.new_target(M, rng), +1
    if kind == "glut":
        return prices.new_target(M, rng), -1
    lo, hi = M // 2, M // 2 + M - 1
    half = max(1, M // 10)
    target = price + (rng.rand_mod(2 * half + 1) - half)
    target = max(lo, min(hi, target))
    return target, 0


def _direction(direction) -> int:
    """Rumor/scheduled direction -> event sign: scarce/shortage/+1, abundant/glut/-1."""
    if isinstance(direction, int):
        return 1 if direction > 0 else -1
    d = str(direction).lower()
    return +1 if d in ("scarce", "shortage", "up", "+1") else -1


def sample_world(state: dict, seed: int) -> Game:
    """Build a `Game(seed)` whose day-0 world MATCHES the observed OCR `state`.

    `state` schema (all keys optional except this is only useful with `prices`):
        {
          "city"/"current_city": str,          # player's current city
          "cash","bank","debt","rank","no_scent": player attrs (as _new_game),
          "inventory": {drug:{qty,avg_buy}} | [{"drug","qty"}],
          "prices": {city: {drug: price}},     # observed sell/market prices
          "stock":  {city: {drug: quantity}},  # observed quantities (optional)
          "rumors": [{"drug","city","direction"}],  # scarce/abundant for TOMORROW
        }

    Observed markets are overwritten (price always, quantity when in `stock`) with
    a hidden target sampled per `sample_target`; unobserved markets keep the seeded
    prior. Active rumors are injected into `game._scheduled` for the next step_day.
    Returns the ready-to-step `Game`.
    """
    game = Game(seed)
    player = game.player

    # Player attributes (city may arrive as "current_city").
    city = state.get("city", state.get("current_city"))
    if city is not None:
        player.city = city
    for attr in _PLAYER_ATTRS:
        if attr in state and state[attr] is not None:
            setattr(player, attr, state[attr])
    if "inventory" in state:
        player.inventory = _normalize_inventory(state["inventory"])

    # Condition the day-0 markets on the observation.
    observed_prices = state.get("prices") or {}
    observed_stock = state.get("stock") or {}
    for obs_city, drug_prices in observed_prices.items():
        if obs_city not in game.world:
            continue                              # not a modeled city -> skip
        stock_row = observed_stock.get(obs_city, {})
        for drug, price in drug_prices.items():
            market = game.world[obs_city].get(drug)
            if market is None or price is None:
                continue
            M = normal_mean(drug, obs_city)
            price = int(price)
            target, event = sample_target(price, M, game.rng)
            market.price = price
            market.target = target
            market.event = event
            qty = stock_row.get(drug)
            if qty is not None:
                market.quantity = int(qty)

    # Inject active rumors so the NEXT step_day honors the 70/20/10 (§4a).
    for rumor in state.get("rumors") or []:
        r_city = rumor.get("city")
        r_drug = rumor.get("drug")
        if r_city in game.world and r_drug in game.world.get(r_city, {}):
            game._scheduled[(r_city, r_drug)] = _direction(rumor.get("direction"))

    return game
