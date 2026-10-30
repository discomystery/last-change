"""Post-game pages: grade each preview call frozen at puck drop, by the rule stored with it, and add the goalies, the
team numbers and a few surprises.

The preview is never rewritten: grading reads the snapshot exactly as it was saved. Calls that cannot be judged (a
power play that barely happened, a line that was hardly on the ice) are marked "na" and left out of the tally.
Reads the site JSON written earlier in `export` (snapshots, fingerprints, lines), so it runs after `previews`.
"""
import gzip
import json
from functools import lru_cache
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import polars as pl

from pipeline.config import CURRENT_SEASON, RAW, REGULAR, SITE_DATA, TABLES
from pipeline.export import grades, pending, previews, wording
from pipeline.ingest import game_stats
from pipeline.export import surprises as surprise_rules
from pipeline.metrics import adjust, goalies, line_order, replays, team_style

# A call on a game whose shift chart the NHL is still filling in: graded on a later run (see pending.py).
PENDING = {"verdict": "pending", "happened": "Checked once the NHL finishes filling in who was on the ice."}

PP_STRENGTHS = {"5v4", "5v3", "4v3"}
MIN_PP_SEC = 60  # less power-play time than this and a power-play call can't be judged
MIN_CLOSE_SEC = 600  # hit rates need some close-game time
MIN_MATCHUP_SEC = 180
EXIT_SEC = 1200  # a skater with no shift in the game's last 20 minutes or more left it (see Season.exits)
NEWS_DAYS = 2  # an injury report the day after the game or the day after that is told on the player who left it (Sora, 2026-10-29)
# most telling first: a placement on injured reserve says more than "day to day", which says more than a bare "out"
NEWS_RANK = {"season": 6, "long": 5, "ir": 4, "week": 3, "day": 2, "out": 1}
BENCHED_SEC = 1800  # a matchup call needs the group on the ice against the opposing lines for three minutes


def clock(sec: float) -> str:
    s = round(sec)
    return f"{s // 60}:{s % 60:02d}"


# Seconds of 5-on-3 and 4-on-3 are worth this many seconds of 5-on-4: the league's expected goals per second at each
# strength over 2023-25 (5-on-4 7.3 per 60, 4-on-3 15.8, 5-on-3 21.6). Counting them this way lets a night with a long
# 5-on-3 be graded against a team's usual 5-on-4 rate without the 5-on-3 inflating it.
MAN_UP = {"5v4": 1.0, "4v3": 2.17, "5v3": 2.97}
MAN_DOWN = {"4v5": 1.0, "3v4": 2.17, "3v5": 2.97}


def man_advantage(tg: pl.DataFrame) -> pl.DataFrame:
    """Per team-game power-play and penalty-kill time (in 5-on-4 seconds) and expected goals, over every man-advantage
    strength, not only 5-on-4 (eye test, 2026-10-13: Ottawa's call at Boston was "too little to judge" after 1:42 at
    5-on-3)."""
    up, down = pl.col("strength").is_in(list(MAN_UP)), pl.col("strength").is_in(list(MAN_DOWN))
    w_up = pl.col("strength").replace_strict(MAN_UP, default=0.0)
    w_down = pl.col("strength").replace_strict(MAN_DOWN, default=0.0)
    return tg.group_by("game_id", "team_id").agg(
        (pl.col("sec") * w_up).sum().alias("pp_sec"), pl.col("sec").filter(up).sum().alias("pp_clock"), pl.col("xgf").filter(up).sum().alias("pp_xgf"),
        (pl.col("sec") * w_down).sum().alias("pk_sec"), pl.col("xga").filter(down).sum().alias("pk_xga"))


def grade(v: float, base: float, floor: float | None, direction: str) -> str:
    """held: at or beyond the team's usual level; partly: beyond the floor (league average or no-matching level)."""
    beyond = (lambda a, b: a >= b) if direction == "above" else (lambda a, b: a <= b)
    if beyond(v, base):
        return "held"
    if floor is not None and beyond(v, floor):
        return "partly"
    return "missed"


class Season:
    """Everything about the finished games of one season that grading needs, loaded once."""

    def __init__(self, season: int):
        self.season = season
        d = TABLES / str(season)
        self.games = {g["game_id"]: g for g in pl.read_parquet(d / "games.parquet").filter(pl.col("game_type") == REGULAR).iter_rows(named=True)}
        self.team_id = {}
        for g in self.games.values():
            self.team_id[(g["game_id"], g["home"])] = g["home_id"]
            self.team_id[(g["game_id"], g["away"])] = g["away_id"]
        self.rows = {(r["game_id"], r["team_id"]): r for r in team_style.per_game(season, adjust.weights()).iter_rows(named=True)}
        tg = pl.read_parquet(d / "team_game.parquet")
        for r in man_advantage(tg).iter_rows(named=True):  # power plays and kills graded on all man-advantage time
            if (row := self.rows.get((r["game_id"], r["team_id"]))) is not None:
                row |= {k: r[k] for k in ("pp_sec", "pp_clock", "pp_xgf", "pk_sec", "pk_xga")}
        self.official = game_stats.lookup(season)
        self.totals = {(r["game_id"], r["team_id"]): r for r in tg.group_by("game_id", "team_id").agg(
            pl.col("sf").sum(), pl.col("hits").sum(),
            pl.col("xgf").filter(pl.col("strength") != "EN").sum().alias("xg"),
            pl.col("cf").filter(pl.col("strength") == "5v5").sum().alias("cf5"),
        ).iter_rows(named=True)}
        disc = pl.read_parquet(d / "discipline.parquet")
        self.short = {(r["game_id"], r["team_id"]): r["times_short"] for r in disc.iter_rows(named=True)}
        self.goals = pl.read_parquet(d / "goals.parquet")
        players = pl.read_parquet(d / "players.parquet")
        self.pos = {(r["game_id"], r["player_id"]): r["pos"] for r in players.select("game_id", "player_id", "pos").iter_rows(named=True)}
        self.name = {r["player_id"]: f"{r['first'][0]}. {r['last']}" for r in players.sort("game_id").iter_rows(named=True)}
        self.name |= wording.number_clashes(season, wording.initials)  # "E. Pettersson (#40)" when two teammates match
        self.full = {r["player_id"]: f"{r['first']} {r['last']}" for r in players.sort("game_id").iter_rows(named=True)}
        self.full |= wording.number_clashes(season, wording.full)
        # lists on the page (the goalie table): "Andersen", "M. Tkachuk" when both brothers play
        self.gshort = wording.short_names(players.iter_rows(named=True), key=lambda r: r["game_id"])
        self.dressed = {(r["game_id"], r["player_id"]) for r in players.select("game_id", "player_id").iter_rows(named=True)}
        self.first_game = {(t, p): g for t, p, g in players.group_by("team_id", "player_id").agg(pl.col("game_id").min()).iter_rows()}
        self._pg, self._av = None, None
        self.stints = pl.read_parquet(d / "stints.parquet")
        self.units = pl.read_parquet(d / "game_units.parquet")
        self.goalies = goalies.per_game(season)
        ev = pl.read_parquet(d / "events.parquet", columns=["game_id", "type", "sec", "team_id", "period", "period_type"])
        # Goals and shots on goal by period, for the score table (shootout left out; their sum equals team_game's sf).
        self.by_period = {}
        for gid, tid, per, ptype, n, gl in (ev.filter(pl.col("type").is_in(["shot-on-goal", "goal"]) & (pl.col("period_type") != "SO"))
                                            .group_by("game_id", "team_id", "period", "period_type")
                                            .agg(pl.len(), (pl.col("type") == "goal").sum()).iter_rows()):
            self.by_period[(gid, tid, per)] = (ptype, int(gl), int(n))
        pen = pl.read_parquet(d / "events.parquet", columns=["game_id", "p1", "pen_type"]) if "pen_type" in pl.read_parquet_schema(d / "events.parquet") else None
        self.ejected = set() if pen is None else {(g, p) for g, p in pen.filter(pl.col("pen_type").is_in(["GAM", "MAT"])).select("game_id", "p1").iter_rows()}
        self.goal_times = {}
        for gid, t, sec in ev.filter((pl.col("type") == "goal") & (pl.col("period_type") != "SO")).select("game_id", "team_id", "sec").iter_rows():
            self.goal_times.setdefault((gid, t), []).append(sec)

    def came_in(self, gid: int, tid: int, goalie: int) -> dict | None:
        """When a relief goalie first went in, and how: "intermission" (to start a period), "after_goal" (at the
        stoppage for a goal against, within 10 s), or "mid" (anywhere else in play, which is more often an injury), and
        whether a teammate goalie went back in after him."""
        home = self.games[gid]["home_id"] == tid
        col = "home_goalie" if home else "away_goalie"
        st = self.stints.filter((pl.col("game_id") == gid) & (pl.col(col) == goalie))
        if not st.height:
            return None
        at = int(st["start"].min())
        opp = self.games[gid]["away_id" if home else "home_id"]
        if at > 0 and at % 1200 == 0:
            how = "intermission"
        elif any(0 <= at - g <= 10 for g in self.goal_times.get((gid, opp), [])):
            how = "after_goal"
        else:
            how = "mid"
        other = self.stints.filter((pl.col("game_id") == gid) & (pl.col(col).is_not_null()) & (pl.col(col) != goalie) & (pl.col("start") > at))
        return {"sec": at, "how": how, "back": bool(other.height)}  # back: the goalie he replaced (or another) went in again

    def exits(self, gid: int, tid: int) -> list[dict]:
        """Skaters who stopped taking shifts with at least a full period still to play (`EXIT_SEC`), earliest first.
        Over 2025-26, 70% of skaters who sat out the last 20 minutes or more missed the team's next game too (6% of all
        skaters do), so most of these are injuries; a few are benchings. One who dressed for the team's next game and
        sat out less than `BENCHED_SEC` is left out, since a coach sitting him is as likely as an injury. The data can't
        say why, so the wording only says he left (or was ejected, from the penalty sheet)."""
        g = self.games[gid]
        col = "home_skaters" if g["home_id"] == tid else "away_skaters"
        st = self.stints.filter(pl.col("game_id") == gid)
        if not st.height:
            return []
        end = int(st["end"].max())
        last = st.select(col, "end").explode(col).drop_nulls(col).group_by(col).agg(pl.col("end").max())
        later = sorted((x for x in self.games.values() if tid in (x["home_id"], x["away_id"]) and x["game_id"] > gid and x["state"] in ("OFF", "FINAL")),
                       key=lambda x: x["game_id"])
        nxt = later[0]["game_id"] if later else None
        out = []
        for p, at in last.iter_rows():
            gone = end - at
            if gone < EXIT_SEC:
                continue
            ejected = (gid, p) in self.ejected
            if not ejected and nxt and (nxt, p) in self.dressed and gone < BENCHED_SEC:
                continue
            out.append({"id": p, "name": self.full.get(p, self.name.get(p, "?")), "pos": self.pos.get((gid, p)), "sec": int(at), "ejected": ejected,
                        "regular": self.regular(gid, tid, p), "confirmed": self.confirmed_out(gid, tid, p, nxt)})
        return sorted(out, key=lambda x: x["sec"])

    def regular(self, gid: int, tid: int, pid: int) -> bool:
        """A top-nine forward or top-four defenseman by average ice time in the club's earlier games this season (three
        games needed). A depth forward who stops playing mid-game is as likely a coach's choice as anything else (Sora,
        2026-10-16: MacEwen, a healthy scratch for six games, was told as having left; McKenna's was the coach's call)."""
        if self._pg is None:
            self._pg = pl.read_parquet(TABLES / str(self.season) / "player_game.parquet", columns=["game_id", "player_id", "team_id", "pos", "sec"])
        mine = self._pg.filter((pl.col("team_id") == tid) & (pl.col("game_id") < gid)).group_by("player_id", "pos").agg(
            pl.col("sec").mean().alias("avg"), pl.len().alias("n")).filter(pl.col("n") >= 3)
        pos = self.pos.get((gid, pid))
        d = pos == "D"
        pool = mine.filter((pl.col("pos") == "D") if d else (pl.col("pos") != "D") & (pl.col("pos") != "G")).sort("avg", descending=True)
        return pid in pool["player_id"].head(4 if d else 9).to_list()

    def confirmed_out(self, gid: int, tid: int, pid: int, nxt: int | None) -> bool:
        """Whether something besides the shift chart says he was out: he sat out the club's next game, or the daily
        availability table (NHL.com Status Report, ESPN injuries, roster moves) has a report on him after this game."""
        if nxt and (nxt, pid) not in self.dressed:
            return True
        self._availability()
        day = self.games[gid]["date"]
        return bool(self._av.height and self._av.filter((pl.col("player_id") == pid) & (pl.col("report_date") >= day)).height)

    def _availability(self) -> pl.DataFrame:
        """Every availability row that carries a report (NHL.com Status Report, ESPN injuries, roster moves)."""
        if self._av is None:
            f = TABLES / str(self.season) / "availability.parquet"
            self._av = pl.read_parquet(f, columns=["date", "player_id", "report_status", "report_date", "report_injury", "report_timeline"]).drop_nulls(
                "report_status") if f.exists() else pl.DataFrame()
        return self._av

    def injury_news(self, gid: int, pid: int) -> dict | None:
        """The injury report that came out on a player within `NEWS_DAYS` of the game he left (Sora, 2026-10-29: once
        the league or ESPN says he is on injured reserve or day to day, the story should say so instead of only that he
        didn't return). The most telling report in that window (`NEWS_RANK`), dated by its first day, with the injury
        and timeline from any report in it. A bare "out" needs an injury named; suspensions and personal leave are
        not injuries, so they are left out. Post-game pages are rebuilt on every update, so the line appears on the
        first run after the report."""
        av = self._availability()
        if not av.height:
            return None
        day = self.games[gid]["date"][:10]
        until = (datetime.fromisoformat(day) + timedelta(days=NEWS_DAYS)).date().isoformat()
        # from the next day on: a report dated the game day can be the morning's, from before he got hurt
        reps = (av.filter((pl.col("player_id") == pid) & (pl.col("report_date") > day) & (pl.col("report_date") <= until)
                          & pl.col("report_status").is_in(list(NEWS_RANK)))
                .select("report_date", "report_status", "report_injury", "report_timeline").unique().sort("report_date"))
        injury = next((x for x in reps["report_injury"].to_list() if x and x.lower() not in ("undisclosed", "unknown")), None)
        reps = reps.filter((pl.col("report_status") != "out") | pl.lit(injury is not None))
        if not reps.height:
            return None
        best = max(reps.iter_rows(named=True), key=lambda r: (NEWS_RANK[r["report_status"]], -int(r["report_date"].replace("-", ""))))
        days = (datetime.fromisoformat(best["report_date"]) - datetime.fromisoformat(day)).days
        return {"status": best["report_status"], "days": days, "injury": injury, "timeline": best["report_timeline"]}

    def pp_goals(self, gid: int, tid: int) -> int:
        return self.goals.filter((pl.col("game_id") == gid) & (pl.col("team_id") == tid) & pl.col("strength").is_in(list(PP_STRENGTHS))).height

    def pp_record(self, gid: int, team: str) -> tuple[int, int]:
        """(power-play goals, power-play chances) for a team by abbreviation: the league's own line when it is saved,
        else our count."""
        if (gid, team) in self.official:
            return self.official[(gid, team)]
        g = self.games[gid]
        tid = self.team_id[(gid, team)]
        opp = g["away_id"] if tid == g["home_id"] else g["home_id"]
        return self.pp_goals(gid, tid), int(self.short.get((gid, opp), 0))


def measure(S: Season, gid: int, claim: dict, places: dict, fp: dict) -> dict:
    """Tonight's figure for one call, its verdict, and a sentence saying what happened."""
    chk, m = claim["check"], claim["metric"]
    if claim["kind"] in ("edge", "contrast"):
        return measure_edge(S, gid, claim, places, fp)
    if claim["kind"] == "changed":
        return measure_changed(S, gid, claim, places)
    if chk is None:
        return {"verdict": None}
    tid = S.team_id.get((gid, chk["team"])) if chk.get("team") else None
    base, league = chk["baseline"], chk.get("league")
    team = places.get(chk.get("team"), "")
    if m in ("volume", "quality", "turnover", "pp"):
        r = S.rows.get((gid, tid))
        if r is None:
            return {"verdict": "na", "happened": "The numbers for this game aren’t available."}
        if m == "volume":
            v = r["cf_adj"] / r["sec5"] * 3600
            txt = f"{team} took {v:.1f} shot attempts per 60 at 5-on-5 (adjusted for score and venue), against their usual {base:.1f}."
            fmt = lambda x: f"{x:.1f} attempts per 60"
        elif m == "quality":
            v = r["xgf_adj"] / r["ff_adj"] if r["ff_adj"] else 0.0
            txt = f"{team}’s average shot had {previews.a_pct(v)} chance of going in, against their usual {100 * base:.1f}%." if v else f"{team} barely got a shot away at 5-on-5."
            fmt = lambda x: f"{100 * x:.1f}% a shot" if x else "–"
        elif m == "turnover":
            v = 100 * r["to_xgf"] / r["xgf5"] if r["xgf5"] else 0.0
            txt = f"{v:.1f}% of {team}’s 5-on-5 chances came right after winning the puck, against their usual {base:.1f}%."
            fmt = lambda x: f"{x:.1f}%"
        else:
            if r["pp_sec"] < MIN_PP_SEC:
                return {"verdict": "na", "happened": f"{team} had only {clock(r.get('pp_clock', r['pp_sec']))} of power-play time, too little to judge.",
                        "usual": f"{base:.1f} per 60", "tonight": "–"}
            v = r["pp_xgf"] / r["pp_sec"] * 3600
            g = S.pp_record(gid, chk["team"])[0]
            txt = (f"{team}’s power play created {v:.1f} expected goals per 60 in {clock(r.get('pp_clock', r['pp_sec']))} of power-play time, against their usual {base:.1f}, "
                   f"and scored {g} {'goal' if g == 1 else 'goals'}.")
            fmt = lambda x: f"{x:.1f} expected goals per 60"
        if claim["kind"] == "duel":  # graded against the league average alone: which side of it the attack landed on
            usual = chk.get("usual", base)
            what = {"volume": "5-on-5 shot attempts", "quality": "chance quality", "pp": "the power play", "turnover": "chances off turnovers"}[m]
            txt = f"On {what}, {team} had {fmt(v)}. The league average is {fmt(base)}, and they came in at {fmt(usual)}."
            return {"verdict": grade(v, base, None, chk["direction"]), "happened": txt, "usual": fmt(usual), "tonight": fmt(v), "league": fmt(base), "_v": v}
        out = {"verdict": grade(v, base, league, chk["direction"]), "happened": txt, "usual": fmt(base), "tonight": fmt(v), "_v": v}
        if league is not None:
            out["league"] = fmt(league)
        return out
    if m == "control":  # the team that usually gets more of the 5-on-5 chances: beat 50% by half the usual gap?
        r = S.rows.get((gid, tid))
        if r is None or r["xgf_adj"] + r["xga_adj"] <= 0:
            return {"verdict": "na", "happened": "The numbers for this game aren’t available."}
        if r["sec5"] < 600:
            return {"verdict": "na", "happened": f"There was only {clock(r['sec5'])} of 5-on-5 play, too little to judge."}
        v = r["xgf_adj"] / (r["xgf_adj"] + r["xga_adj"])
        bar = 0.5 + chk["gap"] / 4  # a margin (2v - 1) of half the usual gap between the two
        opp = places.get(chk["opp"], chk["opp"])
        ut, ub = chk["baseline"][chk["team"]], chk["baseline"][chk["opp"]]
        return {"verdict": "held" if v >= bar else "partly" if v > 0.5 else "missed",
                "happened": f"{team} had {100 * v:.1f}% of the 5-on-5 expected goals (adjusted for score and venue). Coming in, they had "
                            f"{100 * ut:.1f}% in their games and {opp} {100 * ub:.1f}%, so the call needed {100 * bar:.1f}%.",
                "usual": f"{100 * bar:.1f}%", "tonight": f"{100 * v:.1f}%", "league": "50.0%", "_v": v, "_bar": bar}
    if m == "rest":
        r = S.rows.get((gid, tid))
        if r is None or r["xgf_adj"] + r["xga_adj"] <= 0:
            return {"verdict": "na", "happened": "The numbers for this game aren’t available."}
        v = r["xgf_adj"] / (r["xgf_adj"] + r["xga_adj"])
        return {"verdict": grade(v, base, None, "above"),
                "happened": f"{team} had {100 * v:.1f}% of the 5-on-5 expected goals (adjusted for score and venue), against their usual {100 * base:.1f}%.",
                "usual": f"{100 * base:.1f}%", "tonight": f"{100 * v:.1f}%", "league": "50.0%", "_v": v}
    g = S.games[gid]
    rows = [S.rows.get((gid, g["away_id"])), S.rows.get((gid, g["home_id"]))]
    if any(r is None for r in rows):
        return {"verdict": "na", "happened": "The numbers for this game aren’t available."}
    if m == "pace":
        r = rows[0]
        v = (r["ff_adj"] + r["fa_adj"]) / r["sec5"] * 3600
        usual = (f"the {'faster' if chk['direction'] == 'above' else 'slower'} team’s usual {base:.1f}" if "average" in chk
                 else f"the two teams’ usual {base:.1f}")  # rules 3 set the bar at the faster (or slower) team's usual
        return {"verdict": grade(v, base, league, chk["direction"]),
                "happened": f"The game ran at {v:.1f} shots per 60 at 5-on-5, both teams combined, against {usual} and a league average of {league:.1f}.",
                "usual": f"{base:.1f} per 60", "tonight": f"{v:.1f} per 60", "league": f"{league:.1f} per 60", "_v": v}
    if m == "physical":
        if min(r["close_sec"] for r in rows) < MIN_CLOSE_SEC:
            return {"verdict": "na", "happened": "The game was rarely close, and hit rates are only counted in close games, so this one can’t be judged.", "usual": f"hit score {base:.0f}", "tonight": "–"}
        est = fp[g["away"]]["dims"]["physical"]["blend"]
        league_rate = est["v"] * 100 / est["index"]
        v = sum(r["hits_adj"] / r["close_sec"] * 3600 for r in rows) / 2 / league_rate * 100
        return {"verdict": grade(v, base, 100, "above"),
                "happened": f"The two teams’ combined hit score was {v:.0f}, against their usual {base:.0f} (100 is league average, after adjusting for the arena’s scorer).",
                "usual": f"hit score {base:.0f}", "tonight": f"hit score {v:.0f}", "league": "hit score 100", "_v": v}
    if m == "matchup_share":
        if "opp_lines" not in chk:
            return {"verdict": "na", "happened": "This call was saved before the site recorded the visitors’ lines, so it can’t be checked."}
        names = ", ".join(S.name.get(i, "?").split(" ", 1)[-1] for i in chk["unit_ids"])
        opp_place = places.get(claim["opp"], claim["opp"])
        # A call about a group that never took the ice can't be judged (eye test on 2025-26, 2026-10-13: a pair "held"
        # with one of its two defensemen scratched).
        out = [i for i in [*chk["unit_ids"], *chk.get("opp_ids", [])] if (gid, i) not in S.dressed]
        if out:
            who = " and ".join(S.full.get(i, "?") for i in out[:2])
            return {"verdict": "na", "happened": f"{who} didn’t play, so the matchup this call described never happened.",
                    "usual": f"{base}%", "tonight": "–"}
        sec = matchup_seconds(S, gid, chk)
        total = sum(sec.values())
        whole, part = unit_together(S, gid, chk)
        if part and whole < BROKEN_SHARE * part and whole < BROKEN_SEC:
            # The coach broke the group up (eye test, 2026-10-14: Washington's "L1 (Wilson, Tuch, Strome)" was graded
            # missed while the same page said Tuch and Strome had moved to a new line with Kyrou)
            return {"verdict": "na", "happened": f"{team} broke up that {'pair' if len(chk['unit_ids']) == 2 else 'line'} tonight: {names} were together for only "
                                                 f"{clock(whole)} at 5-on-5, so the matchup this call described never really happened.",
                    "usual": f"{base}%", "tonight": "–"}
        if total < MIN_MATCHUP_SEC:
            return {"verdict": "na", "happened": f"{team}’s {chk['unit']} ({names}) spent only {clock(total)} against {opp_place}’s usual lines, too little to judge.",
                    "usual": f"{base}%", "tonight": "–"}
        v = round(100 * sec.get(chk["opp_line"], 0) / total)
        # The bar is halfway from rotation to the home habit (rules 25): calls saved before then are graded the same way.
        bar = chk.get("bar", round((base + chk["threshold"]) / 2))
        most = max(sec, key=sec.get)
        drew = None
        if most != chk["opp_line"]:  # who the line drew instead, for the plain sentence
            drew = (previews._and([S.name.get(i, "?").split(" ", 1)[-1] for i in chk["opp_lines"][most]]), sec[most], total)
        return {"verdict": grade(v, bar, chk["threshold"], "above"),
                "happened": f"{team}’s {chk['unit']} ({names}) spent {v}% of its 5-on-5 time against {opp_place}’s {chk['opp_line']} "
                            f"({clock(sec.get(chk['opp_line'], 0))} of {clock(total)}). The call needed {bar}%, halfway from the {chk['threshold']}% "
                            f"it would get with no line matching to its home habit of {base}%.",
                "usual": f"{base}%", "tonight": f"{v}%", "league": f"{chk['threshold']}%", "_v": v, "_bar": bar, "_drew": drew}
    return {"verdict": "na"}


def measure_edge(S: Season, gid: int, claim: dict, places: dict, fp: dict) -> dict:
    """An edge call: did the team at the better end of the scale come out ahead tonight? Notes saved under the first
    rulebook ("contrast", with no check) say the same thing in words, so they are checked by the same rule."""
    chk, k = claim["check"], claim["metric"]
    if chk is None:
        if k not in previews.EDGE:
            return {"verdict": "na", "happened": "This note compared the two teams on pace, which both teams share in a single game, so it can’t be checked."}
        chk = previews.edge_check(k, claim["team"], claim["opp"], fp)
    top, bot = chk["team"], chk["opp"]
    rt, rb = S.rows.get((gid, S.team_id.get((gid, top)))), S.rows.get((gid, S.team_id.get((gid, bot))))
    T, B = places.get(top, top), places.get(bot, bot)
    if rt is None or rb is None:
        return {"verdict": "na", "happened": "The numbers for this game aren’t available."}
    need = {"pp": ("pp_sec", MIN_PP_SEC, "power-play time"), "pk": ("pk_sec", MIN_PP_SEC, "penalty-kill time"),
            "physical": ("close_sec", MIN_CLOSE_SEC, "close-game time")}.get(k, ("sec5", 600, "5-on-5 time"))
    short = min(rt[need[0]], rb[need[0]])
    if short < need[1]:
        return {"verdict": "na", "happened": f"One of the teams had only {clock(short)} of {need[2]}, too little to judge."}
    val = lambda r: team_style._value(r, k)
    tv, bv = val(rt), val(rb)
    if k in previews.INDEX_TRAITS:
        if not chk.get("league"):
            return {"verdict": "na", "happened": "There was no league average to score this against."}
        tv, bv = 100 * tv / chk["league"], 100 * bv / chk["league"]
    if k == "depth" and (tv != tv or bv != bv):  # an 11-forward lineup: its bottom six is five players (team_style._depth)
        return {"verdict": "na", "happened": "One of the teams dressed 11 forwards and 7 defensemen, so their bottom six was only five players and the share can’t be compared."}
    if tv != tv or bv != bv:  # NaN: nothing this trait counts happened
        return {"verdict": "na", "happened": "Too little of what this call counts happened to judge it."}
    d = (tv - bv) if chk["higher"] else (bv - tv)
    gap = chk["gap"]
    verdict = "held" if d >= gap / 2 else "partly" if d > 0 else "missed"
    fmt = previews.EDGE[k][4]
    ut, ub = chk["baseline"][top], chk["baseline"][bot]
    return {"verdict": verdict,
            "happened": f"On {chk['measure']}, {T} had {fmt(tv)} and {B} {fmt(bv)}. Coming in, {T} was at {fmt(ut)} and {B} at {fmt(ub)}.",
            "usual": f"{top} {fmt(ut)} · {bot} {fmt(ub)}", "tonight": f"{top} {fmt(tv)} · {bot} {fmt(bv)}", "_v": d, "_chk": chk, "_tb": (tv, bv)}


def measure_changed(S: Season, gid: int, claim: dict, places: dict) -> dict:
    """A team playing unlike their two-season selves: did tonight land on this season's side of the blended level?"""
    chk, k = claim["check"], claim["metric"]
    r = S.rows.get((gid, S.team_id.get((gid, chk["team"]))))
    T = places.get(chk["team"], chk["team"])
    if r is None:
        return {"verdict": "na", "happened": "The numbers for this game aren’t available."}
    need = {"pp": ("pp_sec", MIN_PP_SEC, "power-play time"), "pk": ("pk_sec", MIN_PP_SEC, "penalty-kill time"),
            "physical": ("close_sec", MIN_CLOSE_SEC, "close-game time")}.get(k, ("sec5", 600, "5-on-5 time"))
    if r[need[0]] < need[1]:
        return {"verdict": "na", "happened": f"{T} had only {clock(r[need[0]])} of {need[2]}, too little to judge."}
    v = team_style._value(r, k)
    if v != v:
        return {"verdict": "na", "happened": "Too little of what this call counts happened to judge it."}
    base, season = chk["baseline"], chk["season"]
    fmt = previews.EDGE[k][4]
    show = (lambda x: fmt(100 * x / chk["league"])) if k in previews.INDEX_TRAITS and chk.get("league") else fmt
    beyond = (lambda a, b: a > b) if chk["direction"] == "above" else (lambda a, b: a < b)
    verdict = "held" if beyond(v, base) else "missed"
    return {"verdict": verdict,
            "happened": f"On {previews.EDGE[k][3]}, {T} had {show(v)}, against {show(base)} blended with last season and {show(season)} this season alone.",
            "usual": show(base), "tonight": show(v), "_v": v}


def measure_history(claim: dict, glines: list) -> dict:
    """A goalie's record against one team, called a coincidence: was tonight closer to his usual than to that record?"""
    chk = claim["check"]
    g = next((x for x in glines if x["id"] == chk["goalie_id"]), None)
    fmt = lambda x: f"{x:.3f}".lstrip("0")
    if g is None or not g["started"] or not g["shots"]:
        return {"verdict": "na", "happened": "He didn’t start, so this call can’t be judged.", "usual": fmt(chk["baseline"]), "tonight": "–"}
    sv = g["saves"] / g["shots"]
    verdict = "held" if abs(sv - chk["baseline"]) < abs(sv - chk["record"]) else "missed"
    return {"verdict": verdict,
            "happened": f"{g.get('full') or g['name']} stopped {g['saves']} of {g['shots']} shots ({fmt(sv)}), against his usual {fmt(chk['baseline'])} and his {fmt(chk['record'])} against this team before tonight.",
            "usual": fmt(chk["baseline"]), "tonight": fmt(sv), "league": None, "_v": sv, "_name": g["name"]}


BROKEN_SHARE = 0.4  # a group whose players were all out together for under this share of the time it was counted ...
BROKEN_SEC = 300  # ... and under five minutes in all was broken up for the night


def unit_together(S: Season, gid: int, chk: dict) -> tuple[float, float]:
    """5v5 seconds (all of the home group together, at least the matchup's share of it together) in this game."""
    g = S.games[gid]
    unit = set(chk["unit_ids"])
    need = 2 if len(unit) == 3 else 1
    st = S.stints.filter((pl.col("game_id") == gid) & (pl.col("n_home") == 5) & (pl.col("n_away") == 5) & pl.col("home_goalie").is_not_null() & pl.col("away_goalie").is_not_null())
    side = "home_skaters" if chk["team"] == g["home"] else "away_skaters"
    whole = part = 0.0
    for mine, d in st.select(side, "duration").iter_rows():
        n = len(unit & set(mine))
        if n >= need:
            part += d
        if n == len(unit):
            whole += d
    return whole, part


def matchup_seconds(S: Season, gid: int, chk: dict) -> dict[str, float]:
    """5v5 seconds the home group spent against each of the visitors' usual forward lines in this game."""
    g = S.games[gid]
    unit = set(chk["unit_ids"])
    need = 2 if len(unit) == 3 else 1
    lines = {k: set(v) for k, v in chk["opp_lines"].items()}
    out = defaultdict(float)
    st = S.stints.filter((pl.col("game_id") == gid) & (pl.col("n_home") == 5) & (pl.col("n_away") == 5) & pl.col("home_goalie").is_not_null() & pl.col("away_goalie").is_not_null())
    home_is_team = chk["team"] == g["home"]
    for s in st.iter_rows(named=True):
        mine, theirs = (s["home_skaters"], s["away_skaters"]) if home_is_team else (s["away_skaters"], s["home_skaters"])
        if len(unit & set(mine)) < need:
            continue
        fwd = {p for p in theirs if S.pos.get((gid, p)) in ("C", "L", "R")}
        best = max(lines, key=lambda k: len(lines[k] & fwd)) if lines else None
        if best and len(lines[best] & fwd) >= 2:
            out[best] += s["duration"]
    return out


def team_numbers(S: Season, gid: int, abbr: str) -> dict:
    tid = S.team_id[(gid, abbr)]
    t = S.totals.get((gid, tid), {})
    opp = next(a for (g, a) in S.team_id if g == gid and a != abbr)
    ppg, ppo = S.pp_record(gid, abbr)
    return {"shots": int(t.get("sf", 0)), "xg": round(t.get("xg", 0.0), 2), "cf5": int(t.get("cf5", 0)), "hits": int(t.get("hits", 0)),
            "pp_goals": ppg, "pp_opps": ppo}


def periods(S: Season, gid: int, g: dict) -> list[dict]:
    """The score table: goals and shots on goal for each team in each period played (overtime as "OT"), then a
    shootout column ("SO", goals only) when the game went to one: the shootout winner's goal is the difference between
    the final score and the goals in play."""
    teams = {g["away"]: g["away_id"], g["home"]: g["home_id"]}
    last = max([p for (x, _, p) in S.by_period if x == gid] + [4 if g["last_period"] in ("OT", "SO") else 3])
    out = []
    for per in range(1, last + 1):
        label = ("1st", "2nd", "3rd")[per - 1] if per <= 3 else "OT" if per == 4 else f"{per - 3}OT"
        cell = {a: S.by_period.get((gid, t, per), (None, 0, 0)) for a, t in teams.items()}
        out.append({"label": label, "goals": {a: c[1] for a, c in cell.items()}, "shots": {a: c[2] for a, c in cell.items()}})
    if g["last_period"] == "SO":
        play = {a: sum(p["goals"][a] for p in out) for a in teams}
        out.append({"label": "SO", "goals": {g["away"]: g["away_score"] - play[g["away"]], g["home"]: g["home_score"] - play[g["home"]]}, "shots": None})
    return out


def official(season: int, gid: int) -> dict:
    """The league's own shots on goal and goalie saves from the saved boxscore (refetched once after the game, when the
    NHL settles its counts). The play-by-play is one shot short of the box in a few games a season, and fans check our
    saves against NHL.com (outside notes, Oct 21: Schmid 24 of 29 here, 25 of 30 there). Empty when no box is saved."""
    f = RAW / str(season) / "box" / f"{gid}.json.gz"
    try:
        b = json.load(gzip.open(f))
    except (OSError, ValueError):
        return {}
    sog = {b[s]["abbrev"]: b[s]["sog"] for s in ("awayTeam", "homeTeam") if isinstance(b.get(s, {}).get("sog"), int)}
    gk = {}
    for side in ("awayTeam", "homeTeam"):
        for x in (b.get("playerByGameStats") or {}).get(side, {}).get("goalies", []):
            if isinstance(x.get("shotsAgainst"), int) and isinstance(x.get("saves"), int) and x["shotsAgainst"]:
                gk[x["playerId"]] = (x["shotsAgainst"], x["saves"])
    return {"sog": sog, "goalies": gk}


def use_official(nums: dict, glines: list[dict], off: dict) -> None:
    """Shots on goal and goalie saves as the league counts them; goals and chances (xG) stay ours."""
    for t, n in (off.get("sog") or {}).items():
        if t in nums:
            nums[t]["shots"] = n
    for g in glines:
        sa_sv = (off.get("goalies") or {}).get(g["id"])
        if sa_sv:
            sa, sv = sa_sv
            g.update(shots=sa, saves=sv, sv=round(sv / sa, 3))  # goals against stay ours: they match the box, which can
            # count a goal with no shot (Lankinen, Oct 9: 8 against on 20 shots and 13 saves)


@lru_cache(maxsize=4)
def _set_up_goals(season: int) -> dict:
    """(game_id, goalie_id) -> goals he allowed that came off a pass (assisted) and that our chance model rated low.
    A private study of the NHL's goal-tracking files (2026-10-25, project files goal-tracking/before-goals.md) found
    the goals the model rates lowest mostly follow a pass or have an open shooter, which the model cannot see, so
    these are usually well-made plays, not a goalie's fault. Used only to choose wording, never shown."""
    ev = pl.read_parquet(TABLES / str(season) / "events.parquet", columns=["game_id", "event_id", "type", "p2", "goalie_id", "period_type"])
    ev = ev.filter((pl.col("type") == "goal") & (pl.col("period_type") != "SO") & pl.col("p2").is_not_null() & pl.col("goalie_id").is_not_null())
    gp = TABLES / str(season) / "goals.parquet"
    if not gp.exists():
        return {}
    xg = pl.read_parquet(gp, columns=["game_id", "event_id", "xg"])
    low = ev.join(xg, on=["game_id", "event_id"]).filter(pl.col("xg") < surprise_rules.SET_UP_XG)
    return {(r["game_id"], r["goalie_id"]): r["len"] for r in low.group_by("game_id", "goalie_id").len().iter_rows(named=True)}


def goalie_lines(S: Season, gid: int, abbr_of: dict) -> list[dict]:
    out = []
    for r in S.goalies.filter(pl.col("game_id") == gid).sort("team_id", "sec", descending=[False, True]).iter_rows(named=True):
        sv = (r["sa"] - r["ga"]) / r["sa"] if r["sa"] else None
        out.append({"id": r["goalie_id"], "name": S.gshort.get((gid, r["goalie_id"])) or S.name.get(r["goalie_id"], "?"),
                    "full": S.full.get(r["goalie_id"]) or S.name.get(r["goalie_id"], "?"), "team": abbr_of[r["team_id"]], "started": r["started"],
                    "toi": clock(r["sec"]), "shots": r["sa"], "saves": r["sa"] - r["ga"], "ga": r["ga"], "xga": round(r["xga"], 2),
                    "gsax": round(r["xga"] - r["ga"], 2), "sv": round(sv, 3) if sv is not None else None,
                    "high": {"shots": r["sa_high"], "goals": r["ga_high"]}})
        if not r["started"] and r["sec"] > 0:
            came = S.came_in(gid, r["team_id"], r["goalie_id"])
            if came:
                out[-1]["came_in"] = came
    return out


def surprises(S: Season, gid: int, nums: dict, places: dict, fp: dict, usual_lines: dict, glines: list, replay: dict | None,
              calls: list[dict] = ()) -> tuple[list[dict], dict[str, dict]]:
    """See `surprises.py`. `fp` and `usual_lines` are the teams' numbers as the preview saw them; `calls` are the
    snapshot's claims with their verdicts. Returns the surprises and an "and then some" note per call id."""
    g = S.games[gid]
    a, h = g["away"], g["home"]
    rows = {t: S.rows.get((gid, S.team_id[(gid, t)])) for t in (a, h)}
    if any(r is None for r in rows.values()):
        return [], {}
    score = {a: g["away_score"], h: g["home_score"], "pp_goals": {t: nums[t]["pp_goals"] for t in (a, h)},
             "pp_opps": {t: nums[t]["pp_opps"] for t in (a, h)}}
    gg = S.goals.filter(pl.col("game_id") == gid)  # no shootout goals in here
    score["shootout"] = g["last_period"] == "SO"
    score["goals"] = {t: gg.filter(pl.col("team_id") == S.team_id[(gid, t)]).height for t in (a, h)}
    score["non_en"] = {t: gg.filter((pl.col("team_id") == S.team_id[(gid, t)]) & (pl.col("strength") != "EN")).height for t in (a, h)}
    trios = []
    for t in (a, h):
        usual = [set(ids) for ids in usual_lines.get(t, [])]
        if not usual:
            continue
        units = S.units.filter((pl.col("game_id") == gid) & (pl.col("team_id") == S.team_id[(gid, t)]) & (pl.col("kind") == "F") & (pl.col("sec") >= 300)).sort("sec", descending=True)
        for r in units.iter_rows(named=True):
            ids = {int(x) for x in r["unit"].split("-")}
            if ids not in usual:
                trios.append((t, (lambda xs: ", ".join(xs[:-1]) + " and " + xs[-1])([S.full.get(i, "?") for i in line_order.order(S.season, S.team_id[(gid, t)], sorted(ids), g["date"])]), int(r["sec"]), ids))
                break
    left = {t: [x for x in S.exits(gid, S.team_id[(gid, t)]) if x["regular"]] for t in (a, h)}
    cands = surprise_rules.candidates(rows, a, h, score, fp, places, glines, trios, replay, S.season)
    absent = {t: [{"id": p, "name": S.full.get(p, S.name.get(p, "?"))} for p in sorted({p for u in usual_lines.get(t, []) for p in u})
                  if (gid, p) not in S.dressed and S.first_game.get((S.team_id[(gid, t)], p), gid) < gid] for t in (a, h)}
    cands = surprise_rules.forced(cands, left, usual_lines, places, absent)
    cands, beyond = surprise_rules.against_calls(cands, list(calls))
    return surprise_rules.pick(cands), beyond


def replay_block(row: dict, away: str, home: str) -> dict:
    """Run-it-back shares for the post-game page: how often teams with chances like these win (replays.calibrated),
    level replays split evenly. `raw` keeps the uncorrected replay share."""
    h = replays.calibrated(row["home_share"])
    tie = row["reg_tie"]
    h_reg = min(max(h - tie / 2, 0.0), 1 - tie)
    return {"share": {away: round(1 - h, 3), home: round(h, 3)},
            "raw": {away: round(row["away_share"], 3), home: round(row["home_share"], 3)},
            "regulation": {away: round(1 - tie - h_reg, 3), "tie": round(tie, 3), home: round(h_reg, 3)},
            "xg": {away: round(row["away_xg"], 2), home: round(row["home_xg"], 2)},
            "chances": {away: row["away_chances"], home: row["home_chances"]}}


def run(season: int = CURRENT_SEASON) -> dict:
    teams = json.loads((SITE_DATA / "teams.json").read_text())
    places = wording.places({t["abbr"]: t["place"] for t in teams})  # for sentences: "the Rangers", not "NY Rangers"
    fp = json.loads((SITE_DATA / "fingerprints.json").read_text())["teams"]
    lines = json.loads((SITE_DATA / "lines.json").read_text())["teams"]
    usual_lines = {t: [u["ids"] for u in v["units"] if u["label"][0] == "L"] for t, v in lines.items()}
    S = Season(season)
    meter = {r["game_id"]: r for r in replays.season_table(season).iter_rows(named=True)}
    out_dir = SITE_DATA / "recaps"
    out_dir.mkdir(parents=True, exist_ok=True)
    snaps = {}
    for p in (SITE_DATA / "previews").glob("*.json"):
        if p.name != "index.json":
            s = json.loads(p.read_text())
            snaps[s["game_id"]] = s
    index, tally = [], defaultdict(int)
    from pipeline.export import outlook
    sched = json.loads((SITE_DATA / "schedule.json").read_text())["games"]
    try:
        reveals = outlook.reveal(season, [g for g in sched if g["final"]], places)
    except Exception as e:  # the post-game page still works without the reveal
        print(f"win chance reveal unavailable: {e!r}")
        reveals = {}
    by_kind = defaultdict(lambda: defaultdict(int))
    for gid, g in sorted(S.games.items()):
        if g["state"] not in ("OFF", "FINAL"):
            continue
        abbr_of = {g["home_id"]: g["home"], g["away_id"]: g["away"]}
        nums = {t: team_numbers(S, gid, t) for t in (g["away"], g["home"])}
        glines = goalie_lines(S, gid, abbr_of)
        use_official(nums, glines, official(season, gid))
        snap = snaps.get(gid)
        rebuilt = bool((snap or {}).get("rebuilt"))
        pre = (snap or {}).get("pregame") or {}
        fp_pre = {**fp, **pre.get("fingerprints", {}).get("teams", {})}
        lines_pre = {**usual_lines, **{t: [u["ids"] for u in v["units"] if u["label"][0] == "L"] for t, v in pre.get("lines", {}).get("teams", {}).items()}}
        rb = replay_block(meter[gid], g["away"], g["home"]) if gid in meter else None
        waiting = gid in pending.games(season)  # shift chart not finished: grade nothing that needs who was on the ice
        if waiting:
            for t in nums:
                nums[t]["cf5"] = None
        calls, graded_claims = [], []
        for c in (snap or {}).get("claims", []):
            res = dict(PENDING if waiting else
                       measure_history(c, glines) if c["kind"] == "history" else measure(S, gid, c, places, fp_pre))
            res = grades.present(S, gid, c, res, places, fp_pre)
            graded_claims.append({**c, "verdict": res.get("verdict")})
            calls.append({"id": c["id"], "kind": c["kind"], "head": c["head"], "body": c["body"], "call": c.get("call"), "nums": c.get("nums"), "cite": c["cite"],
                          **{k: c[k] for k in ("see", "chips") if c.get(k)}, **res})
            if res.get("verdict") in ("held", "partly", "missed"):
                tally[res["verdict"]] += 1  # rebuilt previews count like live ones (user decision)
                by_kind[c["kind"]][res["verdict"]] += 1
        su = _set_up_goals(season)
        for gl_ in glines:
            gl_["_set_up"] = su.get((gid, gl_["id"]), 0)
        surp, beyond = ([], {}) if waiting else surprises(S, gid, nums, places, fp_pre, lines_pre, glines, rb, graded_claims)
        for gl_ in glines:
            gl_.pop("_set_up", None)  # wording only: not exported
        for c in calls:  # heads and bodies are the frozen preview's own words
            for k in ("happened", "title", "plain", "saw"):
                if c.get(k):
                    c[k] = wording.tidy(c[k])
        for x in surp + list(beyond.values()):
            x["head"], x["body"] = wording.tidy(x.get("head")), wording.tidy(x.get("body"))
        for c in calls:
            if c["id"] in beyond:
                c["beyond"] = beyond[c["id"]]["body"]
        rec = {"game_id": gid, "date": g["date"], "start": g["start_utc"], "away": g["away"], "home": g["home"], "venue": g["venue"],
               "score": {g["away"]: g["away_score"], g["home"]: g["home_score"]}, "end": g["last_period"],
               "preview": snap is not None, "rebuilt": rebuilt, "preview_page": bool((snap or {}).get("pregame")), "snapshot_at": snap["snapshot_at"] if snap else None, "calls": calls,
               "reveal": reveals.get(gid), "numbers": nums, "periods": periods(S, gid, g), "goalies": glines, "surprises": surp,
               "replays": rb, **({"pending": True} if waiting else {})}
        if not waiting:  # skaters who left and didn't come back, for the story (never why; ejections are the penalty notes')
            gone = [{"id": x["id"], "team": t, "sec": x["sec"], "regular": x["regular"], "confirmed": x["confirmed"],
                     **({"news": n} if (n := S.injury_news(gid, x["id"])) else {})}
                    for t in (g["away"], g["home"]) for x in S.exits(gid, S.team_id[(gid, t)]) if not x["ejected"]]
            if gone:
                rec["left"] = sorted(gone, key=lambda x: x["sec"])
        (out_dir / f"{gid}.json").write_text(json.dumps(rec, separators=(",", ":"), ensure_ascii=False))
        graded = [c["verdict"] for c in calls if c.get("verdict") in ("held", "partly", "missed")]
        index.append({"id": gid, "date": g["date"], "away": g["away"], "home": g["home"], "score": rec["score"], "end": g["last_period"],
                      "rebuilt": rebuilt, "calls": len(graded), "held": graded.count("held"), "partly": graded.count("partly"), "missed": graded.count("missed")})
    summary = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "season": season,
               "tally": {k: tally[k] for k in ("held", "partly", "missed")},
               "by_kind": {k: dict(v) for k, v in by_kind.items()}, "games": index}
    (out_dir / "index.json").write_text(json.dumps(summary, separators=(",", ":")))
    return {"recaps": len(index), "graded": sum(tally.values())}
