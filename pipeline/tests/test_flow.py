from pipeline.export.flow import merge, power_plays


def test_shifts_join_within_a_period_but_not_across():
    spans = [(1, 0, 40), (1, 40, 70), (1, 100, 130), (1, 1150, 1200), (2, 1200, 1240)]
    assert merge(spans) == [0, 70, 100, 130, 1150, 1200, 1200, 1240]


def test_power_plays_need_both_goalies_and_a_real_edge():
    codes = [(0, "1551"), (100, "1541"), (220, "1551"), (300, "1451"), (305, "1551"), (400, "0651"), (500, "1551")]
    # 100-220: home 5 on 4 away -> away team on the power play (side 0). 300-305 is a blip; 400 is an empty net.
    assert power_plays(codes, 3600) == [[0, 100, 220]]
    assert power_plays([(0, "1551"), (3500, "1451")], 3600) == [[1, 3500, 3600]]
