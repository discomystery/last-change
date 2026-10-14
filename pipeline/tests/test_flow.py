from pipeline.export.flow import merge, power_plays, strength


def test_shifts_join_within_a_period_but_not_across():
    spans = [(1, 0, 40), (1, 40, 70), (1, 100, 130), (1, 1150, 1200), (2, 1200, 1240)]
    assert merge(spans) == [0, 70, 100, 130, 1150, 1200, 1200, 1240]


def test_power_plays_need_both_goalies_and_a_real_edge():
    codes = [(0, "1551"), (100, "1541"), (220, "1551"), (300, "1451"), (305, "1551"), (400, "0651"), (500, "1551")]
    # 100-220: home 5 on 4 away -> away team on the power play (side 0). 300-305 is a blip; 400 is an empty net.
    assert power_plays(codes, 3600) == [[0, 100, 220]]
    assert power_plays([(0, "1551"), (3500, "1451")], 3600) == [[1, 3500, 3600]]


def test_strength():
    # situation code: away goalie, away skaters, home skaters, home goalie
    assert strength("1451", 1) == 1 and strength("1451", 0) == -1
    assert strength("1541", 0) == 1 and strength("1541", 1) == -1
    assert strength("1551", 1) == 0 and strength("1441", 0) == 0
    assert strength("0651", 1) == 0  # away net empty
    assert strength("1010", 0) == 0  # penalty shot


def test_skaters_on_folds_line_change_blips_into_what_came_before():
    import polars as pl
    from pipeline.export.flow import skaters_on
    st = pl.DataFrame({"start": [0, 100, 102, 160, 280, 300], "end": [100, 102, 160, 280, 300, 400],
                       "n_away": [5, 6, 5, 5, 4, 5], "n_home": [5, 5, 5, 4, 5, 5],
                       "away_goalie": [1, 1, 1, 1, 1, None], "home_goalie": [2, 2, 2, 2, 2, 2]})
    # 100-102 has six away skaters on a change: folded in. 300 on: the away net is empty.
    assert skaters_on(st) == [[0, 5, 5, 1, 1], [160, 5, 4, 1, 1], [280, 4, 5, 1, 1], [300, 5, 5, 0, 1]]


def test_shootout_attempts_in_order():
    from pipeline.export.flow import shootout
    so = lambda typ, team, **d: {"sortOrder": len(plays), "periodDescriptor": {"periodType": "SO"}, "typeDescKey": typ,
                                  "details": {"eventOwnerTeamId": team, **d}}
    plays = []
    plays.append(so("shot-on-goal", 9, shootingPlayerId=1, goalieInNetId=50, shotType="snap"))
    plays.append(so("goal", 18, scoringPlayerId=2, goalieInNetId=60, shotType="wrist"))
    plays.append(so("missed-shot", 9, shootingPlayerId=3, goalieInNetId=50))
    plays.append({"sortOrder": 0, "periodDescriptor": {"periodType": "REG"}, "typeDescKey": "goal", "details": {"eventOwnerTeamId": 9}})
    pbp = {"homeTeam": {"id": 9}, "plays": plays}
    assert shootout(pbp) == [[1, 1, 50, 0, "snap"], [0, 2, 60, 1, "wrist"], [1, 3, 50, -1, None]]
