"""Clubs that dress 11 forwards and 7 defensemen: lines, depth and NHL.com's projected lineups."""
import polars as pl

from pipeline.ingest import nhl_previews
from pipeline.metrics import line_order, team_style, units

# Tampa Bay at home, 2026-10-15, by 5-on-5 time together (seconds): three separate trios, then Guentzel and Point with
# Kucherov, who also plays on the Cirelli line. Seven defensemen: three pairs and Lilleberg rotating in.
F = [("11-12-13", 590), ("14-15-16", 520), ("17-18-19", 440), ("17-20-21", 370), ("12-20-21", 70), ("14-20-21", 40)]
D = [("31-32", 860), ("33-34", 600), ("35-36", 590), ("35-37", 240), ("33-37", 215)]
POS = {**{i: "C" for i in range(11, 22)}, **{i: "D" for i in range(31, 38)}}


def _units(gid=1, tid=14, day="2026-10-15"):
    rows = [{"game_id": gid, "team_id": tid, "kind": k, "unit": u, "sec": float(sec), "cf": 0.0, "ca": 0.0, "xgf": sec / 3600 * 2.5, "xga": sec / 3600 * 2.5,
             "gf": 0.0, "ga": 0.0, "oz_fo": 1.0, "dz_fo": 1.0, "date": day} for k, groups in (("F", F), ("D", D)) for u, sec in groups]
    return pl.DataFrame(rows)


def _tables(tmp_path, monkeypatch, n_fwd=11):
    d = tmp_path / "2026"
    d.mkdir()
    _units().write_parquet(d / "game_units.parquet")
    fwd = list(range(11, 11 + n_fwd))
    dmen = list(range(31, 31 + 18 - n_fwd))
    pl.DataFrame([{"game_id": 1, "team_id": 14, "player_id": p, "pos": POS.get(p, "C")} for p in fwd + dmen]).write_parquet(d / "players.parquet")
    monkeypatch.setattr(units, "TABLES", tmp_path)
    monkeypatch.setattr(line_order, "TABLES", tmp_path)
    monkeypatch.setattr(line_order, "RAW", tmp_path / "raw")
    for f in (units.shapes, units._player_games, units._team_games):
        f.cache_clear()


def test_eleven_forwards_fill_the_fourth_line_with_the_forward_who_doubles_up(tmp_path, monkeypatch):
    _tables(tmp_path, monkeypatch)
    gu = pl.read_parquet(tmp_path / "2026" / "game_units.parquet").filter(pl.col("kind") == "F")
    # Without sharing, no fourth line exists and Guentzel (20) and Point (21) are left out.
    assert [u["unit"] for u in units._pick(gu, 4)] == ["11-12-13", "14-15-16", "17-18-19"]
    four = units._pick(gu, 4, share=True)
    assert [u["unit"] for u in four] == ["11-12-13", "14-15-16", "17-18-19", "17-20-21"]
    assert four[-1]["shared"] == 17
    assert units.eleven(2026, 1, 14)
    lines = units.game_lines(2026, pl.read_parquet(tmp_path / "2026" / "game_units.parquet"), 1, 14)
    assert sum(k == "F" for k, _, _ in lines) == 4 and sum(k == "D" for k, _, _ in lines) == 3
    usual = units.usual(2026)[14]
    assert usual["shape"] == {"f": 11, "d": 7, "eleven": 1, "of": 1}
    assert len([u for u in usual["units"] if u["label"][0] == "L"]) == 4
    assert usual["seventh"]["player"] == 37 and set(usual["seventh"]["with"]) == {35, 33}


def test_twelve_forwards_keep_four_separate_lines(tmp_path, monkeypatch):
    _tables(tmp_path, monkeypatch, n_fwd=12)
    assert not units.eleven(2026, 1, 14)
    assert units.usual(2026)[14]["seventh"] is None


def test_a_forward_on_two_lines_counts_half_for_each():
    sets = {"L1": {17, 20, 21}, "L2": {17, 18, 19}, "L3": {11, 12, 13}}
    assert units.label_of(sets, [17, 20, 21], "L") == "L1"
    assert units.label_of(sets, [17, 18, 19], "L") == "L2"
    assert units.label_of(sets, [17, 20, 18], "L") is None  # one regular from each line plus the double-shifter
    assert units.label_of(sets, [11, 12, 20], "L") == "L3"


def test_depth_leaves_out_games_without_twelve_forwards(tmp_path, monkeypatch):
    d = tmp_path / "2026"
    d.mkdir()
    pl.DataFrame({"game_id": [1, 2], "team_id": [14, 14], "bottom6_sec": [900.0, 1300.0], "fwd_sec": [2700.0, 2900.0], "fwds": [11, 12]}).write_parquet(d / "depth.parquet")
    monkeypatch.setattr(team_style, "TABLES", tmp_path)
    t = team_style._depth(2026).sort("game_id")
    assert t["bottom6_sec"].to_list() == [0.0, 1300.0] and t["fwd_sec"].to_list() == [0.0, 2900.0]
    assert units.plays_eleven(6, 7) and not units.plays_eleven(2, 7) and not units.plays_eleven(1, 1)


BODY = """## **KRAKEN (5-2-0) at LIGHTNING (4-2-0)**

**Kraken projected lineup**

Jared McCann -- Matty Beniers -- Mackie Samoskevich

Vince Dunn -- Adam Larsson

Joey Daccord

***Scratched:** Curtis Douglas*

**Lightning projected lineup**

Brandon Hagel -- Anthony Cirelli -- Nikita Kucherov

Jake Guentzel -- Brayden Point -- Conor Geekie

Jeffrey Viel -- Dominic James

J.J. Moser -- John Carlson

Victor Hedman

Andrei Vasilevskiy

***Scratched:** Gage Goncalves*

***Injured:** Yanni Gourde (hip)*

**Status report**

James will make his season debut after missing the first six games; he will take the place of Goncalves, a forward. ... Nazar returns after serving a two-game suspension. ... Girgensons is a game-time decision. ... The Lighting are expected to dress 11 forwards and seven defensemen. ... The Kraken will dress the same lineup they used on Tuesday. ... McCann was moved to the first line with Beniers.
"""
PEOPLE = {"McCann": ("SEA", "C"), "Beniers": ("SEA", "C"), "Samoskevich": ("SEA", "R"), "Dunn": ("SEA", "D"), "Larsson": ("SEA", "D"),
          "Daccord": ("SEA", "G"), "Douglas": ("SEA", "C"), "Hagel": ("TBL", "L"), "Cirelli": ("TBL", "C"), "Kucherov": ("TBL", "R"),
          "Guentzel": ("TBL", "C"), "Point": ("TBL", "C"), "Geekie": ("TBL", "C"), "Viel": ("TBL", "L"), "James": ("TBL", "R"),
          "Moser": ("TBL", "D"), "Carlson": ("TBL", "D"), "Hedman": ("TBL", "D"), "Vasilevskiy": ("TBL", "G"), "Goncalves": ("TBL", "C"),
          "Gourde": ("TBL", "C"), "Girgensons": ("TBL", "C"), "Nazar": ("TBL", "C")}
FIRST = {"McCann": "Jared", "Beniers": "Matty", "Samoskevich": "Mackie", "Dunn": "Vince", "Larsson": "Adam", "Daccord": "Joey", "Douglas": "Curtis",
         "Hagel": "Brandon", "Cirelli": "Anthony", "Kucherov": "Nikita", "Guentzel": "Jake", "Point": "Brayden", "Geekie": "Conor", "Viel": "Jeffrey",
         "James": "Dominic", "Moser": "J.J.", "Carlson": "John", "Hedman": "Victor", "Vasilevskiy": "Andrei", "Goncalves": "Gage", "Gourde": "Yanni",
         "Girgensons": "Zemgus", "Nazar": "Frank"}
IDS = {last: i for i, last in enumerate(PEOPLE, 1)}


def _known():
    out = {}
    for last, (team, pos) in PEOPLE.items():
        full = nhl_previews.fold(f"{FIRST[last]} {last}")
        out.setdefault(team, {})[full] = (IDS[last], pos)
        out.setdefault("*", {})[full] = (IDS[last], pos)
    return out


def test_reads_an_eleven_forward_projection():
    got = nhl_previews.lineups_from(BODY, "SEA", "TBL", _known())
    tb = got["TBL"]
    assert tb["lines"] == [[IDS["Hagel"], IDS["Cirelli"], IDS["Kucherov"]], [IDS["Guentzel"], IDS["Point"], IDS["Geekie"]], [IDS["Viel"], IDS["James"]]]
    assert tb["pairs"] == [[IDS["Moser"], IDS["Carlson"]]] and tb["extra_d"] == [IDS["Hedman"]] and tb["goalies"] == [IDS["Vasilevskiy"]]
    assert tb["scratched"] == [IDS["Goncalves"]] and tb["injured"] == [IDS["Gourde"]] and tb["missed"] == 0
    assert nhl_previews.shape(tb) == (8, 3)  # the fixture is cut short; a full one reads (11, 7)
    assert got["SEA"]["scratched"] == [IDS["Douglas"]]


def test_status_facts_name_the_facts_and_nothing_else():
    text = nhl_previews.status_text(BODY)
    assert text.startswith("James will make")
    people = {IDS[last]: (team, FIRST[last], last) for last, (team, pos) in PEOPLE.items() if pos != "G"}
    aliases = {"SEA": ["Seattle", "Kraken"], "TBL": ["Tampa Bay", "Lightning"]}
    facts = {(f["kind"], f["team"]): f for f in nhl_previews.status_facts(text, aliases, people)}
    assert facts[("suspension", "TBL")]["state"] == "returns" and facts[("suspension", "TBL")]["games"] == 2
    assert facts[("gtd", "TBL")]["players"] == [IDS["Girgensons"]]
    assert facts[("eleven", None)]["sure"]  # "The Lighting": no club named, so the projection decides
    assert ("same", "SEA") in facts
    assert facts[("lines", "SEA")]["players"] == [IDS["McCann"], IDS["Beniers"]]
    assert all(k != "healthy" for k, _ in facts)


def test_a_defenseman_written_into_a_forward_line_is_the_seventh():
    # NHL.com's WPG at CAR preview, 2026-11-01: Bauer, a defenseman, on the fourth line, and Winnipeg dressed 11 and 7
    row = lambda *names: [[(IDS[n], PEOPLE[n][1])] for n in names]
    got = nhl_previews._place([row("Hagel", "Cirelli", "Kucherov"), row("Viel", "James", "Hedman"), row("Moser", "Carlson")], [], [])
    assert got["lines"] == [[IDS["Hagel"], IDS["Cirelli"], IDS["Kucherov"]], [IDS["Viel"], IDS["James"]]]
    assert got["pairs"] == [[IDS["Moser"], IDS["Carlson"]]] and got["extra_d"] == [IDS["Hedman"]]


def test_last_games_eleven_is_not_tonights():
    aliases = {"DET": ["Detroit", "Red Wings"], "NYI": ["NY Islanders", "Islanders"], "TBL": ["Tampa Bay", "Lightning"],
               "NSH": ["Nashville", "Predators"], "CBJ": ["Columbus", "Blue Jackets"]}
    kinds = lambda text: [(f["kind"], f["team"], f.get("sure")) for f in nhl_previews.status_facts(
        text, {t: a for t, a in aliases.items() if t in ("TBL", "NSH") or "Each" not in text}, {})]
    # NHL.com, 2026-10: the opposite of 11 and 7 tonight
    assert kinds("The Red Wings are expected to dress a conventional lineup after using 11 forwards and seven defensemen in a "
                 "4-3 shootout win against the San Jose Sharks on Saturday.") == [("twelve", "DET", None)]
    assert kinds("The Red Wings held an optional morning skate, and likely will use 12 forwards and six defensemen, after going "
                 "with 11 forwards and seven defensemen in a 4-1 loss at the Calgary Flames on Saturday") == [("twelve", "DET", None)]
    assert kinds("Each team dressed 11 forwards and seven defensemen in its last game Thursday and could do so again; the Lightning "
                 "defeated the Winnipeg Jets 6-4 and the Predators lost to the Washington Capitals 4-3.") == [("eleven", "TBL", False), ("eleven", "NSH", False)]
    assert kinds("Columbus likely will dress 11 forwards and seven defensemen if Provorov is in the lineup.") == [("eleven", "CBJ", False)]
    assert kinds("The Red Wings are expected to dress 11 forwards and seven defensemen, as they have done for most of the season.") == [("eleven", "DET", True)]


def test_preview_lineup_items_follow_what_nhl_com_said_and_last_game():
    from pipeline.export import stories
    W = stories.Words({"WPG": "Winnipeg", "CAR": "Carolina"})
    p = lambda *ids: [{"id": i, "name": f"P. {i}"} for i in ids]
    proj = {"lines": [p(1, 2, 3)] * 4, "pairs": [p(4, 5)] * 3, "extra_d": [], "eleven": False}
    lines = {"WPG": {"shape": {"f": 11, "d": 7, "eleven": 2, "of": 10}, "units": []}, "CAR": {"shape": {"f": 12, "d": 6, "eleven": 0, "of": 10}, "units": []}}
    run = lambda facts: [i["text"] for i in stories._game_day({"teams": {"WPG": proj, "CAR": proj}, "facts": facts}, "WPG", "CAR", "2026-11-01",
                                                               W, {}, lines, {}, 2026)[0] if i["tag"] == "Lineup"]
    said = run([])  # 12 and 6 on NHL.com's lines, 11 and 7 last game: both facts, no call
    assert len(said) == 1 and "back at 12 forwards" in said[0] and "11 and seven last game (and in two of their last ten)" in said[0]
    said = run([{"kind": "twelve", "team": "WPG", "players": []}])
    assert said == ["NHL.com expects the Jets to go back to 12 forwards and six defensemen, after dressing 11 and seven last game."]
