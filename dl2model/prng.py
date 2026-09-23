"""MSVC rand() reimplementation — seedable, deterministic.

Recurrence (see constants): state = state*RAND_MULT + RAND_INC (mod 2^32),
returned value = (state >> 16) & 0x7fff, i.e. an int in [0, 32767].

STUB — implement per docs/tasks/p07-prng.md.
"""
from . import constants as C


class MsvcRand:
    """Bit-exact Microsoft Visual C++ rand()/srand()."""

    def __init__(self, seed: int = 1):
        raise NotImplementedError

    def srand(self, seed: int) -> None:
        raise NotImplementedError

    def rand(self) -> int:
        """Next value in [0, 32767]."""
        raise NotImplementedError

    def rand_mod(self, n: int) -> int:
        """rand() % n (the game's `rand()%n` idiom). n<=0 -> 0."""
        raise NotImplementedError
