"""The self-check (pipeline/quality): its pieces on small made-up inputs."""
import json

from pipeline.export import textcheck, variety
from pipeline.quality import hindsight, run, text


def errors(s):
    return [f["why"] for f in textcheck.check_text(s) if f["level"] == "error"]


def test_reported_injuries_pass_and_guesses_do_not():
    assert errors("Two days later, he was placed on injured reserve with a lower-body injury.") == []
    assert errors("The next day, he was listed as day to day with an upper-body injury.") == []
    assert errors("He was hurt in the second period.")
    assert errors("Matthews left with an apparent knee injury.")


def test_contrast_sentence_with_an_and_chain_is_caught():
    assert errors("Even though Florida will be without Barkov, the Panthers have been out-chancing opponents lately "
                  "and have home-ice advantage, and Winnipeg will be without Morrissey.")
    assert errors("Even though Florida will be without Barkov, the Panthers have been out-chancing opponents lately "
                  "and have home-ice advantage.") == []


def test_texts_walks_reader_facing_fields_only():
    page = {"game_id": 1, "claims": [{"head": "Bring a pillow tonight", "check": {"metric": "pace and more words"}}],
            "story": {"paras": ["One two three four."], "items": [{"tag": "Out", "text": "The Ants are without Star."}]}}
    assert list(text.texts(page)) == ["Bring a pillow tonight", "One two three four.", "The Ants are without Star."]


def test_said_out_finds_dressed_players_but_not_the_goalie_expected_to_start():
    items = [{"tag": "Out", "text": "The Ants are newly without Jöhn Smith. Akira Schmid is expected to start."},
             {"tag": "Missing", "text": "Bob Jones has missed their last two games."}]
    dressed = {"john smith": 1, "akira schmid": 2, "bob jones": 3}
    assert [(n, p) for n, p, _ in hindsight.said_out(items, dressed)] == [("john smith", 1)]


def test_picks_stay_away_from_recent_wording_and_change_nothing_outside_a_preview():
    pool = ["A", "B", "C"]
    plain = variety.pick(pool, 7, "edge")
    with variety.game({variety.crc(plain)}) as picked:
        other = variety.pick(pool, 7, "edge")
    assert other != plain and picked == [variety.crc(other)]
    assert variety.pick(pool, 7, "edge") == plain
    with variety.game({variety.crc(p) for p in pool}):  # everything used lately: fall back to the whole pool
        assert variety.pick(pool, 7, "edge") == plain


def test_recent_reads_each_teams_last_previews(tmp_path, monkeypatch):
    monkeypatch.setattr(variety, "_seen", None)
    for i, (a, h, start) in enumerate([("AAA", "BBB", "2026-10-01"), ("CCC", "AAA", "2026-10-03"), ("AAA", "DDD", "2026-10-09")]):
        (tmp_path / f"20260200{i}.json").write_text(json.dumps({"game_id": i + 1, "start": start, "away": a, "home": h, "picks": [i + 1]}))
    assert variety.recent(tmp_path, ("AAA",), "2026-10-05") == {1, 2}
    assert variety.recent(tmp_path, ("AAA",), "2026-10-05", n=1) == {2}
    assert variety.recent(tmp_path, ("DDD",), "2026-10-05") == set()


def test_alarm_is_empty_when_all_is_well_and_lists_problems_otherwise():
    ok = {"checks": {"text": {"level": "ok", "problems": []}, "outside": {"level": "skipped"}}}
    assert run.alarm(ok) == ""
    bad = {"checks": {"official": {"level": "problem", "problems": [{"what": "CAR: our totals differ"}], "refetch": [5]}}}
    note = run.alarm(bad)
    assert "CAR: our totals differ" in note and "fetched again" in note


def test_expected_lineup_reads_every_projected_player():
    from pipeline.export import stories
    nhl = {"teams": {"AAA": {"ids": [1, 2, 3]}, "BBB": {"ids": [4]}}}
    assert stories.expected_lineup(nhl) == {1, 2, 3, 4} and stories.expected_lineup(None) == set()
