# p07 — prng.py (MSVC rand() reimplementation)

**Files you own:** `dl2model/prng.py`, `tests/test_prng.py`
**Wave 1** (no dependencies). Read `docs/tasks/README.md` first.

## What this is
A bit-exact Microsoft Visual C++ `rand()`/`srand()`. The simulator needs a
seedable PRNG that reproduces the game's exact draw sequence.

## Exact rules (constants: RAND_MULT/INC/MOD)
```
srand(seed): state = seed
rand():      state = (state * 214013 + 2531011) mod 2**32
             return (state >> 16) & 0x7fff        # in 0..32767
rand_mod(n): rand() % n   (n<=0 -> 0)
```

## Verified test vector
`srand(1)` then `rand()` yields **41, 18467, 6334, 26500, 19169, …** (the
canonical MSVCRT sequence). First value 41 and second 18467 are computed by hand
— assert both.

## Tests (`tests/test_prng.py`) — must include
```python
from dl2model.prng import MsvcRand

def test_known_sequence():
    r = MsvcRand(1)
    assert [r.rand() for _ in range(5)] == [41, 18467, 6334, 26500, 19169]

def test_range():
    r = MsvcRand(12345)
    assert all(0 <= r.rand() < 32768 for _ in range(10000))

def test_srand_resets():
    r = MsvcRand(1); first = [r.rand() for _ in range(3)]
    r.srand(1);      assert [r.rand() for _ in range(3)] == first

def test_rand_mod_zero():
    assert MsvcRand(1).rand_mod(0) == 0
```
(If 6334/26500/19169 differ in your implementation, your recurrence is off —
the first two, 41 and 18467, are hand-verified; match them exactly.)

## Done when
`python3 -m pytest tests/test_prng.py -q` green, then commit + push + PR.
