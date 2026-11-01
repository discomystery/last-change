from pipeline.export import finishing


def test_estimate_pulls_toward_average():
    m, sd = finishing.estimate(0, 0)
    assert m == 1 and abs(sd - 1 / finishing.K ** 0.5) < 1e-12
    hot, _ = finishing.estimate(20, 10)  # twice his chances on a small sample
    assert 1 < hot < 1.15
    cold, _ = finishing.estimate(5, 10)
    assert 0.9 < cold < 1
