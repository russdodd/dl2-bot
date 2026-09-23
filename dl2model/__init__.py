"""dl2model — an exact, decompiled reimplementation of the Drug Lord 2.2 market.

Every constant and formula in this package was recovered from the DL2.2
executable (see docs/tasks/*.md for the source notes). It is deliberately
side-effect free and screen-free: pure functions and dataclasses that the live
bot (optimizer/decay/shipping) and the offline simulator both build on.

Modules:
    constants   complete data tables + shared integer helpers (COMPLETE)
    prng        MSVC rand() reimplementation (seedable)
    prices      price/target momentum + spike-decay expectation
    stock       availability / stock model + listing roll
    shipping    exact shipping cost + delivery/failure EV
    risk        No-Scent carry-detection + police-encounter model
    rumors      forward-signal rumor model (70.2/10.2/19.6)
    finance     loans, rank/capacity, overhead, final score, game horizon
    simulator   full seedable day-stepping game engine (composes the above)
    strategy    multi-day plan search
    montecarlo  EV + variance of a decision over many simulated futures
"""
