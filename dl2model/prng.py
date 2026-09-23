"""MSVC rand() reimplementation — seedable, deterministic.

Recurrence (see constants): state = state*RAND_MULT + RAND_INC (mod 2^32),
returned value = (state >> 16) & 0x7fff, i.e. an int in [0, 32767].
"""
from . import constants as C


class MsvcRand:
    """Bit-exact Microsoft Visual C++ rand()/srand()."""

    def __init__(self, seed: int = 1):
        self.srand(seed)

    def srand(self, seed: int) -> None:
        self.state = seed & 0xFFFFFFFF

    def rand(self) -> int:
        """Next value in [0, 32767]."""
        self.state = (self.state * C.RAND_MULT + C.RAND_INC) % C.RAND_MOD
        return (self.state >> 16) & 0x7FFF

    def rand_mod(self, n: int) -> int:
        """rand() % n (the game's `rand()%n` idiom). n<=0 -> 0."""
        if n <= 0:
            return 0
        return self.rand() % n
