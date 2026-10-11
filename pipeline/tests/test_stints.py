from pipeline.build.stints import Shift, build_stints, clean_shifts, game_seconds, on_ice, strength


def test_game_seconds():
    assert game_seconds(1, "00:00") == 0
    assert game_seconds(2, "05:30") == 1530
    assert game_seconds(4, "04:59") == 3899


def test_stints_split_at_every_change():
    shifts = [Shift(1, True, False, 0, 40), Shift(2, True, False, 40, 90), Shift(9, False, False, 0, 90), Shift(30, True, True, 0, 90)]
    st = build_stints(shifts)
    assert [(s["start"], s["end"]) for s in st] == [(0, 40), (40, 90)]
    assert st[0]["home_skaters"] == [1] and st[1]["home_skaters"] == [2]
    assert st[0]["away_skaters"] == [9] and st[1]["home_goalie"] == 30 and st[0]["away_goalie"] is None


def test_event_attribution_at_a_change():
    off, on = Shift(1, True, False, 0, 40), Shift(2, True, False, 40, 90)
    # A goal at the whistle belongs to the players who were on; the faceoff after it to the new ones.
    assert [s.player_id for s in on_ice([off, on], 40, faceoff=False)] == [1]
    assert [s.player_id for s in on_ice([off, on], 40, faceoff=True)] == [2]


def test_clean_shifts_drops_goal_markers_duplicates_and_shootout():
    base = {"teamId": 12, "period": 1, "startTime": "00:00", "endTime": "00:30", "typeCode": 517, "playerId": 5}
    rows = [base, dict(base), {**base, "typeCode": 505}, {**base, "period": 5}, {**base, "endTime": "00:00"}]
    assert len(clean_shifts(rows, 12, set(), max_period=4)) == 1


def test_strength_buckets():
    assert strength(5, 5, True, True) == "5v5"
    assert strength(4, 5, True, True) == "4v5"
    assert strength(6, 5, False, True) == "EA"
    assert strength(5, 6, True, False) == "EN"


def test_overlapping_rows_for_one_player_are_merged():
    base = {"teamId": 12, "period": 1, "typeCode": 517, "playerId": 30}
    rows = [{**base, "startTime": "00:00", "endTime": "10:00"}, {**base, "startTime": "05:00", "endTime": "20:00"}, {**base, "period": 2, "startTime": "00:00", "endTime": "20:00"}]
    out = clean_shifts(rows, 12, {30}, max_period=4)
    assert [(s.start, s.end) for s in out] == [(0, 1200), (1200, 2400)]


def test_html_report_parsing():
    from pipeline.ingest.html_shifts import parse_report
    html = '<td class="playerHeading + border" colspan="8">15 SISSONS, COLTON</td><tr><td align="center" class="x">1</td>\n<td align="center" class="x">1</td>\n<td align="center" class="x">0:00 / 20:00</td>\n<td align="center" class="x">0:35 / 19:25</td>' \
           '<tr><td class="x">2</td><td class="x">OT</td><td class="x">2:47 / 2:13</td><td class="x">3:34 / 1:26</td>'
    rows = parse_report(html, 18, {15: 999})
    assert [(r["playerId"], r["period"], r["startTime"], r["endTime"]) for r in rows] == [(999, 1, "00:00", "00:35"), (999, 4, "02:47", "03:34")]


def test_penalty_shot_codes():
    from pipeline.build.stints import penalty_shot
    assert penalty_shot("1010") and penalty_shot("0101")  # one shooter against one goalie, home or away shooting
    assert not penalty_shot("1551") and not penalty_shot("0651") and not penalty_shot(None)


def _pbp(end_clock: str, period: int = 3) -> dict:
    return {"homeTeam": {"id": 1}, "awayTeam": {"id": 2},
            "plays": [{"periodDescriptor": {"number": period, "periodType": "REG"}, "timeInPeriod": end_clock}]}


def _shift(team: int, period: int, start: str, end: str) -> dict:
    return {"typeCode": 517, "teamId": team, "period": period, "startTime": start, "endTime": end}


def test_unfinished_shift_chart_is_caught():
    from pipeline.ingest.nhl import shifts_cover

    full = [_shift(1, 3, "00:00", "20:00"), _shift(2, 3, "00:00", "20:00")]
    assert shifts_cover(full, _pbp("20:00"))
    # CAR at CHI, 2026-10-10: the chart saved after the game stopped at 13:05 of the third.
    assert not shifts_cover([_shift(1, 3, "00:00", "13:05"), _shift(2, 3, "00:00", "20:00")], _pbp("20:00"))
    assert not shifts_cover([], _pbp("20:00"))


def test_shift_gap_never_reads_as_empty_net():
    from pipeline.build.games import _goalie_in_net

    # Situation code says both goalies are in, the shift chart has none: the goalie on the shot fills the gap.
    assert _goalie_in_net("1551", True, None, 30, 31) == 31
    assert _goalie_in_net("1551", False, None, 40, None) == 40
    # A real pulled goalie (home net empty, six home skaters) stays empty.
    assert _goalie_in_net("1560", True, None, 30, None) is None
    # The shift chart wins when it names a goalie.
    assert _goalie_in_net("1551", True, 33, 30, 31) == 33
