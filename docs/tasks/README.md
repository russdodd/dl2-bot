# DL2 exact-model rebuild — parallel task pack

We reverse-engineered the Drug Lord 2.2 executable and now have the game's
*exact* formulas. This pack turns them into `dl2model/`, an isolated, screen-free
Python package that the live bot and an offline simulator both build on.

Each task below is **one module** + **one test file**, owned by one Claude
session in its own git worktree. Nobody edits a shared file, so the branches
merge cleanly. `dl2model/constants.py` is already COMPLETE — every task imports
its data from there and must not redefine it.

## The rules (every task)
- **Only touch the two files your task names** (`dl2model/<mod>.py` and
  `tests/test_<mod>.py`). Do not edit `constants.py`, other modules, or the live
  bot (`optimizer.py`, `dl2sweep.py`, `parse.py`, …). Wiring the package into the
  live bot is a separate, later step done by the repo owner — not your job.
- **Pure logic, no screen, no I/O.** Functions take data as arguments. No
  ScreenCaptureKit, no clicks, no reading the game.
- **Fill in the stubs exactly** as the docstrings + this doc specify. Keep the
  documented signatures (other modules import them).
- **Write real tests** in `tests/test_<mod>.py` and make them pass:
  `python3 -m pytest tests/test_<mod>.py -q` from the repo root. Include the
  numeric checks listed in your task doc.
- **Integer math:** the game is C. Use `constants.trunc_div` for any C divide
  whose numerator can be negative; plain `//` is fine for non-negative operands.
- Commit only your two files. **Do not** commit `decay_model.json`,
  `ship_rates.json`, `audit_log.jsonl`, or `__pycache__`.

## Dependency waves
- **Wave 1 — no dependencies, fully parallel** (code straight against
  `constants.py`): **p01 prices, p02 stock, p03 shipping, p04 risk, p05 rumors,
  p06 finance, p07 prng.** Launch all seven at once.
- **Wave 2 — depend on wave-1 modules' public APIs**: **p08 simulator** (needs
  all of wave 1), **p09 strategy** and **p10 montecarlo** (need p08). You may
  start these in parallel and code to the documented contracts, but your
  end-to-end tests only go green after the wave-1 branches merge to `main` and
  you rebase. Until then, unit-test your logic with small fakes/monkeypatched
  primitives, and mark integration tests `@pytest.mark.xfail(reason="needs wave-1")`.

## Git workflow (your worktree is already set up)
Your session starts in a worktree already checked out on your branch
(`task/pNN-<mod>`). Confirm with `git status` and `git branch --show-current`.
When your tests pass:
```
git add dl2model/<mod>.py tests/test_<mod>.py
git commit -m "<mod>: implement exact DL2 <thing>"
git push -u origin task/pNN-<mod>
```
Then open a PR against `main` on GitHub (repo: `russdodd/dl2-bot`). `gh` isn't
installed here, so open it in the web UI. Do not merge — the owner merges.

## Source of truth
The formulas in your task doc were transcribed from the executable's
disassembly. If a docstring in the stub and this pack ever disagree, this pack
wins — but they shouldn't. If something is genuinely undecoded (e.g. combat
loss resolution) it is marked **UNKNOWN/TODO**: expose it as a parameter, do not
invent a number.
