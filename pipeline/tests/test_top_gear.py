from pipeline.export import top_gear as tg


def test_curve_falls_and_reads_once_an_hour():
    # 10 hours: 300 skates over 18, 50 over 20, 5 over 22, ten fastest from 22.9 down to 22.0
    p = {"n18": 300, "n20": 50, "n22": 5, "top": [22.9 - 0.1 * k for k in range(10)]}
    c = tg.curve(p, 10.0)
    rates = [r for _, r in c]
    assert rates == sorted(rates, reverse=True)
    assert c[0] == (18.0, 30.0)
    hr = tg.once_an_hour(c)
    assert 20.0 < hr < 22.0  # 5 per 60 at 20 mph, 0.5 per 60 at 22 mph
    assert abs(tg.at(c, hr) - 1.0) < 1e-6


def test_slow_skater_never_reaches_once_an_hour_at_18():
    assert tg.once_an_hour(tg.curve({"n18": 3, "n20": 0, "n22": 0, "top": [19.0, 18.5]}, 10.0)) is None
