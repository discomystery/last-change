"""Best against best: only two of the league's best teams by blended standing (Sora, 2026-10-16)."""
import polars as pl

from pipeline.export import storylines as sl


def _games(rows):
    return pl.DataFrame(rows, schema=["game_id", "season", "date", "home", "away", "home_score", "away_score", "last_period"], orient="row")


def _season(season, date, results):
    """results: {team: (wins, losses)} against a pool of filler clubs."""
    rows, gid = [], season * 100000
    for t, (w, l) in results.items():
        for k in range(w + l):
            gid += 1
            rows.append((gid, season, date, t, f"F{k % 8}", 3 if k < w else 1, 2, "REG"))
    return rows


def test_summit(monkeypatch):
    monkeypatch.setattr(sl, "_playoff_teams", lambda season: {"AAA", "BBB", "PHI"})
    last = _season(2025, "2026-01-01", {"AAA": (55, 27), "BBB": (52, 30), "CCC": (50, 32), "PHI": (50, 32)})
    now = _season(2026, "2026-10-10", {"AAA": (7, 2), "BBB": (6, 3), "CCC": (7, 2), "PHI": (1, 8)})
    G = _games(last + now)
    assert sl.summit(G, "AAA", "BBB", 2026, "2026-10-16") == "best"
    assert sl.summit(G, "AAA", "PHI", 2026, "2026-10-16") is None  # last spring's run, but falling apart now
    # CCC missed the playoffs but is winning this season, so it counts once the record says so
    assert sl.summit(G, "AAA", "CCC", 2026, "2026-10-16") == "best"
    assert sl.summit(G, "AAA", "CCC", 2026, "2026-10-05") is None  # before any games: last spring's playoffs only


def test_good_tier(monkeypatch):
    """A .580 club near the top makes "two good teams", never "best against best"."""
    monkeypatch.setattr(sl, "_playoff_teams", lambda season: {"AAA", "DDD"})
    last = _season(2025, "2026-01-01", {"AAA": (55, 27), "DDD": (48, 34)})
    now = _season(2026, "2026-10-10", {"AAA": (8, 2), "DDD": (29, 21)})
    G = _games(last + now)
    assert sl.summit(G, "AAA", "DDD", 2026, "2026-10-16") == "good"


def test_a_deep_run_lifts_last_season(monkeypatch):
    """Two clubs with the same records: the one that won three series last spring stands higher."""
    monkeypatch.setattr(sl, "_playoff_teams", lambda season: {"AAA", "BBB"})
    monkeypatch.setattr(sl, "series_won", lambda season: {"AAA": 3})
    last = _season(2025, "2026-01-01", {"AAA": (50, 32), "BBB": (50, 32)})
    now = _season(2026, "2026-10-10", {"AAA": (5, 2), "BBB": (5, 2)})
    st = sl.standing(_games(last + now), 2026, "2026-10-16")
    assert st["AAA"]["blend"] - st["BBB"]["blend"] > 0.03
