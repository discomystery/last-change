"""Plain-English intros for team and player pages.

A few sentences at the top of each page, numbers-light, saying who a team or player is by our numbers, whether they
are good, whether they have been good, and (for players) where they have played and any storylines for the next game:
a first game back against a former team, a run of points against the next opponent, a career record against them.

Everything is rule-based from data we already hold or fetch (NHL career game logs in `ingest/careers.py`). Wording
follows the site's voice: teams are "they" with nicknames, no numbers in the first sentence, ranks in words.
"""
from __future__ import annotations

import json
import zlib
from datetime import date

import polars as pl

from pipeline.config import CURRENT_SEASON, PLAYOFFS, REGULAR, SITE_DATA, TABLES
from pipeline.metrics import adjust

HISTORY = 4  # seasons of team history to describe (we hold full tables from 2022-23)


# ---------------------------------------------------------------- helpers

def _pick(pool: list[str], *key) -> str:
    """Stable choice from a wording pool (same text every run, varied across teams and players)."""
    return pool[zlib.crc32("|".join(map(str, key)).encode()) % len(pool)]


def _cap(t: str) -> str:
    return t[:1].upper() + t[1:] if t else t


def _and(xs: list[str]) -> str:
    xs = [x for x in xs if x]
    return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " and " + xs[-1] if xs else ""


def _ordinal(n: int) -> str:
    s = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{s}"


WORDS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve"]


def _num(n: int) -> str:
    return WORDS[n] if 0 <= n < len(WORDS) else str(n)


def _season_name(s: int) -> str:
    return f"{s}-{str(s + 1)[-2:]}"


def _teams() -> dict[str, dict]:
    return {t["abbr"]: t for t in json.loads((SITE_DATA / "teams.json").read_text())}


def _nick(t: dict) -> str:
    return t.get("nick") or t["place"]


def _the(t: dict) -> str:
    return f"the {_nick(t)}"


def _rank_words(rank: int, n: int = 32) -> str:
    """A league rank in words, never a bare "14th of 32"."""
    if rank == 1:
        return "the best in the league"
    if rank <= 3:
        return "as good as anyone in the league"
    if rank <= 8:
        return "among the league's best"
    if rank <= n // 2:
        return "a bit better than average"
    if rank <= n - 8:
        return "a bit worse than average"
    if rank <= n - 3:
        return "among the league's worst"
    return "as bad as anyone in the league"


# ---------------------------------------------------------------- team history

def _results(season: int) -> pl.DataFrame:
    """One row per team per game: points earned, goals, regular season or playoffs."""
    g = pl.read_parquet(TABLES / str(season) / "games.parquet").filter(pl.col("state").is_in(["OFF", "FINAL"]))
    rows = []
    for r in g.iter_rows(named=True):
        extra = r["last_period"] in ("OT", "SO")
        for side, other in (("home", "away"), ("away", "home")):
            gf, ga = r[f"{side}_score"], r[f"{other}_score"]
            rows.append({"game_id": r["game_id"], "date": r["date"], "type": r["game_type"], "team": r[side], "opp": r[other],
                         "home": side == "home", "gf": gf, "ga": ga, "win": gf > ga, "otl": gf < ga and extra})
    return pl.DataFrame(rows)


def _playoff_round(res: pl.DataFrame, team: str) -> int:
    """0 = missed, 1-4 = the round they went out in, 5 = won the Cup."""
    p = res.filter((pl.col("type") == PLAYOFFS) & (pl.col("team") == team))
    if p.is_empty():
        return 0
    last = p.sort("game_id")[-1]
    rnd = int(str(last["game_id"][0])[-3])
    series = p.filter(pl.col("game_id").cast(pl.Utf8).str.slice(0, 9) == str(last["game_id"][0])[:9])
    return 5 if rnd == 4 and series["win"].sum() == 4 else rnd


def team_history(seasons: list[int]) -> dict[str, list[dict]]:
    """Per team, per past season: points rank, points share, playoff result."""
    out: dict[str, list[dict]] = {}
    for s in seasons:
        res = _results(s)
        reg = res.filter(pl.col("type") == REGULAR).group_by("team").agg(
            gp=pl.len(), pts=(2 * pl.col("win").cast(pl.Int32) + pl.col("otl").cast(pl.Int32)).sum(),
            gd=(pl.col("gf") - pl.col("ga")).sum()).sort(["pts", "gd"], descending=True).with_row_index("rank", offset=1)
        for r in reg.iter_rows(named=True):
            out.setdefault(r["team"], []).append({"season": s, "rank": r["rank"], "pts": r["pts"], "gp": r["gp"],
                                                  "playoffs": _playoff_round(res, r["team"])})
    return out


def xg_share(season: int) -> dict[str, dict]:
    """5v5 score-and-venue adjusted xG share this season, per team, plus games with the better of the chances."""
    tg = pl.read_parquet(TABLES / str(season) / "team_game.parquet").filter(pl.col("strength") == "5v5")
    t = adjust.apply(tg, adjust.weights())
    games = pl.read_parquet(TABLES / str(season) / "games.parquet").filter(pl.col("game_type") == REGULAR)
    abbr = dict(zip(games["home_id"], games["home"])) | dict(zip(games["away_id"], games["away"]))
    per = t.filter(pl.col("game_id").is_in(games["game_id"].to_list())).group_by("game_id", "team_id").agg(
        pl.col("xgf_adj").sum(), pl.col("xga_adj").sum())
    tot = per.group_by("team_id").agg(pl.col("xgf_adj").sum(), pl.col("xga_adj").sum(), gp=pl.len(),
                                      better=(pl.col("xgf_adj") > pl.col("xga_adj")).sum())
    tot = tot.with_columns(share=pl.col("xgf_adj") / (pl.col("xgf_adj") + pl.col("xga_adj"))).sort("share", descending=True).with_row_index("rank", offset=1)
    return {abbr[r["team_id"]]: {"share": r["share"], "rank": r["rank"], "gp": r["gp"], "better": r["better"]} for r in tot.iter_rows(named=True)}


# ---------------------------------------------------------------- team identity

# How a fingerprint extreme reads in a sentence: (top-of-league phrase, bottom-of-league phrase). Verb phrases after
# "The Hurricanes ...". None = that end says nothing interesting (e.g. low "second chances").
STYLE = {
    "volume": ("fire from everywhere", "pick their spots rather than pile up shots"),
    "quality": ("get to the dangerous chances", "settle for a lot of hopeful shots"),
    "rebounds": ("crash the net for second chances", None),
    "turnover": ("pounce on turnovers", None),
    "dShots": ("send their defensemen in to shoot", "leave most of the shooting to the forwards"),
    "inClose": ("get right to the net", "shoot from distance"),
    "suppression": ("give up very few shots", "let the other team shoot a lot"),
    "qualityAllowed": ("keep the other team to hopeful shots", "give up dangerous chances"),
    "breakdowns": ("rarely get burned after losing the puck", "get caught after turnovers too often"),
    "pace": ("play fast, wide-open games", "grind games down"),
    "forecheck": ("hound the puck in the other team's end", "sit back rather than forecheck"),
    "physical": ("hit everything that moves", "play a finesse game"),
    "depth": ("roll all four lines", "lean hard on their top players"),
}
# Two traits that read better as one thought: (trait, end, trait, end) -> phrase.
PAIRS = {
    ("volume", "hi", "quality", "lo"): "fire from everywhere, though a lot of it is hopeful",
    ("volume", "lo", "quality", "hi"): "don't shoot much, but they get to the dangerous chances",
    ("suppression", "hi", "qualityAllowed", "lo"): "give up very few shots, though the ones they allow are dangerous",
    ("suppression", "lo", "qualityAllowed", "hi"): "let the other team shoot, but mostly from places that don't hurt",
    ("volume", "hi", "suppression", "hi"): "spend most of the night in the other team's end",
    ("volume", "lo", "suppression", "lo"): "spend a lot of the night in their own end",
}
# Special teams, goaltending and discipline: a clause of their own, (good, bad).
EXTRA = {
    "pp": ("their power play is lethal", "their power play has no bite"),
    "pk": ("their penalty kill is airtight", "their penalty kill leaks"),
    "goalie": ("their goaltending has been stealing games", "their goaltending has let them down"),
    "powerKill": ("they're dangerous even shorthanded", None),
    "discipline": ("they stay out of the box", "they spend a lot of time in the box"),
}
OPPOSED = [{"volume", "pace"}, {"suppression", "pace"}, {"quality", "inClose"}, {"forecheck", "physical"}]


def _extremes(fp: dict, keys, cut: int) -> list[tuple[str, str, int]]:
    """(trait, "hi"/"lo", distance from the middle) for traits at or beyond the cut, most extreme first."""
    out = []
    for k in keys:
        e = (fp["dims"].get(k) or {}).get("blend")
        if e and e["pct"] >= 100 - cut:
            out.append((k, "hi", e["pct"] - 50))
        elif e and e["pct"] <= cut:
            out.append((k, "lo", 50 - e["pct"]))
    return sorted(out, key=lambda x: -x[2])


def team_style(fp: dict) -> tuple[list[str], list[str]]:
    """Phrases for the team's two most extreme 5-on-5 traits (pairs read as one thought), and the clauses for special
    teams and goaltending at the far ends. Returns (phrases, trait keys used)."""
    ext = [(k, end, d) for k, end, d in _extremes(fp, STYLE, 20) if STYLE[k][0 if end == "hi" else 1]]
    phrases, used = [], []
    for k, end, _ in ext:
        if k in used or any({k, u} <= pair for u in used for pair in OPPOSED):
            continue
        pair = next(((k2, e2) for k2, e2, _ in ext if k2 not in used and k2 != k
                     and ((k, end, k2, e2) in PAIRS or (k2, e2, k, end) in PAIRS)), None)
        if pair:
            phrases.append(PAIRS.get((k, end, *pair)) or PAIRS[(*pair, k, end)])
            used += [k, pair[0]]
        else:
            phrases.append(STYLE[k][0 if end == "hi" else 1])
            used.append(k)
        if len(phrases) == 2:
            break
    return phrases, used


def team_extras(fp: dict) -> tuple[str | None, list[str]]:
    ext = [(k, end) for k, end, _ in _extremes(fp, EXTRA, 15) if EXTRA[k][0 if end == "hi" else 1]][:2]
    if not ext:
        return None, []
    cl = [EXTRA[k][0 if end == "hi" else 1] for k, end in ext]
    joint = " and " if len({end for _, end in ext}) == 1 else ", but "
    return _cap(joint.join(cl)) + ".", [k for k, _ in ext]


def _record(t: dict) -> str:
    return f"{t['w']}-{t['l']}-{t['otl']}"


def _of(k: int, n: int) -> str:
    if k == 0:
        return f"none of their {_num(n)}"
    if k == n:
        return f"all {_num(n)}" if n > 2 else "both"
    return f"{_num(k)} of {_num(n)}"


def team_intro(abbr: str, t: dict, fp: dict, xg: dict, hist: list[dict], recent: list[dict]) -> dict:
    """Sentences: who they are; are they good this season; have they been good; current form."""
    the = _cap(_the(t))
    phrases, used = team_style(fp)
    if phrases:
        out = [f"{the} {_and(phrases)}."]
    else:
        out = [f"{the} don't lean hard on any one style: nothing in their 5-on-5 game sits far from league average."]
    extra, used2 = team_extras(fp)
    if extra:
        out.append(extra)

    # This season: the record against the chances.
    x = xg.get(abbr)
    gp = t["gp"]
    if gp and x:
        pts_pct = t["pts"] / (2 * gp)
        rec = "good" if pts_pct >= 0.6 else "bad" if pts_pct < 0.45 else "even"
        play = "good" if x["share"] >= 0.53 else "bad" if x["share"] <= 0.47 else "even"
        start = f"{_cap(_num(gp))} games in, they're {_record(t)}"
        ch = f"they've had the better of the chances at 5-on-5 in {_of(x['better'], x['gp'])}"
        verdict = {
            ("good", "good"): f"{start}, and it's no fluke: {ch}.",
            ("good", "even"): f"{start}, a shade better than the play: {ch}.",
            ("good", "bad"): f"{start}, but the record flatters them: {ch}.",
            ("even", "good"): f"{start}, and they've played better than that: {ch}.",
            ("even", "even"): f"{start}, and that's about how they've played: {ch}.",
            ("even", "bad"): f"{start}, a bit better than the play: {ch}.",
            ("bad", "good"): f"{start}, which undersells how they've played: {ch}.",
            ("bad", "even"): f"{start}, a little unlucky given the play: {ch}.",
            ("bad", "bad"): f"{start}, and the play matches the record: {ch}.",
        }[(rec, play)]
        out.append(verdict[:-1] + (". Early days, though." if gp < 10 else "."))

    # History: playoffs and finishes over the seasons we hold.
    if hist:
        past = sorted(hist, key=lambda h: h["season"])[-HISTORY:]
        made = [h for h in past if h["playoffs"]]
        last = past[-1]
        best = max(reversed(past), key=lambda h: h["playoffs"])
        run = {5: "won the Cup", 4: "made the Final", 3: "reached the conference final", 2: "won a round"}
        n = len(past)
        if len(made) == n:
            s = f"They've been good for a while: playoffs in each of the last {_num(n)} seasons"
        elif not made:
            s = f"Good seasons have been hard to find: no playoffs in any of the last {_num(n)}"
        else:
            s = f"They've made the playoffs in {_num(len(made))} of the last {_num(n)} seasons"
        if best["playoffs"] >= 2:
            when = "last spring" if best is last else f"in {best['season'] + 1}"
            s += f", and {run[best['playoffs']]} {when}"
        s += "."
        if not last["playoffs"] and made:
            s += f" Last season they missed, finishing {_ordinal(last['rank'])} in the league."
        elif not made and last["rank"] <= 20:
            s += f" Last season was their best in a while ({_ordinal(last['rank'])} in the league)."
        elif last["rank"] <= 3:
            s += f" Last season they finished {_ordinal(last['rank'])} in the league."
        out.append(s)

    # Form: a streak of three or more.
    if recent:
        won = recent[-1]["win"]
        n = 0
        for r in reversed(recent):
            if r["win"] == won:
                n += 1
            else:
                break
        if n >= 3:
            out.append(f"They've {'won' if won else 'lost'} {_num(n)} straight.")
    return {"team": abbr, "name": t["name"], "text": out, "style": used + used2}


# ---------------------------------------------------------------- players

ROLE_LINE = {"L1": "first-line", "L2": "second-line", "L3": "third-line", "L4": "fourth-line",
             "P1": "top-pair", "P2": "second-pair", "P3": "third-pair"}
POS_NOUN = {"C": "center", "L": "winger", "R": "winger", "D": "defenseman"}
# Standout skills (blend percentile 85+ among his peers, likely range clear of the middle). Phrase completes "He ...".
SKILL = {
    "offImpact": "drives play at 5-on-5",
    "defImpact": "shuts down the other team's chances",
    "chances": "gets to dangerous scoring chances",
    "shooting": "shoots a lot",
    "finishing": "finishes better than most",
    "playmaking": "sets up a lot of goals",
    "powerPlay": "is a real threat on the power play",
    "shThreat": "is dangerous even while killing penalties",
    "hits": "plays a physical game",
    "blocks": "blocks a lot of shots",
    "takeaways": "steals a lot of pucks",
    "drawsPenalties": "draws a lot of penalties",
    "faceoffs": "wins his draws",
}


def _role_words(p: dict, teams: dict) -> list[str]:
    """"Aho is Carolina's second-line center and their busiest forward." plus a special-teams sentence."""
    role = p.get("role") or {}
    team = teams.get(p["team"], {"place": p["team"]})
    noun = POS_NOUN.get(p["pos"], "player")
    line = (role.get("line") or {}).get("label")
    who = f"{team['place']}'s {ROLE_LINE[line]} {noun}" if line in ROLE_LINE else f"a {noun} for {team['place']}"
    if role.get("toi_rank") == 1 and role.get("gp", 0) >= 2:
        who += f" and their busiest {'defenseman' if p['pos'] == 'D' else 'forward'}"
    out = [f"{p['last']} is {who}."]
    st = []
    pp = (role.get("pp") or {}).get("label")
    if pp == "PP1":
        st.append("quarterbacks the top power-play unit" if (role.get("pp") or {}).get("role") == "Quarterback" else "plays on the top power-play unit")
    elif pp == "PP2":
        st.append("plays on the second power-play unit")
    pk = (role.get("pk") or {}).get("role")
    if pk == "starter":
        st.append("starts the penalty kill" + (" and takes the draw" if (role.get("pk") or {}).get("draw") else ""))
    elif pk == "second":
        st.append("comes on as the second wave of the penalty kill")
    if st:
        out.append(f"He {_and(st)}.")
    return out


def _skills(p: dict) -> list[str]:
    found = []
    for k in SKILL:
        e = (p.get("traits", {}).get(k) or {}).get("blend")
        if e and e.get("ok") and e["pct"] >= 85 and e.get("lo", 0) >= 60:
            found.append((k, e["pct"]))
    found.sort(key=lambda kv: -kv[1])
    return [SKILL[k] for k, _ in found[:2]]


def _nhl_seasons(career: dict) -> list[dict]:
    """Regular-season NHL rows from the landing response, merged per season (a traded player has one row per team)."""
    rows: dict[int, dict] = {}
    for s in career.get("seasonTotals", []):
        if s.get("leagueAbbrev") != "NHL" or s.get("gameTypeId") != REGULAR:
            continue
        r = rows.setdefault(s["season"], {"season": s["season"], "gp": 0, "g": 0, "a": 0, "p": 0})
        for k, src in (("gp", "gamesPlayed"), ("g", "goals"), ("a", "assists"), ("p", "points")):
            r[k] += s.get(src) or 0
    return [rows[k] for k in sorted(rows)]


def _games(logs: dict) -> list[dict]:
    """Every NHL game from the career logs, oldest first."""
    out = [{**g, "type": int(key.split("-")[1])} for key, gl in logs.items() for g in gl]
    return sorted(out, key=lambda g: (g["gameDate"], g["gameId"]))


def _stints(games: list[dict]) -> list[dict]:
    """Spells with each club, in order: team, first and last game date, regular-season games."""
    out: list[dict] = []
    for g in games:
        if out and out[-1]["team"] == g["teamAbbrev"]:
            out[-1]["last"] = g["gameDate"]
            out[-1]["gp"] += g["type"] == REGULAR
        else:
            out.append({"team": g["teamAbbrev"], "first": g["gameDate"], "last": g["gameDate"], "gp": int(g["type"] == REGULAR)})
    return out


def _season_of(d: str) -> int:
    y, m = int(d[:4]), int(d[5:7])
    return y if m >= 8 else y - 1


def _seasons_span(first: str, last: str) -> int:
    return _season_of(last) - _season_of(first) + 1


def _full(seasons: list[dict]) -> list[dict]:
    return [s for s in seasons if s["season"] // 10000 < CURRENT_SEASON and s["gp"] >= 40]


def career_sentence(p: dict, seasons: list[dict]) -> str | None:
    """Have they been good: a scoring identity from the last three full seasons."""
    full = _full(seasons)
    if not full:
        return None
    last = full[-1]
    recent = full[-3:]
    ppg = sum(s["p"] for s in recent) / sum(s["gp"] for s in recent)
    if p["pos"] == "D":
        label = "one of the league's top-scoring defensemen" if ppg >= 0.7 else None
    else:
        label = "one of the league's elite scorers" if ppg >= 1.25 else "a point-a-game player" if ppg >= 0.95 else None
    goals30 = sum(1 for s in full if s["gp"] >= 60 and s["g"] * 82 / s["gp"] >= 30)
    nhl_years = len(seasons) - (seasons[-1]["season"] // 10000 == CURRENT_SEASON)
    if label and len(recent) >= 3:
        out = f"He's been {label} for years: {last['p']} points last season"
        if goals30 >= 2 and p["pos"] != "D":
            out += f", and {_num(goals30)} 30-goal seasons in his career"
        return out + "."
    if label:
        return f"He's already {label}: {last['p']} points last season, his {_ordinal(nhl_years)} in the league."
    if last["p"] >= 45 or (p["pos"] == "D" and last["p"] >= 35):
        return f"He had {last['p']} points last season."
    return None


def season_form(p: dict, seasons: list[dict]) -> str | None:
    s = (p.get("stats") or {}).get("season")
    full = _full(seasons)[-3:]
    if not s or s["gp"] < 3 or not full:
        return None
    usual = sum(x["p"] for x in full) / sum(x["gp"] for x in full)
    now = s["p"] / s["gp"]
    if usual < 0.3:
        return None
    if now >= usual * 1.4 and s["p"] >= 4:
        return f"He's off to a flying start, with {s['p']} points in {_num(s['gp'])} games."
    if now <= usual * 0.4 and s["gp"] >= 4:
        return f"It's been a slow start: {_num(s['p'])} point{'s' if s['p'] != 1 else ''} in {_num(s['gp'])} games."
    return None


def move_sentence(p: dict, stints: list[dict], teams: dict, today: str) -> str | None:
    """A player new to his club (first game with them in the last 200 days): where he came from."""
    if len(stints) < 2 or stints[-1]["team"] != p["team"]:
        return None
    cur, prev = stints[-1], stints[-2]
    if (date.fromisoformat(today) - date.fromisoformat(cur["first"])).days > 200:
        return None
    here = teams[p["team"]]["place"]
    pt = teams.get(prev["team"])
    spell = _seasons_span(prev["first"], prev["last"])
    if _season_of(cur["first"]) != _season_of(prev["last"]):
        when = "this season"
    else:
        when = f"in {date.fromisoformat(cur['first']).strftime('%B')}"
    if spell >= 3:
        return f"He's new in {here} {when}, after {_num(spell)} seasons with the {_nick(pt) if pt else prev['team']}."
    return f"He's new in {here} {when}, arriving from {pt['place'] if pt else prev['team']}."


def _streak(games: list[dict], key: str) -> tuple[int, int]:
    """Games in a row (most recent first) with at least one `key`, and the points in that run."""
    n = pts = 0
    for g in reversed(games):
        if g[key] > 0:
            n += 1
            pts += g["points"]
        else:
            break
    return n, pts


def opponent_notes(p: dict, games: list[dict], stints: list[dict], opp: str, teams: dict, tonight: bool) -> list[str]:
    """Storylines for his next game, against `opp`: a first game back, a run against them, a career record."""
    out = []
    o = teams.get(opp, {"place": opp, "nick": opp})
    on = "Tonight" if tonight else "His next game"
    reg = [g for g in games if g["type"] == REGULAR]
    vs = [g for g in reg if g["opponentAbbrev"] == opp and g["teamAbbrev"] != opp]
    former = [s for s in stints[:-1] if s["team"] == opp]
    if former:
        since_left = [g for g in vs if g["gameDate"] > former[-1]["last"]]
        drafted = p.get("draft_team") == opp
        if not since_left:
            tail = ", the club that drafted him" if drafted else ""
            out.append(f"{on} is his first game against the {_nick(o)} since leaving {o['place']}{tail}.")
        else:
            spell = sum(_seasons_span(s["first"], s["last"]) for s in former)
            out.append(f"{on} is against the {_nick(o)}, his team for {_num(spell)} season{'s' if spell != 1 else ''}.")
    if len(vs) >= 3:
        gn, _ = _streak(vs, "goals")
        pn, pts = _streak(vs, "points")
        if gn >= 3:
            out.append(f"He has scored in {_num(gn)} straight games against the {_nick(o)}.")
        elif pn >= 5:
            out.append(f"He has a point in {_num(pn)} straight games against the {_nick(o)}, {pts} points in all.")
        total = sum(g["points"] for g in vs)
        usual = sum(g["points"] for g in reg) / len(reg)
        if gn < 3 and pn < 5 and len(vs) >= 10 and usual >= 0.4 and total / len(vs) >= 1.3 * usual:
            out.append(f"He has always liked playing the {_nick(o)}: {total} points in {len(vs)} career games against them.")
        elif len(vs) >= 10 and usual >= 0.6 and total / len(vs) <= 0.6 * usual:
            out.append(f"The {_nick(o)} have given him trouble over the years: {total} points in {len(vs)} career games.")
    return out


def reunions(stints: list[dict], games: list[dict], opp_players: list[dict]) -> list[str]:
    """Former long-time teammates (three or more seasons on the same club) on the other side for the first time."""
    out = []
    for q in opp_players:
        now = q["stints"][-1]
        if any(g["opponentAbbrev"] == now["team"] and g["gameDate"] >= now["first"] for g in games):
            continue  # they have met since he moved
        for a in stints:
            for b in q["stints"][:-1]:
                if a["team"] != b["team"]:
                    continue
                lo, hi = max(a["first"], b["first"]), min(a["last"], b["last"])
                if hi >= lo and _seasons_span(lo, hi) >= 3:
                    out.append(f"His teammate of {_num(_seasons_span(lo, hi))} seasons, {q['name']}, is on the other side for the first time.")
    return out[:1]


def own_streak(games: list[dict]) -> str | None:
    """His current run this season: a goal streak of three or a point streak of four or more."""
    cur = [g for g in games if g["type"] == REGULAR and g["gameId"] // 1_000_000 == CURRENT_SEASON]
    if len(cur) < 3:
        return None
    gn, _ = _streak(cur, "goals")
    pn, _ = _streak(cur, "points")
    if gn >= 3:
        return f"He has scored in {_num(gn)} straight games."
    if pn >= 4:
        return f"He has a point in {_num(pn)} straight games{' to start the season' if pn == len(cur) else ''}."
    return None


def player_intro(p: dict, career: dict | None, teams: dict, next_game: dict | None, today: str,
                 opp_players: list[dict] | None = None) -> dict:
    out = _role_words(p, teams)
    skills = _skills(p)
    if skills:
        out.append(f"He {_and(skills)}.")
    stories = []
    if career:
        seasons = _nhl_seasons(career["landing"])
        games = _games(career["logs"])
        stints = _stints(games)
        p = {**p, "draft_team": (career["landing"].get("draftDetails") or {}).get("teamAbbrev")}
        for s in (move_sentence(p, stints, teams, today), career_sentence(p, seasons), season_form(p, seasons), own_streak(games)):
            if s:
                out.append(s)
        if next_game:
            stories = opponent_notes(p, games, stints, next_game["opp"], teams, next_game["date"] == today)
            stories += reunions(stints, games, opp_players or [])
    return {"id": p["id"], "text": out, "next": stories}


# ---------------------------------------------------------------- export

def _careers() -> dict[int, dict]:
    from pipeline.ingest import careers
    return careers.load_all()


def run(today: str | None = None) -> dict:
    """Write intros/teams.json (all 32) and intros/players.json (everyone with a player page)."""
    today = today or date.today().isoformat()
    teams = _teams()
    fp = json.loads((SITE_DATA / "fingerprints.json").read_text())["teams"]
    xg = xg_share(CURRENT_SEASON)
    hist = team_history(list(range(CURRENT_SEASON - HISTORY, CURRENT_SEASON)))
    res = _results(CURRENT_SEASON).filter(pl.col("type") == REGULAR).sort("game_id")
    sched = json.loads((SITE_DATA / "schedule.json").read_text())["games"]
    nxt: dict[str, dict] = {}
    for g in sched:
        if not g["final"] and g["date"] >= today:
            for t, o in ((g["home"], g["away"]), (g["away"], g["home"])):
                nxt.setdefault(t, {"date": g["date"], "opp": o, "id": g["id"], "home": t == g["home"]})
    team_out = {a: team_intro(a, teams[a], fp[a], xg, hist.get(a, []), res.filter(pl.col("team") == a).to_dicts())
                for a in teams if a in fp}

    careers = _careers()
    index = json.loads((SITE_DATA / "players" / "index.json").read_text())
    pages = {}
    for row in index:
        f = SITE_DATA / "players" / f"{row['id']}.json"
        if f.exists():
            pages[row["id"]] = json.loads(f.read_text())
    stints = {pid: _stints(_games(c["logs"])) for pid, c in careers.items()}
    by_team: dict[str, list[dict]] = {}
    for pid, p in pages.items():
        if pid in stints:
            by_team.setdefault(p["team"], []).append({"name": p["name"], "stints": stints[pid]})
    player_out = {}
    for pid, p in pages.items():
        if p.get("pos") == "G" or "traits" not in p:
            continue
        n = nxt.get(p["team"])
        player_out[pid] = player_intro(p, careers.get(pid), teams, n, today, by_team.get(n["opp"], []) if n else [])
        if n:
            player_out[pid]["game"] = n
    curly = lambda xs: [x.replace("'", "’") for x in xs]
    for v in team_out.values():
        v["text"] = curly(v["text"])
    for v in player_out.values():
        v["text"], v["next"] = curly(v["text"]), curly(v["next"])
    out = SITE_DATA / "intros"
    out.mkdir(parents=True, exist_ok=True)
    (out / "teams.json").write_text(json.dumps({"generated": today, "teams": team_out}, separators=(",", ":")))
    (out / "players.json").write_text(json.dumps({"generated": today, "players": player_out}, separators=(",", ":")))
    return {"teams": len(team_out), "players": len(player_out), "with_career": sum(1 for k in player_out if k in careers)}
