"""How the matchup looks before the game, in words, and what the win model said, revealed after it.

The user is against sports betting, so the win chance is never shown, exported or saved before the game, in any
file (the repo and the data branch are public). Before the game it only picks the wording of a short note written
from one team's side ("a tough test", "might need a miracle"). After the game the post-game page works the chance out
again from the same pre-game inputs (win_model uses only earlier days) and reveals it with its breakdown.

Each note is written for both teams; the page shows the visitor's own team's version if it is playing, otherwise the
home team's.
"""
import re
import zlib
from functools import lru_cache

import polars as pl

from pipeline.config import FULL_SEASONS, TABLES
from pipeline.export import wording
from pipeline.metrics import win_model

# (upper bound on the team's chance, headline, caveat). Bands are for wording only and are never published.
# Over 2025-26 the model's chances ran from about 38% to 70% for nine games in ten.
BANDS = [
    (0.30, ["A steep climb for {T}", "{T} has their work cut out", "Everything points away from {T}"],
     ["{T} could still pull it off {how}, but they might need a miracle.", "{T}’s best hope is {how_bare}, plus a big night in goal."]),
    (0.42, ["A tough matchup for {T}", "{T} faces a real test", "Not an easy night for {T}"],
     ["{T} can pull it off {how}.", "{T}’s best way through is {how_bare}."]),
    (0.48, ["A slightly uphill night for {T}", "{T} starts a half-step behind", "Close, but {O} has the slight edge"],
     ["{T} can tip it {how}.", "{T}’s best way to tip it is {how_bare}."]),
    (0.52, ["Too close to call", "A genuine toss-up", "Nothing to separate these two"],
     ["{T} can tip it {how}.", "For {T}, the difference could be {how_bare}."]),
    (0.58, ["{T} has a slight edge", "Leaning {T}’s way", "{T} starts a half-step ahead"],
     ["It’s still close, though: {O} can make it interesting {how_o}.", "{O} can still swing it {how_o}."]),
    (0.70, ["{T} should have the upper hand", "The matchup suits {T}", "{T} has a lot going for them"],
     ["{O} can make it interesting {how_o}.", "{O}’s way back in is {how_o_bare}."]),
    (1.01, ["{T} has nearly everything going for them", "A big edge for {T}", "This one sets up well for {T}"],
     ["Anything short of a win would be a surprise, though {O} can make a game of it {how_o}.",
      "{O}’s only real way in is {how_o_bare}, and they would need a near-perfect night."]),
]
# A team's way to win, from its strongest trait against the matching trait of the other team.
ROUTE = {  # trait: (the trait it meets on the other side, "if ..." phrase, bare phrase)
    "volume": ("suppression", "if they keep the shots coming", "piling up shots"),
    "quality": ("qualityAllowed", "if they get good looks", "getting good looks"),
    "pp": ("pk", "if their power play gets going", "a power play that gets going"),
    "pk": ("pp", "if their penalty kill holds up", "a penalty kill that holds up"),
    "suppression": ("volume", "if they keep shots to a minimum", "keeping shots to a minimum"),
    "qualityAllowed": ("quality", "if they keep dangerous shots away", "keeping dangerous shots away"),
}


@lru_cache(maxsize=1)
def model():
    return win_model.fit(win_model.pregame(win_model.team_games(FULL_SEASONS)))


def team_ids(season: int) -> dict[str, int]:
    out = {}
    for s in (season - 1, season):
        g = pl.read_parquet(TABLES / str(s) / "games.parquet")
        out |= {**dict(zip(g["home"], g["home_id"])), **dict(zip(g["away"], g["away_id"]))}
    return out


def morning(season: int, games: list[dict]) -> dict[int, dict]:
    """Each game's home win chance as of the morning of its date, and how each input moved it. `games` are schedule
    rows (id, date, home, away); only games from earlier days feed the ratings, so the figure is the same whether it
    is worked out the morning of the game or after it."""
    ids = team_ids(season)
    abbr = {v: k for k, v in ids.items()}
    played = win_model.team_games(FULL_SEASONS + [season])
    m, out = model(), {}
    try:  # who each team has been dressing: a finished game's own previous lineup, otherwise the latest one
        done = win_model.lineups([season]).select("game_id", "team_id", pl.col("roster").alias("r"))
        upcoming = win_model.next_rosters([season])
        who = _who(season)
    except Exception as e:  # no player ratings yet: chances without lineups
        print(f"lineups unavailable for the morning figure: {e!r}")
        done, upcoming, who = None, None, {}
    for day in sorted({g["date"] for g in games}):
        today = [g for g in games if g["date"] == day and g["home"] in ids and g["away"] in ids]
        if not today:
            continue
        rows = pl.DataFrame([{"game_id": g["id"], "season": season, "date": day, "home_id": ids[g["home"]], "away_id": ids[g["away"]],
                              "home_score": 0, "away_score": 0, "h_xgd": 0.0, "a_xgd": 0.0} for g in today], schema=played.schema)
        df = win_model.pregame(pl.concat([played.filter(pl.col("date") < day), rows]))
        df = df.filter(pl.col("game_id").is_in([g["id"] for g in today]))
        if upcoming is not None:
            df = _with_rosters(df, done, upcoming)
        for r, p in zip(df.iter_rows(named=True), win_model.predict(m, df)):
            out[r["game_id"]] = {"p_home": float(p), "steps": win_model.explain(m, r)}
            if upcoming is not None and done.filter(pl.col("game_id") == r["game_id"]).is_empty():
                out[r["game_id"]]["lineups"] = {abbr[t]: who[t] for t in (r["home_id"], r["away_id"]) if t in who and t in abbr}
    return out


KEY_PLAYER = 0.04  # expected goals a game a missing or returning skater adds to his team's rating to be named


def _who(season: int) -> dict[int, dict]:
    """Per team id, for its next game: the most valuable skater it will be without (still with the club, but out or
    not dressing) and the one news says is coming back, as last names, if either is worth naming."""
    path = TABLES / str(season) / "availability.parquet"
    if not path.exists():
        return {}
    av = pl.read_parquet(path)
    av = av.filter(pl.col("date") == av["date"].max())
    out_now = {(r["team"], r["player_id"]): r["report_status"] for r in av.filter(pl.col("status").is_in(["injured", "suspended", "personal", "scratched", "off_roster"])).iter_rows(named=True)}
    names = {}
    for s in (season - 1, season):
        f = TABLES / str(s) / "players.parquet"
        if f.exists():
            names |= dict(pl.read_parquet(f).select("player_id", "last").iter_rows())
    abbr = {v: k for k, v in team_ids(season).items()}
    from pipeline.metrics import relevancy
    stars = relevancy.standouts(season)
    out = {}
    for tid, u in win_model.next_lineups([season]).items():
        team = abbr.get(tid)
        without = [p for p, v in u["missing"] if v >= KEY_PLAYER and (team, p) in out_now]
        # A star the rating has stopped counting on (out a long time) is still news to a reader.
        without = without or [p for p, t in stars.items() if (team, p) in out_now]
        back = [p for p, v in u["back"] if v >= KEY_PLAYER and p in u["in"]]
        out[tid] = {"without": names.get(without[0]) if without else None, "back": names.get(back[0]) if back else None,
                    "maybe": bool(without) and out_now[(team, without[0])] == "day"}  # day to day: he may still play
    return out


def _with_rosters(df: pl.DataFrame, done: pl.DataFrame, upcoming: dict[int, float]) -> pl.DataFrame:
    """Adds the `roster` term (home minus away): from the games table for games already played, so the figure is
    the same as it was that morning, and from each team's latest lineup for games still to come."""
    known = {(g, t): r for g, t, r in done.iter_rows()}
    side = lambda g, t: known.get((g, t), upcoming.get(t, 0.0))
    return df.with_columns(roster=pl.Series([side(g, h) - side(g, a) for g, h, a in df.select("game_id", "home_id", "away_id").iter_rows()],
                                            dtype=pl.Float64))


CLOSE = (45, 55)  # the reveal calls a game "close to even" only inside this range (user, 2026-10-11: "a little closer")


def _a(pct: int) -> str:
    """"a 72%", but "an 83%", "an 18%", "an 8%"."""
    return f"{'an' if pct in (8, 11, 18) or 80 <= pct <= 89 else 'a'} {pct}%"


def _pick(pool: list[str], *key) -> str:
    return pool[zlib.crc32("-".join(map(str, key)).encode()) % len(pool)]


def _route(fp: dict, team: str, opp: str) -> tuple[str, str]:
    """The team's best way to win: its trait that most outranks the other team's matching trait."""
    gap = lambda k: fp[team]["dims"][k]["blend"]["pct"] - fp[opp]["dims"][ROUTE[k][0]]["blend"]["pct"]
    k = max((k for k in ROUTE if k in fp[team]["dims"] and ROUTE[k][0] in fp[opp]["dims"]), key=gap)
    return ROUTE[k][1], ROUTE[k][2]


def _why(steps: list[dict], team: str, opp: str, home: str, places: dict, lineups: dict | None = None) -> str:
    """Plain reasons, from the model's own breakdown, as one sentence: the reasons that help one side first (biggest
    first, Sora 2026-10-12: chance control matters more than home ice), then "but" and the ones that help the other.
    Clauses about the same team share its name once. When the two are even at out-chancing opponents, that is said
    first on its own. `lineups` (by team abbreviation) names a key skater a side will be without or gets back."""
    s = {x["input"]: x["shift"] * (1 if team == home else -1) for x in steps}  # > 0 helps `team`
    other = {team: opp, opp: team}
    facts = []  # (weight, side it helps, subject, singular predicate, plural predicate)
    chances = s.get("xgd", 0.0)
    if abs(chances) >= 0.025:
        wide = " by a wide margin" if abs(chances) >= 0.08 else ""
        side = team if chances > 0 else opp
        facts.append((abs(chances), side, side, f"has been out-chancing opponents{wide} lately",
                      f"have been out-chancing opponents{wide} lately"))
    roster = abs(s.get("roster", 0.0)) or 0.01
    for side in (team, opp):
        lu = (lineups or {}).get(side) or {}
        if lu.get("without"):
            verb = "may be" if lu.get("maybe") else "will be"
            facts.append((roster, other[side], side, f"{verb} without {lu['without']}", f"{verb} without {lu['without']}"))
        if lu.get("back"):
            facts.append((roster, side, side, f"gets {lu['back']} back", f"get {lu['back']} back"))
    if abs(s.get("b2b", 0.0)) >= 0.005:
        tired = opp if s["b2b"] > 0 else team
        facts.append((abs(s["b2b"]), other[tired], tired, "is on the second night of a back-to-back",
                      "are on the second night of a back-to-back"))
    facts.append((abs(s.get("home", 0.0)), home, home, "has home-ice advantage", "have home-ice advantage"))
    facts.sort(key=lambda f: -f[0])
    first = facts[0][1]
    groups = [[f for f in facts if f[1] == first], [f for f in facts if f[1] != first]]
    for g in groups:  # one team's clauses stay together, in the order of that team's biggest
        lead = {}
        for f in g:
            lead.setdefault(f[2], len(lead))
        g.sort(key=lambda f: lead[f[2]])

    def clauses(group: list, after: str | None) -> str:
        many = lambda t: places[t].lower().startswith("the ")  # "the Rangers have", not "has"
        parts = []  # one phrase per team: "Edmonton has been ... lately and has home-ice advantage"
        for f in group:
            if parts and parts[-1][0] == f[2]:
                parts[-1][1] += " and " + (f[4] if parts[-1][1].startswith("they ") or many(f[2]) else f[3])
            elif not parts and f[2] == after:
                parts.append([f[2], "they " + f[4]])  # "..., but they are on the second night"
            else:
                parts.append([f[2], f"{places[f[2]]} {f[4] if many(f[2]) else f[3]}"])
        out = [p[1] for p in parts]
        if len(out) == 1:
            return out[0]
        return ", ".join(out[:-1]) + (", and " if len(out) > 2 or " and " in out[0] else " and ") + out[-1]

    text = clauses(groups[0], None)
    if groups[1]:
        text += ", but " + clauses(groups[1], groups[0][-1][2])
    text += "."
    if abs(chances) < 0.025:
        text = "The two have been about even at out-chancing opponents lately. " + text[0].upper() + text[1:]
    return text


def _join(why: str, caveat: str) -> str:
    """The reasons, then the verdict, as separate sentences."""
    return f"{why} {caveat}"


_PLURAL = {"has": "have", "is": "are", "faces": "face", "starts": "start", "gets": "get"}


def _agree(text: str) -> str:
    """"NY Rangers" reads badly as a place, so the New York teams go by nickname, and nicknames are plural."""
    text = re.sub(r"\b([Tt]he (?:Rangers|Islanders)) (has|is|faces|starts|gets)\b", lambda m: f"{m[1]} {_PLURAL[m[2]]}", text)
    text = re.sub(r"(Rangers|Islanders)’s\b", r"\1’", text)
    return text[0].upper() + text[1:]


def notes(gid: int, away: str, home: str, chance: dict, fp: dict, places: dict) -> dict[str, dict]:
    """The pre-game note for each team. Holds words only, never the chance itself."""
    places = {t: "the " + p[3:] if p.startswith("NY ") else p for t, p in places.items()}
    out = {}
    lineups = chance.get("lineups") or {}
    for team, opp in ((home, away), (away, home)):
        p = chance["p_home"] if team == home else 1 - chance["p_home"]
        _, heads, caveats = next(b for b in BANDS if p < b[0])
        how, how_bare = _route(fp, team, opp)
        how_o, how_o_bare = _route(fp, opp, team)
        w = {"T": places[team], "O": places[opp], "how": how, "how_bare": how_bare, "how_o": how_o, "how_o_bare": how_o_bare}
        out[team] = {"head": _agree(_pick(heads, gid, team, "head").format(**w)),
                     "body": _agree(_join(_why(chance["steps"], team, opp, home, places, lineups), _agree(_pick(caveats, gid, team, "caveat").format(**w))))}
    return out


def reveal(season: int, final: list[dict], places: dict) -> dict[int, dict]:
    """After the game: the chance we gave each team that morning and at puck drop (with tonight's lineup), how each
    input moved it, and a sentence for each team's side."""
    ids = team_ids(season)
    say = wording.places(places)
    m = model()
    df = win_model.pregame(win_model.team_games(FULL_SEASONS + [season])).filter(pl.col("season") == season)
    try:
        df = win_model.with_lineups(df, win_model.lineups([season]))
    except Exception:  # no player ratings to value lineups with: show the morning figure only
        df = df.with_columns(lineup=pl.lit(None, pl.Float64))
    rows = {r["game_id"]: r for r in df.iter_rows(named=True)}
    named = _named_changes(season)
    morning_p = dict(zip(df["game_id"], win_model.predict(m, df)))
    puck = dict(zip(df["game_id"], win_model.predict(m, df.with_columns(pl.col("lineup").fill_null(0.0)), puck_drop=True)))
    out = {}
    for g in final:
        r = rows.get(g["id"])
        if r is None or g["home"] not in ids:
            continue
        has_lineup = r.get("lineup") is not None
        steps = win_model.explain(m, r if has_lineup else {**r, "lineup": None})
        ph = float(morning_p[g["id"]])
        home_won = g["hs"] > g["as"]
        sides = {}
        for team, opp, p, won in ((g["home"], g["away"], ph, home_won), (g["away"], g["home"], 1 - ph, not home_won)):
            pct, T, O = round(100 * p), say[team], say[opp]
            # bands on the shown figure, so "60%" is never also "close to even"; 45-55% is a coin flip, 40-44% and 56-59%
            # a lean, under 40% or 60%+ a clear call
            if won:
                head = (f"We gave {T} only {_a(pct)} chance, and {T} proved us wrong." if pct < 40 else
                        f"We had {T} as slight underdogs ({pct}%), and {T} pulled it off." if pct < CLOSE[0] else
                        f"We had this close to even ({pct}% for {T}), and {T} came through." if pct <= CLOSE[1] else
                        f"We leaned {T}’s way ({pct}%), and {T} came through." if pct < 60 else
                        f"We gave {T} {_a(pct)} chance, and {T} delivered.")
            else:
                head = (f"We gave {T} only {_a(pct)} chance, and it went the way we feared." if pct < 40 else
                        f"We had {T} as slight underdogs ({pct}%), and it went {O}’s way." if pct < CLOSE[0] else
                        f"We had this close to even ({pct}% for {T}), and it went {O}’s way." if pct <= CLOSE[1] else
                        f"We leaned {T}’s way ({pct}%), but {O} had other ideas." if pct < 60 else
                        f"We gave {T} {_a(pct)} chance, but {O} had other ideas.")
            sides[team] = {"head": wording.tidy(head), "chance": pct}
        out[g["id"]] = {"p_home": round(ph, 4), "p_home_puck": round(float(puck[g["id"]]), 4) if has_lineup else None,
                        "steps": [{**s, "shift": round(s["shift"], 4)} for s in steps], "sides": sides,
                        # What each step was made of, per team, so the page can say what every adjustment was for.
                        "inputs": {t: {"xgd": round(r[f"{k}_xgd"], 2), "b2b": int(r[f"{k}_b2b"]),
                                       **{c: round(r[f"{k}_{c}"], 3) for c in ("roster", "lineup") if r.get(f"{k}_{c}") is not None}}
                                   for t, k in ((g["home"], "h"), (g["away"], "a"))},
                        "players": {t: named(g["id"], t, g["date"]) for t in (g["home"], g["away"])}}
    return out


NAMED = 0.03  # expected goals a game a skater's absence or late change must be worth to be named after the game
BIG = 0.06  # ... and to be called a big loss (about the top tenth of absences)


def _named_changes(season: int):
    """For the post-game reveal: which skaters the lineup steps were about, valued with the same player ratings that
    moved the chance. `missing`: usual players the morning lineup was without (still with the club: dressed for it this
    season or on its roster that day); `out` / `in`: who sat out or came in after the morning. Most valuable first."""
    try:
        who = win_model.lineup_names([season])
    except Exception:
        return lambda gid, team, day: None
    names: dict[int, str] = {}
    for s in (season - 1, season):
        f = TABLES / str(s) / "players.parquet"
        if f.exists():
            names |= {q: f"{a} {b}" for q, a, b in pl.read_parquet(f).select("player_id", "first", "last").unique("player_id", keep="last").iter_rows()}
    path = TABLES / str(season) / "availability.parquet"
    held = set()
    if path.exists():
        av = pl.read_parquet(path).filter(pl.col("status") != "no_club")
        names |= {q: f"{a} {b}" for q, a, b in av.select("player_id", "first", "last").unique("player_id").iter_rows() if q not in names}
        held = set(av.select("date", "team", "player_id").iter_rows())
    ids = team_ids(season)
    from pipeline.metrics import relevancy
    stars = relevancy.standouts(season)
    f = TABLES / str(season) / "player_game.parquet"
    dressed = set(pl.read_parquet(f).select("game_id", "team_id", "player_id").iter_rows()) if f.exists() else set()

    def one(gid: int, team: str, day: str):
        w = who.get((gid, ids.get(team)))
        if not w:
            return None
        pick = lambda xs: [{"id": int(q), "name": names[q], "xg": round(float(v), 3), "big": bool(v >= BIG)}
                           for q, v, *_ in xs if v >= NAMED and q in names][:3]
        missing = [x for x in w["missing"] if x[2] or (day, team, x[0]) in held]
        res = {"missing": pick(missing), "out": pick(w["late_out"]), "in": pick(w["late_in"])}
        # Stars who missed the game but were not named above: the rating had stopped counting on them ("priced", out
        # a long time, or never seen with this club) or his usual night is worth little by the ratings ("small").
        # Readers still want to hear he was out.
        shown = {x["id"] for k in res for x in res[k]}
        res["stars"] = [{"id": int(q), "name": names[q], "why": "small" if w["worth"].get(q, NAMED) < NAMED else "priced"} for q in stars
                        if q not in shown and q in names and (day, team, q) in held and (gid, ids.get(team), q) not in dressed][:2]
        return res
    return one
