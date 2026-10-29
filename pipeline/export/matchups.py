"""Who played against whom: each finished game's line-against-line 5-on-5 time, for the post-game page.

Sora, 2026-10-15 (thread on post-game line matchups, mock-up approved the same day): the shift chart shows who was on
the ice together, but not how much time every line spent against every other line. One small file per finished
regular-season game, `matchups/{game_id}.json`, read when the post-game page is built and drawn as the preview's
split strips under a fold in "How it unfolded".

Lines and pairs are tonight's, numbered the way the Swing tags and the flow chart number them (`units.game_lines`).
A group on the ice counts as one of them when at least two of its three forwards (or one of its two defensemen)
belong to it, as on the team and preview pages (`units.matchups`). Only 5-on-5 with both goalies in. Time against a
forward group that is none of the four lines ("mixed") is counted apart and left out of the shares.

When a regular skater left the game (the recap's `left`, written by `recaps.Season.exits`), the game is also split
at his last shift: "before" and "after", with each line's players as they actually went out after the change.

`notes` are the "What changed" lines, by fixed rules (never why a player was out or left):
- match: the home team's lines sent out against one opposing line far more than rotation would give (the home coach
  has last change, so these are his picks);
- swing: the matchup with the biggest gap in chances, when no match note already told it;
- exit: after a regular left, who moved into his spot, whose ice time rose or fell, whether the home team's matchups
  held, and how the chances went before and after (with the score, since a team behind pushes the play);
`lede` says who had last change and who was missing or left, and `foot` compares how hard the home coach matched
with his other home games this season.
"""
import json
from collections import Counter, defaultdict

import polars as pl

from pipeline.export.grammar import and_list
from pipeline.config import CURRENT_SEASON, REGULAR, SITE_DATA, TABLES
from pipeline.export import pending, wording
from pipeline.metrics import line_order
from pipeline.metrics import units as U

OUT = SITE_DATA / "matchups"
UNBLOCKED = ["shot-on-goal", "missed-shot", "goal"]
LINES = ["L1", "L2", "L3", "L4"]
GROUPS = LINES + ["P1", "P2", "P3"]
ORD = {1: "first", 2: "second", 3: "third", 4: "fourth"}
ROLE = {"L3": "The third line", "L4": "The fourth line"}
PERIOD = {1: "first", 2: "second", 3: "third"}
# Rules for the notes (seconds, percentage points, expected goals).
MATCH_SHARE, MATCH_GAP, MATCH_SEC = 50, 20, 240   # sent out against one line half his time, 20+ points above rotation, 4+ minutes
SWING_SEC, SWING_GAP = 180, 0.35                    # a matchup of 3+ minutes with a chance gap of 0.35+ expected goals
EXIT_MIN_SEC = 300                                  # he played at least 5 minutes before leaving, so "before" means something
MOVED_SEC = 120                                     # a new trio needs 2+ minutes together to count as a move
SHARE_MOVE = 7                                      # a forward's share of the team's 5-on-5 time moved 7+ points
HELD, BROKE = 45, 35                                # a matched line still saw that opponent 45%+ of the time after, or under 35%
REGULAR_OF = 5, 3                                   # in a line or pair in 3+ of the team's last 5 games
INTENSITY_GAMES = 3                                 # earlier home games under the same coach needed to compare matching


def clock(sec: float) -> str:
    s = int(round(sec))
    return f"{s // 60}:{s % 60:02d}"


def label(sets: dict[str, set], players: list[int], kind: str) -> str | None:
    """Which of tonight's lines (or pairs) this group on the ice is: the one holding most of it, needing two of three
    forwards or one of two defensemen, as `units.matchups` does."""
    return U.label_of(sets, players, kind)


class Season:
    """Everything one season's games need, read once."""

    def __init__(self, season: int):
        d = TABLES / str(season)
        self.season = season
        self.games = pl.read_parquet(d / "games.parquet").filter(pl.col("game_type") == REGULAR).sort("date", "game_id")
        self.stints = pl.read_parquet(d / "stints.parquet")
        ev = pl.read_parquet(d / "events.parquet", columns=["game_id", "event_id", "period_type", "sec", "type", "is_home", "situation_code", "home_on", "away_on"])
        ev = ev.filter(pl.col("period_type") != "SO")
        self.goals = ev.filter(pl.col("type") == "goal").select("game_id", "sec", "is_home")
        xg = pl.read_parquet(d / "shots_xg_own.parquet")
        # 5-on-5 with both goalies in (penalty shots carry their own codes, so they are out)
        self.shots = ev.filter(pl.col("type").is_in(UNBLOCKED) & (pl.col("situation_code") == "1551") & pl.col("is_home").is_not_null()).join(
            xg, on=["game_id", "event_id"], how="left").with_columns(pl.col("xg").fill_null(0.0))
        self.gu = pl.read_parquet(d / "game_units.parquet") if (d / "game_units.parquet").exists() else None
        self.players = pl.read_parquet(d / "players.parquet")
        self.info = {r["player_id"]: r for r in self.players.sort("game_id").iter_rows(named=True)}
        self.dressed = defaultdict(set)  # (game, team) -> players
        self.later_teams = defaultdict(list)  # player -> [(game, team)]
        for r in self.players.iter_rows(named=True):
            self.dressed[(r["game_id"], r["team_id"])].add(r["player_id"])
            self.later_teams[r["player_id"]].append((r["game_id"], r["team_id"]))
        cp = d / "coaches.parquet"
        self.coach = {(r["game_id"], r["team"]): r["coach"] for r in pl.read_parquet(cp).iter_rows(named=True)} if cp.exists() else {}
        teams = json.loads((SITE_DATA / "teams.json").read_text()) if (SITE_DATA / "teams.json").exists() else []
        self.place = {t["abbr"]: wording.place(t["place"]) for t in teams}
        self._picks: dict[tuple[int, int], set] = {}
        self.points = defaultdict(list)  # player -> [(game, points)], last season and this one
        for s in (season - 1, season):
            pg = TABLES / str(s) / "player_game.parquet"
            if pg.exists():
                for r in pl.read_parquet(pg, columns=["game_id", "player_id", "g", "a1", "a2"]).iter_rows(named=True):
                    self.points[r["player_id"]].append((r["game_id"], (r["g"] or 0) + (r["a1"] or 0) + (r["a2"] or 0)))

    def points_before(self, pid: int, gid: int) -> int:
        return sum(n for g, n in self.points.get(pid, []) if g < gid)

    def picks(self, gid: int, tid: int) -> set:
        """Players in one game's four lines and three pairs, by 5-on-5 time together (`units._pick`)."""
        if (gid, tid) not in self._picks:
            mine = self.gu.filter((pl.col("game_id") == gid) & (pl.col("team_id") == tid)) if self.gu is not None else None
            out = set()
            if mine is not None:
                for kind, n in (("F", 4), ("D", 3)):
                    for u in U._pick(mine.filter(pl.col("kind") == kind), n, share=kind == "F" and U.eleven(self.season, gid, tid)):
                        out |= {int(p) for p in u["unit"].split("-")}
            self._picks[(gid, tid)] = out
        return self._picks[(gid, tid)]

    def team_games(self, tid: int, before: int) -> list[int]:
        g = self.games.filter(((pl.col("home_id") == tid) | (pl.col("away_id") == tid)) & pl.col("state").is_in(["OFF", "FINAL"]))
        ids = g["game_id"].to_list()
        return ids[:ids.index(before)] if before in ids else ids

    def missing(self, gid: int, tid: int) -> list[int]:
        """Regulars (in a line or pair in 3 of the team's last 5 games) who didn't dress tonight and haven't played for
        another club since, most-used first."""
        prev = self.team_games(tid, gid)[-REGULAR_OF[0]:]
        count = Counter(p for g in prev for p in self.picks(g, tid))
        out = []
        for p, n in count.most_common():
            if n < REGULAR_OF[1] or p in self.dressed[(gid, tid)]:
                continue
            if any(g >= gid and t != tid for g, t in self.later_teams[p]):
                continue  # moved on
            out.append(p)
        return out

    def name(self, pid: int, short: dict[int, str]) -> str:
        return short.get(pid) or self.info.get(pid, {}).get("last", str(pid))

    def full(self, pid: int) -> str:
        r = self.info.get(pid)
        return f"{r['first']} {r['last']}" if r else str(pid)


def short_names(rows: list[dict]) -> dict[int, str]:
    """Last names, with a first initial where two players in the game share one ("A. Protas", "I. Protas")."""
    by_last = defaultdict(list)
    for r in rows:
        by_last[r["last"]].append(r)
    out = {}
    for last, rs in by_last.items():
        for r in rs:
            out[r["player_id"]] = f"{r['first'][0]}. {last}" if len(rs) > 1 else last
    return out


def side_groups(sets: dict[str, set], skaters: list[int], pos: dict[int, str]) -> tuple[str | None, str | None, tuple | None]:
    f = [p for p in skaters or [] if pos.get(p) in ("C", "L", "R")]
    d = [p for p in skaters or [] if pos.get(p) == "D"]
    return (label(sets, f, "L") if len(f) == 3 else None, label(sets, d, "P") if len(d) == 2 else None, tuple(sorted(f)) if len(f) == 3 else None)


def tally(S: Season, gid: int, g: dict, sets: dict[int, dict[str, set]], pos: dict[int, str], cut: int | None) -> dict:
    """Seconds and chances by period ("all", and "before"/"after" the cut), side (0 away, 1 home), own group and
    opposing forward line."""
    ids = (g["away_id"], g["home_id"])
    parts = lambda t: ("all",) if cut is None else ("all", "before" if t < cut else "after")
    sec = defaultdict(float)
    trios = defaultdict(Counter)          # (period, side, line) -> trio -> seconds
    toi = defaultdict(float)              # (period, side, player) -> 5-on-5 seconds
    team_sec = defaultdict(float)         # (period, side) -> 5-on-5 seconds
    trail = defaultdict(float)            # (period, side) -> 5-on-5 seconds trailing
    lead = defaultdict(float)
    goal_rows = S.goals.filter(pl.col("game_id") == gid).sort("sec").iter_rows(named=True)
    goals = [(r["sec"], int(bool(r["is_home"]))) for r in goal_rows]
    st = S.stints.filter((pl.col("game_id") == gid) & (pl.col("n_home") == 5) & (pl.col("n_away") == 5)
                         & pl.col("home_goalie").is_not_null() & pl.col("away_goalie").is_not_null())
    for s in st.iter_rows(named=True):
        on = {0: s["away_skaters"], 1: s["home_skaters"]}
        gr = {h: side_groups(sets[ids[h]], on[h], pos) for h in (0, 1)}
        score = [sum(1 for t, w in goals if t < s["start"] + 1 and w == h) for h in (0, 1)]
        for P in parts(s["start"]):
            for h in (0, 1):
                team_sec[(P, h)] += s["duration"]
                if score[h] < score[1 - h]:
                    trail[(P, h)] += s["duration"]
                elif score[h] > score[1 - h]:
                    lead[(P, h)] += s["duration"]
                for p in on[h] or []:
                    toi[(P, h, p)] += s["duration"]
                own_f, own_d, trio = gr[h]
                opp = gr[1 - h][0] or "mixed"
                for own in (own_f, own_d):
                    if own:
                        sec[(P, h, own, opp)] += s["duration"]
                if own_f and trio:
                    trios[(P, h, own_f)][trio] += s["duration"]
                if gr[1 - h][0]:
                    sec[(P, h, "*", gr[1 - h][0])] += s["duration"]
    xg = defaultdict(float)  # (period, side, own, opp, "xgf"|"xga"|"gf"|"ga")
    for r in S.shots.filter(pl.col("game_id") == gid).iter_rows(named=True):
        on = {0: r["away_on"], 1: r["home_on"]}
        gr = {h: side_groups(sets[ids[h]], on[h], pos) for h in (0, 1)}
        shooter = int(bool(r["is_home"]))
        for P in parts(r["sec"]):
            for h in (0, 1):
                mine = shooter == h
                xg[(P, h, "team", None, "xgf" if mine else "xga")] += r["xg"]
                xg[(P, h, "team", None, "gf" if mine else "ga")] += r["type"] == "goal"
                opp = gr[1 - h][0]
                for own in gr[h][:2]:
                    if not own:
                        continue
                    for key in ((own, None), (own, opp)) if opp else ((own, None),):
                        xg[(P, h, *key, "xgf" if mine else "xga")] += r["xg"]
                        xg[(P, h, *key, "gf" if mine else "ga")] += r["type"] == "goal"
    return {"sec": sec, "trios": trios, "toi": toi, "team_sec": team_sec, "trail": trail, "lead": lead, "xg": xg}


def matched_share(T: dict, P: str, h: int) -> float | None:
    """How hard a coach matched: each forward line's time against the one opposing line it saw most, over all its
    time (mixed included)."""
    top = tot = 0.0
    for own in LINES:
        vs = [T["sec"].get((P, h, own, o), 0.0) for o in LINES]
        top += max(vs)
        tot += sum(vs) + T["sec"].get((P, h, own, "mixed"), 0.0)
    return 100 * top / tot if tot else None


def game(S: Season, gid: int, g: dict, recap: dict, history: dict) -> dict | None:
    if S.gu is None:
        return None
    gu = S.gu.filter(pl.col("game_id") == gid)
    if gu.is_empty():
        return None
    ids = (g["away_id"], g["home_id"])
    abbr = (g["away"], g["home"])
    place = {h: S.place.get(abbr[h], abbr[h]) for h in (0, 1)}
    rows = S.players.filter(pl.col("game_id") == gid)
    pos = {r["player_id"]: r["pos"] for r in rows.iter_rows(named=True)}
    short = short_names(list(rows.iter_rows(named=True)))
    nm = lambda p: S.name(p, short)
    sets, members = {}, {}
    for h, tid in enumerate(ids):
        sets[tid], members[h] = {}, {}
        for _, lab, u in U.game_lines(S.season, gu, gid, tid):
            ps = [int(p) for p in u["unit"].split("-")]
            sets[tid][lab] = set(ps)
            members[h][lab] = ps
    if not all(len([k for k in sets[t] if k[0] == "L"]) == 4 for t in ids):
        return None

    # The split: the first regular (in one of tonight's lines or pairs) who left with time still to play.
    split = None
    for x in recap.get("left", []):
        h = 1 if x["team"] == abbr[1] else 0
        if x["sec"] >= EXIT_MIN_SEC and any(x["id"] in m for m in members[h].values()):
            split = {"id": x["id"], "side": h, "sec": x["sec"]}
            break
    cut = split["sec"] if split else None
    T = tally(S, gid, g, sets, pos, cut)

    def lead_of(h: int, lab: str) -> int:
        """The player a line is known by: its top scorer over last season and this one before tonight, then the most
        5-on-5 time tonight."""
        ps = members[h][lab]
        return max(ps, key=lambda p: (S.points_before(p, gid), T["toi"].get(("all", h, p), 0.0)))

    total = {h: g["home_score" if h else "away_score"] for h in (0, 1)}
    word = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine"}

    def goals_then(w: int, k: int) -> str:
        """The goals in a matchup, as a sentence: " Both Washington goals came then."."""
        if not k:
            return ""
        if k == total[w]:
            lead = "The only" if k == 1 else "Both" if k == 2 else "All " + word.get(k, str(k))
            return f" {lead} {place[w]} goal{'s' if k > 1 else ''} came then."
        its = "their" if place[w].startswith("the ") else "its"
        return f" {place[w][0].upper() + place[w][1:]} scored {word.get(k, str(k))} of {its} {word.get(total[w], str(total[w]))} goals then."

    def trio_names(P: str, h: int, lab: str) -> tuple[list[str], bool]:
        """A line's players as they went out in this part of the game (its most-used trio), and whether that differs
        from the line's players over the whole game."""
        base = members[h][lab]
        if lab[0] != "L" or P == "all":
            return [nm(p) for p in base], False
        c = T["trios"].get((P, h, lab))
        if not c:
            return [nm(p) for p in base], False
        trio, secs = c.most_common(1)[0]
        if set(trio) == set(base) or secs < MOVED_SEC:
            return [nm(p) for p in base], False
        keep = [p for p in base if p in trio]
        new = [p for p in trio if p not in base]
        return [nm(p) for p in line_order.order(S.season, ids[h], new + keep, g.get("date"))], True  # left wing, center, right wing

    periods = ["all"] + (["before", "after"] if split else [])
    data = {}
    for P in periods:
        data[P] = {}
        for h in (0, 1):
            tot = sum(T["sec"].get((P, h, "*", o), 0.0) for o in LINES)
            out_rows = []
            for own in GROUPS:
                if own not in members[h]:
                    continue
                vs = [round(T["sec"].get((P, h, own, o), 0.0)) for o in LINES]
                x = lambda k, o=None: T["xg"].get((P, h, own, o, k), 0.0)
                names, new = trio_names(P, h, own)
                out_rows.append({"label": own, "names": names, **({"new": True} if new else {}), "sec": sum(vs),
                                 "mixed": round(T["sec"].get((P, h, own, "mixed"), 0.0)), "vs": vs,
                                 "xgf": round(x("xgf"), 2), "xga": round(x("xga"), 2), "gf": int(x("gf")), "ga": int(x("ga")),
                                 "cells": [[round(x("xgf", o), 2), round(x("xga", o), 2), int(x("gf", o)), int(x("ga", o))] for o in LINES]})
            data[P][h] = {"rows": out_rows, "expected": [round(100 * T["sec"].get((P, h, "*", o), 0.0) / tot) if tot else 0 for o in LINES]}

    coach_h = S.coach.get((gid, abbr[1]))
    notes = []
    row = lambda P, h, lab: next(r for r in data[P][h]["rows"] if r["label"] == lab)
    line_name = lambda h, lab: ROLE.get(lab) or f"{nm(lead_of(h, lab))}’s line"
    opp_name = lambda h, lab: f"{nm(lead_of(h, lab))}’s line"
    list_of = lambda h, lab: and_list([nm(p) for p in members[h][lab]])

    def chances(f: float, a: float, they: str = "they") -> str:
        if f + a < 0.3:
            return ""
        if f >= 1.25 * a and f - a >= 0.1:
            return f"{they} had the better of the chances, {f:.2f} to {a:.2f} expected goals"
        if a >= 1.25 * f and a - f >= 0.1:
            return f"{they} had the worse of the chances, {f:.2f} to {a:.2f} expected goals"
        return f"the chances were close, {f:.2f} to {a:.2f} expected goals"

    # match: the home coach's picks
    told = set()
    cands = []
    for lab in LINES:
        r = row("all", 1, lab)
        tot = r["sec"]
        if not tot:
            continue
        for j, v in enumerate(r["vs"]):
            share = 100 * v / tot
            gap = share - data["all"][1]["expected"][j]
            if share >= MATCH_SHARE and gap >= MATCH_GAP and v >= MATCH_SEC:
                cands.append((gap, lab, j, v, tot, share))
    cands.sort(reverse=True)
    for k, (gap, lab, j, v, tot, share) in enumerate(cands[:3]):
        opp = LINES[j]
        f, a, gf, ga = row("all", 1, lab)["cells"][j]
        how = f"{clock(v)} of their {clock(tot)} at 5-on-5 against {list_of(0, opp)}"
        text = (f"{coach_h} sent {list_of(1, lab)} out for {how}" if k == 0 and coach_h else f"{list_of(1, lab)} spent {how}")
        text += f" ({round(share)}%, about {data['all'][1]['expected'][j]}% with no line matching)"
        ch = chances(f, a)
        text += (f", and {ch}." if ch else ".") + (goals_then(1, gf) if gf >= ga else goals_then(0, ga))
        notes.append({"kind": "match", "head": f"{line_name(1, lab)} drew {opp_name(0, opp)}", "text": text})
        told.add((lab, opp))

    # swing: the matchup with the biggest chance gap, from the home team's side
    best = None
    for lab in LINES:
        r = row("all", 1, lab)
        for j, (f, a, gf, ga) in enumerate(r["cells"]):
            if r["vs"][j] >= SWING_SEC and abs(f - a) >= SWING_GAP and (best is None or abs(f - a) > abs(best[2] - best[3])):
                best = (lab, j, f, a, gf, ga, r["vs"][j])
    if best and (best[0], LINES[best[1]]) not in told:
        lab, j, f, a, gf, ga, v = best
        won = f > a
        w = 1 if won else 0
        text = (f"{list_of(1, lab)} kept drawing {place[0]}’s {ORD[j + 1]} line, {list_of(0, LINES[j])}. "
                f"In those {clock(v)} {place[w]} had {max(f, a):.2f} expected goals to {place[1 - w]}’s {min(f, a):.2f}")
        text += "." + goals_then(w, gf if won else ga)
        notes.append({"kind": "swing", "head": "One matchup paid off" if won else "One matchup went wrong", "text": text})

    # exit: what changed after a regular left
    lede_bits = {0: [], 1: []}
    for h, tid in enumerate(ids):
        gone = S.missing(gid, tid)[:2]
        if gone:
            lede_bits[h].append(f"played without {and_list([S.full(p) for p in gone])}")
    if split:
        h, pid, at = split["side"], split["id"], split["sec"]
        per = at // 1200 + 1
        when = f"{clock(at - (per - 1) * 1200)} of the {PERIOD.get(per, 'overtime')}" if per <= 3 else "the start of overtime"
        lede_bits[h].append(f"lost {S.full(pid)} after {when}")
        mine = next(lab for lab, ps in members[h].items() if pid in ps)
        mates = [p for p in members[h][mine] if p != pid]
        sents = []
        if mine[0] == "L":
            c = Counter()
            for (P, hh, lab), trios in T["trios"].items():
                if P == "after" and hh == h:
                    for trio, s in trios.items():
                        if all(m in trio for m in mates):
                            c[next(p for p in trio if p not in mates)] += s
            if c:
                repl, s = c.most_common(1)[0]
                if s >= MOVED_SEC:
                    from_lab = next((lab for lab, ps in members[h].items() if repl in ps), None)
                    up = from_lab is not None and from_lab[0] == "L" and int(from_lab[1]) > int(mine[1])
                    sents.append(f"{nm(repl)} moved {'up' if up else 'in'} beside {and_list([nm(m) for m in mates])} ({clock(s)} together)")
                    split["repl"] = nm(repl)
        # whose 5-on-5 time rose or fell
        tb, ta = T["team_sec"].get(("before", h), 0.0), T["team_sec"].get(("after", h), 0.0)
        if tb and ta:
            moves = []
            for p in {p for (P, hh, p) in T["toi"] if hh == h and P == "before"} | {p for (P, hh, p) in T["toi"] if hh == h and P == "after"}:
                if p == pid or pos.get(p) not in ("C", "L", "R"):
                    continue
                b = 100 * T["toi"].get(("before", h, p), 0.0) / tb
                a = 100 * T["toi"].get(("after", h, p), 0.0) / ta
                moves.append((a - b, p, round(b), round(a)))
            up = sorted([m for m in moves if m[0] >= SHARE_MOVE], reverse=True)[:2]
            down = sorted([m for m in moves if m[0] <= -SHARE_MOVE])[:1]
            bits = [f"{nm(p)} {'went' if i == 0 else ''} from {b}% to {a}%".replace("  ", " ") for i, (_, p, b, a) in enumerate(up)]
            if bits:
                s = f"{' and '.join(bits)} of {place[h]}’s 5-on-5 time"
                if down:
                    _, p, b, a = down[0]
                    s += f", and {nm(p)} dropped from {b}% to {a}%"
                sents.append(s)
            elif down:
                _, p, b, a = down[0]
                sents.append(f"{nm(p)} dropped from {b}% to {a}% of {place[h]}’s 5-on-5 time")
        # did the home team's matchups hold?
        held, broke = [], []
        for lab in LINES:
            rb, ra = row("before", 1, lab), row("after", 1, lab)
            if rb["sec"] < 120 or ra["sec"] < 120:
                continue
            j = max(range(4), key=lambda k: rb["vs"][k])
            b = 100 * rb["vs"][j] / rb["sec"]
            if b < MATCH_SHARE:
                continue
            a = 100 * ra["vs"][j] / ra["sec"]
            own = line_name(1, lab)
            own = own[0].lower() + own[1:] if lab in ROLE else own
            what = f"{own} against {place[0]}’s {ORD[j + 1]} line ({round(b)}% before, {round(a)}% after)"
            if a >= HELD:
                held.append(what)
            elif a < BROKE:
                broke.append(what)
        if held:
            sents.append(f"The matchups {'mostly ' if broke else ''}stayed: {and_list(held[:2])}")
        if broke:
            sents.append(f"{'One' if len(broke) == 1 else 'Some'} changed: {and_list(broke[:2])}" if held else f"The matchups changed: {and_list(broke[:2])}")
        head = f"After {nm(pid)} left, the lines changed" + (" but the matchups held" if held and not broke else " and most matchups held" if len(held) > len(broke) else "")
        if sents:
            notes.append({"kind": "exit", "head": head, "text": ". ".join(s[0].upper() + s[1:] for s in sents) + "."})
        # and how the chances went
        xb = (T["xg"].get(("before", h, "team", None, "xgf"), 0.0), T["xg"].get(("before", h, "team", None, "xga"), 0.0))
        xa = (T["xg"].get(("after", h, "team", None, "xgf"), 0.0), T["xg"].get(("after", h, "team", None, "xga"), 0.0))
        if sum(xb) >= 0.3 and sum(xa) >= 0.3 and tb >= 300 and ta >= 300:
            sb, sa = xb[0] / sum(xb), xa[0] / sum(xa)
            head = "And it went better" if sa - sb >= 0.1 else "And it went worse" if sb - sa >= 0.1 else "And the chances stayed about even"
            text = f"{place[h]} had {xb[0]:.2f} to {xb[1]:.2f} expected goals at 5-on-5 before {nm(pid)} left and {xa[0]:.2f} to {xa[1]:.2f} after."
            if mine[0] == "L" and split.get("repl"):
                rb, ra = row("before", h, mine), row("after", h, mine)
                if rb["xgf"] + rb["xga"] >= 0.2 and ra["xgf"] + ra["xga"] >= 0.2:
                    text += (f" The {ORD[int(mine[1])]} line went from {rb['xgf']:.2f} to {rb['xga']:.2f} with {nm(pid)} "
                             f"to {ra['xgf']:.2f} to {ra['xga']:.2f} with {split['repl']}.")
            trail, lead = T["trail"].get(("after", h), 0.0), T["lead"].get(("after", h), 0.0)
            if abs(sa - sb) >= 0.1:
                if trail >= ta / 2 and sa > sb:
                    text += f" {place[h]} trailed for most of that stretch, though, and a team behind usually pushes the play, so the score explains part of it."
                elif lead >= ta / 2 and sa < sb:
                    text += f" {place[h]} led for most of that stretch, though, and a team ahead usually sits back, so the score explains part of it."
            notes.append({"kind": "result", "head": head, "text": text})

    lede = f"{place[1]} had last change, so {coach_h or 'their coach'} picked most of these matchups."
    for h in (1, 0):
        if lede_bits[h]:
            lede += f" {place[h]} {and_list(lede_bits[h])}."

    # foot: how hard the home coach matched, against his other home games this season
    foot = None
    tonight = matched_share(T, "all", 1)
    past = [v for (c, v) in history.get(ids[1], []) if c == coach_h][-10:]
    if tonight is not None and len(past) >= INTENSITY_GAMES and coach_h:
        lo, hi = min(past), max(past)
        how = "harder than" if tonight > hi + 3 else "less than" if tonight < lo - 3 else "about as hard as"
        n = len(past)
        foot = (f"{coach_h} matched {how} in his other home games: each line spent {round(tonight)}% of its time against one opposing line, "
                f"against {round(lo)}% to {round(hi)}% in his {'first ' + ['three', 'four', 'five', 'six', 'seven', 'eight', 'nine'][n - 3] if n < 10 else 'last ten'}.")
    if tonight is not None:
        history.setdefault(ids[1], []).append((coach_h, tonight))

    return {"game_id": gid, "away": abbr[0], "home": abbr[1],
            **({"split": {"name": nm(split["id"]), "side": split["side"], "sec": split["sec"]}} if split else {}),
            "lede": wording.tidy(lede), "notes": [{**n, "text": wording.tidy(n["text"])} for n in notes], **({"foot": foot} if foot else {}),
            "periods": {P: {str(h): data[P][h] for h in (0, 1)} for P in periods}}


def run(season: int = CURRENT_SEASON) -> dict:
    S = Season(season)
    games = S.games.filter(pl.col("state").is_in(["OFF", "FINAL"]))
    waiting = pending.games(season)
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.json"):
        old.unlink()
    history: dict[int, list] = {}
    n = 0
    for g in games.iter_rows(named=True):
        gid = g["game_id"]
        if gid in waiting:
            continue
        rp = SITE_DATA / "recaps" / f"{gid}.json"
        recap = json.loads(rp.read_text()) if rp.exists() else {}
        out = game(S, gid, g, recap, history)
        if out:
            (OUT / f"{gid}.json").write_text(json.dumps(out, separators=(",", ":"), ensure_ascii=False))
            n += 1
    return {"games": n}


if __name__ == "__main__":
    print(run())
