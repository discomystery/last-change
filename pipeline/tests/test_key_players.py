from pipeline.export import key_players as K


def test_hot_needs_a_real_gap():
    assert K.form(15, 10, 50, 82)[0] == "hot"      # 1.5 a game against about 0.6
    assert K.form(7, 10, 50, 82)[0] is None         # a point or so over his pace is nothing


def test_cold_only_for_scorers():
    assert K.form(1, 10, 100, 82)[0] == "cold"     # 1 point where about 11 were usual
    assert K.form(0, 10, 10, 82)[0] is None         # a depth player's quiet ten games is his usual
