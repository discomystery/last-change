import numpy as np

from pipeline.export import trend


def test_change_found_when_play_drops():
    rng = np.random.default_rng(3)
    f = np.r_[rng.gamma(4, 0.6, 50), rng.gamma(4, 0.35, 40)]
    a = rng.gamma(4, 0.5, 90)
    brk = trend.change(f, a, seed=1)
    assert brk is not None and 40 <= brk["k"] <= 60 and brk["after"] < brk["before"]


def test_no_change_on_steady_play():
    rng = np.random.default_rng(4)
    f, a = rng.gamma(4, 0.5, 90), rng.gamma(4, 0.5, 90)
    assert trend.change(f, a, seed=1) is None
