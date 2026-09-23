from dl2model.prng import MsvcRand


def test_known_sequence():
    r = MsvcRand(1)
    assert [r.rand() for _ in range(5)] == [41, 18467, 6334, 26500, 19169]


def test_first_two_hand_verified():
    # srand(1): state = 1*214013 + 2531011 = 2745024; (2745024 >> 16) & 0x7fff = 41
    # next: state = 2745024*214013 + 2531011 = 587526212739 mod 2**32 = 1210902787
    #       (1210902787 >> 16) & 0x7fff = 18467
    r = MsvcRand(1)
    assert r.rand() == 41
    assert r.rand() == 18467


def test_range():
    r = MsvcRand(12345)
    assert all(0 <= r.rand() < 32768 for _ in range(10000))


def test_srand_resets():
    r = MsvcRand(1)
    first = [r.rand() for _ in range(3)]
    r.srand(1)
    assert [r.rand() for _ in range(3)] == first


def test_rand_mod_zero():
    assert MsvcRand(1).rand_mod(0) == 0


def test_rand_mod_negative():
    assert MsvcRand(1).rand_mod(-5) == 0


def test_rand_mod_in_range():
    r = MsvcRand(999)
    assert all(0 <= r.rand_mod(7) < 7 for _ in range(1000))


def test_default_seed_is_one():
    assert MsvcRand().rand() == MsvcRand(1).rand() == 41
