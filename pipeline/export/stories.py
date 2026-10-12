"""The short version: a plain-English story at the top of every preview and post-game page.

Sora's reviewer asked for a numbers-light read of each game: before it, what to expect and who to watch; after it,
what happened and what it means. Every sentence comes from a fixed rule over our own tables (no free text), and the
only numbers allowed in the prose are the counts a fan says out loud: scores, records, streaks, goals, games. Ranks
and rates are told in words, and every figure stays in the call cards below.

A story is {kicker, head, paras, list_head, items}, where each item is {tag, text}. A preview's first paragraph ends
with the matchup's lean, which is written from one team's side (the outlook note); `lean` holds it for each team and
the page shows the visitor's own team's version, or the home team's.

Narratives found (preview):
  last meeting  the earlier games between the two this season
  form          winning and losing streaks, the latest result if it was a rout
  old team      a player facing a club he played for, and whether it is the first time since he left
  owns them     a player's point streak or scoring record against tonight's opponent since 2023-24
  hot streak    a point streak this season
  slow start    a regular scorer last season with no goals yet
  missing       a regular who sat out the team's latest game or games ("has missed", never "injured")
  still out     a regular from last season who hasn't played this season (the lines' absence note)
  milestone     a skater who dressed last game and is close to a round number: his 500th, 600th... NHL game next,
                or within two of 100, 200... goals, 200+ assists or 300+ points (complete NHL careers only)
  bad blood     the last meeting this season had a line brawl, a big scrum, two or more fights, an ejection or a fight
History starts in 2023-24, so records against a team say "since 2023-24".

Everything a preview story reads is from before the game's date, so a rebuilt preview tells it as a live one would.
"""
import json
import zlib
from datetime import date as Date
from functools import lru_cache

import polars as pl

from pipeline.config import CURRENT_SEASON, FULL_SEASONS, REGULAR, SITE_DATA, TABLES
from pipeline.export import penalties, wording

SEASONS = [*FULL_SEASONS, CURRENT_SEASON]
WORDS = ["no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve"]
PERIODS = {1: "first", 2: "second", 3: "third"}
LIKELY = 0.6  # how sure starters.py must be of the visitors' goalie before his record on tonight's ice is told (right about 7 in 10)
MAX_ITEMS, MIN_ITEMS, MAX_PER_TAG = 4, 2, 2


def num(n: int) -> str:
    return WORDS[n] if 0 <= n < len(WORDS) else str(n)


def _pick(pool: list[str], *key) -> str:
    return pool[zlib.crc32("-".join(map(str, key)).encode()) % len(pool)]


def _secs(toi: str | None) -> int:
    """Seconds in a "mm:ss" clock string (0 when missing)."""
    if not toi:
        return 0
    m, _, sec = toi.partition(":")
    return int(m) * 60 + int(sec or 0)


def _cap(t: str) -> str:
    return t[:1].upper() + t[1:]


def _and(xs: list[str]) -> str:
    return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " and " + xs[-1]


def weekday(d: str) -> str:
    return Date.fromisoformat(d[:10]).strftime("%A")


# --- history -------------------------------------------------------------------------------------------------------

@lru_cache(maxsize=1)
def book() -> tuple[pl.DataFrame, pl.DataFrame, dict[int, str]]:
    """Finished regular-season games since 2023-24, every skater's line in them (team, opponent, points), and names."""
    games, rows, names = [], [], []
    for s in SEASONS:
        d = TABLES / str(s)
        if not (d / "games.parquet").exists():
            continue
        g = pl.read_parquet(d / "games.parquet").filter((pl.col("game_type") == REGULAR) & pl.col("state").is_in(["OFF", "FINAL"]))
        games.append(g.select("game_id", "season", "date", "home", "away", "home_id", "away_id", "home_score", "away_score", "last_period"))
        if (d / "player_game.parquet").exists():
            rows.append(pl.read_parquet(d / "player_game.parquet", columns=["game_id", "player_id", "team_id", "pos", "sec", "g", "a1", "a2"]))
        if (d / "players.parquet").exists():
            names.append(pl.read_parquet(d / "players.parquet", columns=["game_id", "player_id", "first", "last"]))
    G = pl.concat(games).sort("date", "game_id")
    P = (pl.concat(rows).join(G.select("game_id", "season", "date", "home", "away", "home_id"), on="game_id")
         .with_columns(team=pl.when(pl.col("team_id") == pl.col("home_id")).then(pl.col("home")).otherwise(pl.col("away")),
                       opp=pl.when(pl.col("team_id") == pl.col("home_id")).then(pl.col("away")).otherwise(pl.col("home")),
                       pts=pl.col("g") + pl.col("a1") + pl.col("a2"))
         .drop("home", "away", "home_id").sort("date", "game_id"))
    N = pl.concat(names).sort("game_id").group_by("player_id").agg(pl.col("first").last(), pl.col("last").last())
    # two teammates with the same name (Vancouver's Elias Petterssons) get their numbers
    return G, P, {r["player_id"]: f"{r['first']} {r['last']}" for r in N.iter_rows(named=True)} | wording.number_clashes(CURRENT_SEASON, wording.full)


def results(G: pl.DataFrame, team: str, season: int) -> list[dict]:
    """A team's finished games this season in order: win/loss, scores, opponent, home or away, how it ended."""
    out = []
    for r in G.filter((pl.col("season") == season) & ((pl.col("home") == team) | (pl.col("away") == team))).iter_rows(named=True):
        home = r["home"] == team
        us, them = (r["home_score"], r["away_score"]) if home else (r["away_score"], r["home_score"])
        out.append({"win": us > them, "us": us, "them": them, "opp": r["away"] if home else r["home"], "home": home,
                    "end": r["last_period"], "date": r["date"], "game_id": r["game_id"]})
    return out


def streak(res: list[dict]) -> tuple[str, int]:
    """("won", n) or ("winless", n) for the run the team is on."""
    if not res:
        return "", 0
    kind, n = res[-1]["win"], 0
    for r in reversed(res):
        if r["win"] != kind:
            break
        n += 1
    return ("won" if kind else "winless"), n


def record(res: list[dict]) -> str:
    w = sum(r["win"] for r in res)
    otl = sum(not r["win"] and r["end"] != "REG" for r in res)
    return f"{w}-{len(res) - w - otl}-{otl}"


def score_text(r: dict, win: bool = False) -> str:
    """ "5-2", or "3-2 in overtime" / "in a shootout", always winner first; with `win`, "3-2 overtime win"."""
    a, b = max(r["us"], r["them"]), min(r["us"], r["them"])
    if win:
        return f"{a}-{b} " + {"OT": "overtime win", "SO": "shootout win"}.get(r["end"], "win")
    tail = {"OT": " in overtime", "SO": " in a shootout"}.get(r["end"], "")
    return f"{a}-{b}{tail}"


class Words:
    """How sentences name teams: "the Hurricanes" (plural verbs), "Carolina" (a place), "Raleigh"-style home cities
    are not known, so places stand in for them."""

    def __init__(self, places: dict[str, str]):
        from pipeline.export import previews
        self.places = places
        self.nick = previews.names(places)

    def the(self, t: str) -> str:
        return f"the {self.nick.get(t, t)}"

    def poss(self, t: str) -> str:
        n = self.the(t)
        return n + ("’" if n.endswith("s") else "’s")

    def place(self, t: str) -> str:
        p = self.places.get(t, t)
        return self.the(t) if p.startswith("NY ") else p  # "NY Rangers" reads badly as a place


# --- preview -------------------------------------------------------------------------------------------------------

def _short(pid: int, P: pl.DataFrame, N: dict[int, str], season: int) -> str:
    """His last name, or his full name when a teammate this season shares it (the Tkachuks, the two Elias Petterssons)."""
    surname = lambda p: N.get(p, "").split(" (#")[0].split(" ", 1)[-1]
    teams = P.filter((pl.col("season") == season) & (pl.col("player_id") == pid))["team"].unique().to_list()
    mates = P.filter((pl.col("season") == season) & pl.col("team").is_in(teams) & (pl.col("player_id") != pid))["player_id"].unique().to_list()
    return N.get(pid, "") if any(surname(m) == surname(pid) for m in mates) else surname(pid)


def _current_team(P: pl.DataFrame, season: int) -> dict[int, str]:
    rows = P.filter(pl.col("season") == season).group_by("player_id").agg(pl.col("team").last())
    return dict(zip(rows["player_id"], rows["team"]))


def preview_facts(away: str, home: str, before: str, W: Words, season: int = CURRENT_SEASON, likely: dict | None = None) -> dict:
    """Everything the preview story can say, from games before `before` (a date)."""
    G, P, N = book()
    G, P = G.filter(pl.col("date") < before[:10]), P.filter(pl.col("date") < before[:10])
    cur = _current_team(P, season)
    out = {"meetings": [], "form": {}, "items": [], "heads": []}
    # last meetings this season
    for r in results(G, home, season):
        if r["opp"] == away:
            out["meetings"].append(r)
    for t in (away, home):
        res = results(G, t, season)
        out["form"][t] = {"res": res, "streak": streak(res), "record": record(res)}
    items = out["items"]
    for t, o in ((away, home), (home, away)):
        roster = [p for p, team in cur.items() if team == t]
        mine = P.filter(pl.col("player_id").is_in(roster))
        this = mine.filter((pl.col("season") == season) & (pl.col("team") == t))
        # hot streaks
        for pid, g in this.group_by("player_id"):
            pts = g.sort("date")["pts"].to_list()
            n = 0
            for x in reversed(pts):
                if x <= 0:
                    break
                n += 1
            if n >= 4:
                text = (f"{N[pid[0]]} has a point in every game this season." if n == len(pts) else f"{N[pid[0]]} has a point in {num(n)} straight games.")
                items.append({"tag": "Hot streak", "text": text, "score": 60 + n + sum(pts) / 100, "pid": pid[0]})
        # slow start: scored often last season, nothing yet
        last = mine.filter(pl.col("season") == season - 1).group_by("player_id").agg(gp=pl.len(), g=pl.col("g").sum())
        last = last.filter((pl.col("gp") >= 40) & (pl.col("g") / pl.col("gp") >= 0.35))
        now = this.group_by("player_id").agg(gp=pl.len(), g=pl.col("g").sum())
        cold = last.join(now, on="player_id", suffix="_now").filter((pl.col("gp_now") >= 4) & (pl.col("g_now") == 0))
        if cold.height:
            pid = cold.sort(pl.col("g") / pl.col("gp"), descending=True)["player_id"][0]
            items.append({"tag": "Slow start", "text": f"{N[pid]} is still looking for his first goal of the season.", "score": 50, "pid": pid})
        # records against tonight's opponent since 2023-24
        vs = mine.filter(pl.col("opp") == o).sort("date")
        cands = []
        for pid, g in vs.group_by("player_id"):
            pid = pid[0]
            pts, goals, gp = g["pts"].to_list(), int(g["g"].sum()), g.height
            n = 0
            for x in reversed(pts):
                if x <= 0:
                    break
                n += 1
            elsewhere = g.filter(pl.col("team") != t)
            mostly = elsewhere["team"].mode()[0] if elsewhere.height * 2 > gp else None
            tail = f", most of them with {W.place(mostly)}" if mostly else ""
            if n >= 5:
                extra = f", and {int(sum(pts))} points in his last {num(gp) if gp <= 12 else gp} against them" if sum(pts) >= 1.5 * gp else ""
                cands.append({"tag": "Owns them", "score": 70 + n, "pid": pid, "streak": n,
                              "text": f"{N[pid]} has a point in {num(n)} straight games against {W.place(o)}{extra}."})
            elif gp >= 6 and goals / gp >= 0.6:
                cands.append({"tag": "Haunts them", "score": 68 + goals / gp * 10, "pid": pid,
                              "text": f"{N[pid]} has {num(goals)} goals in {num(gp) if gp <= 12 else gp} games against {W.place(o)} since 2023-24{tail}."})
        cands.sort(key=lambda c: -c["score"])
        items += cands[:1]
        if cands and cands[0].get("streak", 0) >= 7:
            out["heads"].append((80 + 1.5 * cands[0]["streak"], f"{_short(cands[0]['pid'], P, N, season)}’s favourite opponent"))
        # old teams: the club he played for before this one
        olds = []
        for pid, g in mine.sort("date").group_by("player_id", maintain_order=True):
            pid = pid[0]
            teams = g["team"].to_list()
            if teams[-1] != t:
                continue
            prev = next((x for x in reversed(teams) if x != t), None)
            if prev != o:
                continue
            for_them = g.filter(pl.col("team") == o)
            left = for_them["date"].max()
            met = g.filter((pl.col("date") > left) & (pl.col("opp") == o)).height
            olds.append((for_them.height, pid, met == 0))
        olds.sort(reverse=True)
        if olds:
            n_old, pid, first = olds[0]
            lastname = _short(pid, P, N, season)
            if first and n_old >= 40:
                text = f"{N[pid]} faces {W.place(o)} for the first time since leaving."
                where = W.place(o)
                out["heads"].append((85 + min(n_old, 300) / 30 + 2 * (home == o), f"{lastname} returns to {where}" if home == o else f"{lastname} meets his old team"))
            else:
                text = f"{N[pid]} faces his old team, {W.place(o)}."
            items.append({"tag": "Old team", "text": text, "score": (80 if first else 45) + min(n_old, 300) / 30, "pid": pid})
            others = [N[p] for _, p, _ in olds[1:4]]
            if others:
                items.append({"tag": "Old team", "text": f"{_and(others)} also {'played' if len(others) > 1 else 'played'} for {W.place(o)} before.",
                              "score": 30, "pid": None})
        # missing: a regular this season who sat out the latest game(s)
        res = out["form"][t]["res"]
        if len(res) >= 3:
            team_games = [r["game_id"] for r in res]
            per = this.group_by("player_id").agg(gp=pl.len(), toi=pl.col("sec").mean(), pos=pl.col("pos").first(),
                                                 last=pl.col("game_id").max())
            for pos, top in (("F", 6), ("D", 4)):
                grp = per.filter(pl.col("pos").is_in(["C", "L", "R"]) if pos == "F" else pl.col("pos") == "D").sort("toi", descending=True).head(top)
                for r in grp.iter_rows(named=True):
                    missed = len(team_games) - 1 - team_games.index(r["last"])
                    if missed >= 1 and r["gp"] >= 2:
                        when = "latest game" if missed == 1 else f"last {num(missed)} games"
                        items.append({"tag": "Missing", "score": 72 + (top - grp["player_id"].to_list().index(r["player_id"])),
                                      "pid": r["player_id"], "text": f"{N[r['player_id']]} has missed {W.poss(t)} {when}."})
        if res:
            items += _milestones(t, res[-1], P, N, before)
    item = _goalie_on_the_road(away, home, before, W, N, (likely or {}).get(away))
    if item:
        items.append(item)
    return out


MILESTONE_NEAR = 2  # goals, assists or points away from a round hundred


@lru_cache(maxsize=1)
def _careers() -> dict[int, list[tuple[str, int, int]]]:
    """Every skater with a complete NHL career on file: his regular-season games as (date, goals, points), oldest first."""
    from pipeline.ingest import careers
    out = {}
    for pid, c in careers.load_all().items():
        if (c.get("landing") or {}).get("position") == "G":
            continue
        out[pid] = sorted((x["gameDate"], int(x.get("goals") or 0), int(x.get("points") or 0)) for log in c["logs"].values() for x in log)
    return out


def milestone(games: list[tuple[str, int, int]], before: str) -> tuple[float, str] | None:
    """(score, phrase) for the round number a skater is closest to, from his games before `before`, e.g. "is one goal
    from 400" or "is set to play his 800th NHL game"; None if nothing is close."""
    past = [x for x in games if x[0] < before[:10]]
    gp, g, p = len(past), sum(x[1] for x in past), sum(x[2] for x in past)
    found = []
    if gp + 1 >= 500 and (gp + 1) % 100 == 0:
        found.append((72 + (gp + 1) / 200, f"is set to play his {gp + 1:,}th NHL game"))
    for what, have, low, base in (("goal", g, 100, 72), ("assist", p - g, 200, 66), ("point", p, 300, 70)):
        nxt = (have // 100 + 1) * 100
        away = nxt - have
        if nxt >= low and away <= MILESTONE_NEAR:
            unit = what if away == 1 else what + "s"
            found.append((base + nxt / 200 - away, f"is {num(away)} {unit} from {nxt:,}"))
    return max(found) if found else None


def _games_between(t: str, after: str, before: str) -> int:
    """How many of a team's scheduled games fall after one date (local) and start before a time (UTC, as in `start`)."""
    p = SITE_DATA / "schedule.json"
    if not p.exists():
        return 0
    return sum(1 for g in json.loads(p.read_text())["games"]
               if t in (g["home"], g["away"]) and (g.get("date") or g["start"][:10]) > after and g["start"] < before)


def _milestones(t: str, last: dict, P: pl.DataFrame, N: dict[int, str], before: str) -> list[dict]:
    """One item per skater who dressed in the team's last game and is close to a milestone. Only for the team's next
    game: a game further ahead would count from a total another game may change."""
    if _games_between(t, last["date"], before):
        return []
    last_game = last["game_id"]
    out = []
    car = _careers()
    for pid in P.filter((pl.col("game_id") == last_game) & (pl.col("team") == t))["player_id"].to_list():
        got = milestone(car[pid], before) if pid in car and pid in N else None
        if got:
            out.append({"tag": "Milestone", "text": f"{N[pid]} {got[1]}.", "score": got[0], "pid": pid})
    return out


def _goalie_on_the_road(away: str, home: str, before: str, W: Words, N: dict[int, str], likely: list | None) -> dict | None:
    """The visitors' likely starting goalie (pipeline/metrics/starters.py, when it gives him LIKELY or better) and his
    record on tonight's ice, when it makes a story: never won there, a long winless run there, or a run of wins."""
    from pipeline.metrics import venues
    if not likely or likely[0][1] < LIKELY:
        return None
    pid = likely[0][0]
    if not N.get(pid):
        return None
    try:
        where = f"on {W.poss(home)} ice" if W.places[home].startswith("NY ") else f"in {W.place(home)}"
        story = venues.storyline(pid, N[pid], home, where, before)
    except Exception as e:  # no saved boxscores or career files: no goalie storyline
        print(f"goalie records unavailable: {e!r}")
        return None
    return {"tag": "In net", "pid": pid, **story} if story else None


def _still_out(notes: list[str], team: str, N: dict[int, str], season: int) -> list[dict]:
    """The lines' absence notes ("S. Jarvis has not played this season. ...") as short items, with full names."""
    G, P, _ = book()
    out = []
    last = P.filter((pl.col("season") == season - 1) & (pl.col("team") == team)).select("player_id").unique()["player_id"].to_list()
    for n in notes:
        if " has not played this season" not in n:
            continue
        short = n.split(" has not played this season")[0]
        first, _, lastname = short.partition(". ")
        name = next((N[p] for p in last if N.get(p, "").endswith(" " + lastname) and N[p].startswith(first)), short)
        out.append({"tag": "Still out", "text": f"{name} hasn’t played yet this season.", "score": 55, "pid": None})
    return out


def _form_sentence(t: str, f: dict, W: Words, meeting: bool) -> str | None:
    kind, n = f["streak"]
    res = f["res"]
    if not res:
        return None
    lastg = res[-1]
    rout = lastg["win"] and lastg["us"] - lastg["them"] >= 4
    tail = f", the last a {score_text(lastg)} rout of {W.place(lastg['opp'])}" if rout else ""
    if kind == "won" and n >= 3:
        return f"{_cap(W.the(t))} have won {num(n)} straight{tail}."
    if kind == "winless" and n >= 3:
        return f"{_cap(W.the(t))} haven’t won in {num(n)}."
    if kind == "won" and n >= 2:
        return f"{_cap(W.the(t))} have won two in a row{tail}."
    return None


def preview_story(gid: int, away: str, home: str, start: str, places: dict, claims: list[dict],
                  outlook: dict | None, lines: dict, season: int = CURRENT_SEASON, likely: dict | None = None) -> dict:
    """The story for one preview, from data before the game and the calls already chosen for it."""
    W = Words(places)
    facts = preview_facts(away, home, start, W, season, likely)
    _, _, N = book()
    sents = []
    # the last meeting(s)
    m = facts["meetings"]
    if len(m) == 1:
        r = m[0]
        winner = home if r["win"] else away
        where = W.place(r["home"] and home or away) if r["home"] else W.place(away)
        place = W.place(home) if r["home"] else W.place(away)
        if winner == away and r["home"]:
            sents.append(f"{_cap(W.the(away))} already came to {place} once this season and left with a {score_text(r, win=True)}.")
        else:
            sents.append(f"{_cap(W.the(winner))} won the first meeting this season, {score_text(r)} in {place}.")
        if r["home"]:
            facts["heads"].append((70, f"Round two in {W.place(home)}"))
        else:
            facts["heads"].append((65, "Round two"))
    elif len(m) > 1:
        wins = sum(r["win"] for r in m)
        if wins in (0, len(m)):
            w = home if wins else away
            sents.append(f"{_cap(W.the(w))} have won all {num(len(m))} meetings this season.")
        else:
            sents.append(f"The teams have split {num(len(m))} meetings this season.")
    # form
    form = {t: _form_sentence(t, facts["form"][t], W, bool(m)) for t in (home, away)}
    if any(form.values()):
        for t in (home, away):
            if form[t]:
                sents.append(form[t])
            elif facts["form"][t]["res"]:
                sents.append(f"{_cap(W.the(t))} are {facts['form'][t]['record']}.")
    else:
        fa, fh = facts["form"][away], facts["form"][home]
        if fa["res"] and fh["res"]:
            sents.append(f"{_cap(W.the(away))} come in at {fa['record']}, {W.the(home)} at {fh['record']}.")
    for t, f in facts["form"].items():
        k, n = f["streak"]
        if k == "won" and n >= 4:
            facts["heads"].append((60 + n, f"{_cap(W.the(t))} come in hot"))
        if k == "winless" and n >= 4:
            facts["heads"].append((58 + n, f"{_cap(W.the(t))} need a win"))
    # what to look for: the gist of the top calls
    look, kinds = [], set()
    for c in claims:  # one per kind of call: two duels or two edges in a row read the same
        if c.get("gist") and c["kind"] not in kinds and len(look) < 2:
            look.append(c["gist"])
            kinds.add(c["kind"])
    blood = penalties.bad_blood(m[-1]["game_id"], season, N) if m else None
    items = sorted(facts["items"] + ([blood] if blood else []) + _still_out([n["text"] for n in lines.get(away, {}).get("notes", [])], away, N, season)
                   + _still_out([n["text"] for n in lines.get(home, {}).get("notes", [])], home, N, season), key=lambda i: -i["score"])
    picked, per, seen = [], {}, set()
    for i in items:
        if per.get(i["tag"], 0) >= MAX_PER_TAG or (i.get("pid") and i["pid"] in seen) or len(picked) >= MAX_ITEMS:
            continue
        per[i["tag"]] = per.get(i["tag"], 0) + 1
        seen.add(i.get("pid"))
        picked.append({"tag": i["tag"], "text": i["text"]})
    for t in (away, home):  # always at least two: each team's leading scorer
        if len(picked) >= MIN_ITEMS:
            break
        lead = _leader(t, start, season)
        if lead and lead[0] not in seen:
            picked.append({"tag": "Leads the way", "text": f"{N[lead[0]]} leads {W.the(t)} with {num(lead[1])} {'point' if lead[1] == 1 else 'points'}."})
    heads = sorted(facts["heads"], key=lambda h: -h[0])
    head = heads[0][1] if heads else (claims[0]["head"] if claims else f"{W.place(away)} at {W.place(home)}")
    lean = {t: v["body"] for t, v in (outlook or {}).items()}
    paras = [" ".join(sents)] if sents else []
    if look:
        paras.append(" ".join(look))
    return {"kicker": "Before the game", "head": head, "paras": paras, "lean": lean, "list_head": "Who to watch", "items": picked}


def _leader(t: str, before: str, season: int) -> tuple[int, int] | None:
    _, P, _ = book()
    p = P.filter((pl.col("season") == season) & (pl.col("team") == t) & (pl.col("date") < before[:10]))
    if p.is_empty():
        return None
    r = p.group_by("player_id").agg(pl.col("pts").sum()).sort("pts", descending=True).row(0)
    return (r[0], int(r[1])) if r[1] > 0 else None


# --- post-game -----------------------------------------------------------------------------------------------------

def _goals(gid: int, season: int, home_id: int) -> list[dict]:
    """The game's goals in order (shootout left out): period, seconds into it, team side, scorer and helpers, strength."""
    ev = pl.read_parquet(TABLES / str(season) / "events.parquet").filter((pl.col("game_id") == gid) & (pl.col("type") == "goal"))
    out, hs, as_ = [], 0, 0
    for r in ev.sort("sort").iter_rows(named=True):  # `sec` counts from the opening faceoff; scores are kept here
        if r["period_type"] == "SO":
            continue
        code = str(r["situation_code"] or "1551")
        home = r["team_id"] == home_id
        a_sk, h_sk = int(code[1]), int(code[2])
        mine, theirs = (h_sk, a_sk) if home else (a_sk, h_sk)
        empty = (code[3] == "0") if not home else (code[0] == "0")  # the other side's goalie off
        strength = "en" if empty else "pp" if mine > theirs else "sh" if mine < theirs else "ev"
        hs, as_ = hs + home, as_ + (not home)
        out.append({"period": r["period"], "sec": r["sec"] - 1200 * (r["period"] - 1), "home": home, "p1": r["p1"], "p2": r["p2"], "p3": r["p3"],
                    "strength": strength, "hs": hs, "as": as_,
                    # the other side had pulled its goalie for an extra attacker (more skaters than the scorers), not
                    # just left the net during a delayed penalty
                    "extra": empty and theirs > mine})
    return out


def _when(g: dict) -> str:
    p, s = g["period"], g["sec"]
    t = f"{s} seconds" if s < 60 else f"{s // 60}:{s % 60:02d}"
    if p > 3:
        return f"{t} into overtime" if s < 180 else "in overtime"
    if p == 1 and s < 120:
        return f"{t} in"
    part = "early in" if s < 400 else "midway through" if s < 800 else "late in"
    return f"{part} the {PERIODS[p]}"


def recap_story(r: dict, places: dict, season: int = CURRENT_SEASON) -> dict | None:
    """The story for a finished game, from its post-game JSON (r), its Swing scores and the season's tables."""
    W = Words(places)
    G, P, N = book()
    gid, away, home = r["game_id"], r["away"], r["home"]
    row = G.filter(pl.col("game_id") == gid)
    if row.is_empty():
        return None
    home_id = row["home_id"][0]
    goals = _goals(gid, season, home_id)
    winner = home if r["score"][home] > r["score"][away] else away
    loser = away if winner == home else home
    ws, ls = r["score"][winner], r["score"][loser]
    end = r.get("end", "REG")
    side = lambda g: home if g["home"] else away
    last = lambda pid: _short(pid, P, N, season)
    said: set[int] = set()

    def name(pid: int) -> str:
        if pid in said:
            return last(pid)
        said.add(pid)
        return N.get(pid, "")
    # scorers
    by = {}
    for g in goals:
        by.setdefault(g["p1"], []).append(g)
    hat = [(pid, gs) for pid, gs in by.items() if len(gs) >= 3]
    twice = [pid for pid, gs in by.items() if len(gs) == 2 and side(gs[0]) == winner]
    # lead path
    lead_max = {home: 0, away: 0}
    for g in goals:
        d = g["hs"] - g["as"]
        lead_max[home] = max(lead_max[home], d)
        lead_max[away] = max(lead_max[away], -d)
    comeback = lead_max[loser] >= 2
    gl = {x["team"]: x for x in r.get("goalies", []) if x.get("started")}
    rb = r.get("replays") or {}
    share = (rb.get("share") or {}).get(winner)
    # headline
    heads = []
    if hat:
        pid, gs = max(hat, key=lambda x: len(x[1]))
        kinds = {g["strength"] for g in gs}
        if len(gs) >= 4:
            heads.append((100, f"{last(pid)} scores {num(len(gs))}"))
        elif {"ev", "pp", "sh"} <= kinds:
            heads.append((100, f"{last(pid)}’s hat trick, one at every strength"))
        else:
            heads.append((95, f"A hat trick for {last(pid)}"))
    wg = gl.get(winner)
    if ls == 0 and wg:
        heads.append((92, f"{last(wg['id']) or wg['name'].split(' ', 1)[-1]} shuts out {W.the(loser)}"))
    if share is not None and share <= 0.35 and ws - ls <= 2:
        thief = wg["name"].split(" ", 1)[-1] if wg and (wg.get("gsax") or 0) >= 1 else None
        where = f" in {W.place(home)}" if winner == away else ""
        heads.append((90, f"{thief} steals one{where}" if thief else f"{_cap(W.the(winner))} steal one{where}"))
    if comeback:
        heads.append((88, f"{_cap(W.the(winner))} come back from {num(lead_max[loser])} down"))
    if end in ("OT", "SO"):
        ot = next((g for g in goals if g["period"] > 3), None)
        heads.append((80, f"{last(ot['p1'])} wins it in overtime" if ot and end == "OT" else f"{_cap(W.the(winner))} win it in a shootout"))
    if ws - ls >= 4:
        heads.append((75, f"{_cap(W.the(winner))} roll past {W.the(loser)}"))
    heads.append((10, f"{_cap(W.the(winner))} beat {W.the(loser)}"))
    head = max(heads, key=lambda h: h[0])[1]
    # paragraph one: how the goals went
    s1 = []
    if goals:
        first = goals[0]
        early3 = next((g for g in goals if abs(g["hs"] - g["as"]) >= 3), None)
        held_on = early3 and all(abs(g["hs"] - g["as"]) >= 2 for g in goals[goals.index(early3):])
        if early3 and early3["period"] == 1 and side(early3) == winner and held_on:
            s1.append(f"{_cap(W.the(winner))} had this one put away early.")
        if first["period"] <= 3:  # a lone overtime goal is told once, as the winner
            if first["p1"] in twice:
                s1.append(f"{name(first['p1'])} scored twice, opening the scoring {_when(first)}.")
            else:
                s1.append(f"{name(first['p1'])} opened the scoring {_when(first)}.")
        pp_two = [pid for pid in twice if all(g["strength"] == "pp" for g in by[pid])]
        more = [p for p in twice if p != first["p1"]][:2]
        if len(more) == 2:
            s1.append(f"{name(more[0])} and {name(more[1])} added two each.")
        elif more:
            s1.append(f"{name(more[0])} added two{' power-play' if more[0] in pp_two else ''} goals.")
        if early3 and early3["period"] == 1 and early3["sec"] < 600:
            score = f"{max(early3['hs'], early3['as'])}-{min(early3['hs'], early3['as'])}"
            if side(early3) == winner:
                s1.append(f"It was {score} before the first period was half over.")
            else:
                s1.append(f"{_cap(W.the(loser))} led {score} before the first period was half over.")
        scorers = {g["p1"] for g in goals if side(g) == winner}
        if len(scorers) >= 4 and not hat:
            s1.append(f"{_cap(num(len(scorers)))} different {W.nick.get(winner, winner)} scored.")
        if hat:
            pid, gs = max(hat, key=lambda x: len(x[1]))
            kinds = [{"ev": "at even strength", "pp": "on the power play", "sh": "shorthanded", "en": "into an empty net"}[g["strength"]] for g in gs]
            if len(set(kinds)) == len(kinds):
                s1.append(f"{name(pid)} scored {num(len(gs))}: {_and(['one ' + k for k in kinds])}.")
            else:
                s1.append(f"{name(pid)} scored {num(len(gs))}.")
        if comeback:
            s1.append(f"{_cap(W.the(winner))} came all the way back." if any(x.endswith("half over.") and "led" in x for x in s1)
                      else f"{_cap(W.the(loser))} led by {num(lead_max[loser])}, but {W.the(winner)} came all the way back.")
        else:
            ties = [g for g in goals if g["hs"] == g["as"]]
            if len(ties) >= 2:
                tiers = {side(g) for g in ties}
                times = "Twice" if len(ties) == 2 else f"{_cap(num(len(ties)))} times"
                if len(tiers) == 1:
                    t = tiers.pop()
                    other = away if t == home else home
                    s1.append(f"{times} {W.the(t)} answered a {W.nick.get(other, other)} lead.")
                else:
                    s1.append(f"The game was tied {'twice' if len(ties) == 2 else num(len(ties)) + ' times'}.")
        if end == "OT":
            ot = next((g for g in goals if g["period"] > 3), None)
            if ot:
                s1.append(f"{name(ot['p1'])} won it {_when(ot)}.")
        elif end == "REG":  # the winner: the goal that put them one past the loser's final total
            gw = next((g for g in goals if side(g) == winner and (g["hs"] if g["home"] else g["as"]) == ls + 1), None)
            if gw and gw["p1"] not in said and gw is not goals[0] and ws - ls <= 2:
                s1.append(f"{name(gw['p1'])} scored the winner {_when(gw)}.")
        elif end == "SO":
            if s1 and s1[-1].endswith("came all the way back."):
                s1[-1] = s1[-1][:-1] + ", and won it in the shootout."
            else:
                s1.append(f"{_cap(W.the(winner))} won it in the shootout.")
    # A starter was pulled only if a teammate goalie then played: a short night on its own is usually the net left
    # empty for an extra attacker.
    in_net = r.get("goalies", [])
    relieved = {x["team"] for x in in_net if not x.get("started") and _secs(x.get("toi")) > 0}
    pulled = [x for x in in_net if x.get("started") and x["team"] in relieved and x.get("ga", 0) >= 3]
    for x in pulled[:1]:
        s1.append(f"{W.place(x['team'])} pulled {name(x['id']) or x['name']} after {num(x['ga'])} goals.")
    # Pulling the goalie for an extra attacker and giving up an empty-net goal: name whoever was last in net.
    en = [g for g in goals if g.get("extra") and side(g) == winner]
    if en:
        mine = [x for x in in_net if x["team"] == loser]
        pulled = next((x for x in mine if not x.get("started") and _secs(x.get("toi")) > 0), None) or next((x for x in mine if x.get("started")), None)
        who = (name(pulled["id"]) or pulled["name"]) if pulled else "their goalie"
        into = "into the empty net" if len(en) == 1 else ("twice" if len(en) == 2 else f"{num(len(en))} times") + " into the empty net"
        s1.append(_pick([f"{_cap(W.place(loser))} pulled {who} for an extra attacker, and {W.the(winner)} made them pay {into}.",
                         f"{_cap(W.place(loser))} went for the extra skater late and got burned: {W.the(winner)} scored {into}."],
                        r["game_id"], "en"))
    if ls == 0 and wg:
        s1.append(f"{name(wg['id']) or wg['name']} stopped all {wg['saves']} shots.")
    # paragraph two: was it fair, and who stood out
    s2 = []
    if share is not None:
        def tenths(p):
            return {10: "every time", 9: "nine in ten", 8: "about four in five", 7: "about seven in ten", 6: "about six in ten",
                    5: "about half the time", 4: "about four in ten", 3: "about three in ten", 2: "about one in five",
                    1: "about one in ten", 0: "almost never"}[round(p * 10)]
        if share >= 0.7:
            s2.append(f"The score was fair. Replay this game’s chances a hundred times and {W.the(winner)} win {tenths(share)}.")
        elif share <= 0.4:
            g = gl.get(winner)
            lead = f"The score flatters {W.the(winner)}: {W.the(loser)} had the better of the play." if ws - ls >= 3 else f"{_cap(W.the(loser))} had the better of the play."
            s2.append(f"{lead} Replay the chances a hundred times and {W.the(loser)} win {tenths(1 - share)}.")
            if g and (g.get("gsax") or 0) >= 1:
                s2.append(f"{name(g['id']) if g.get('id') in N else g['name']} made the difference in net, stopping {g['saves']} of {g['shots']}.")
        elif ws - ls >= 3:
            s2.append("The chances were closer than the score.")
        else:
            s2.append("The chances were close to even, so this one could have gone either way.")
    swing = _swing(gid)
    if swing:
        top = swing[0]
        if top["id"] not in said and top.get("pos") != "G":
            pts = sum(top["id"] in (g["p1"], g["p2"], g["p3"]) for g in goals)
            tail = ", even in a loss" if top.get("team") == loser else ", without a point" if not pts else ""
            s2.append(f"By our Swing measure, {name(top['id'])} had the best night of anyone{tail}.")
    for sp in r.get("surprises", []):
        if sp.get("kind") == "lineup" and "different line" in sp.get("head", ""):
            body = sp.get("body", "")
            team = next((t for t in (away, home) if W.place(t) in sp["head"]), None)
            dressed = P.filter((pl.col("game_id") == gid) & (pl.col("team") == (team or "")))["player_id"].to_list()
            trio = []
            for short in body.split(" played ")[0].split(", "):
                first, _, lastname = short.partition(". ")
                pid = next((p for p in dressed if N.get(p, "").endswith(" " + lastname) and N[p].startswith(first)), None)
                trio.append(name(pid) if pid else short)
            line_goals = sum(1 for g in goals if side(g) == team and {g["p1"], g["p2"], g["p3"]} & set(
                p for p in dressed if any(N.get(p, "").endswith(" " + x.split(" ")[-1]) for x in trio)))
            if team:
                tail = f", and it had a hand in {'both' if line_goals == 2 else num(line_goals)} of their goals" if line_goals >= 2 else ""
                s2.append(f"{_cap(W.the(team))} tried a line they hadn’t used before: {_and(trio)}{tail}.")
            break
    # paragraph three: the penalty sheet, only when something stood out (a fight, a brawl, an ejection, a strange count)
    s3 = penalties.recap_notes(gid, season, {home_id: home, row["away_id"][0]: away}, W, name)
    # what it means
    items = []
    for t in (winner, loser):
        res = results(G.filter(pl.col("date") <= r["date"]), t, season)
        k, n = streak(res)
        prev_k, prev_n = streak(res[:-1])
        if t == winner and k == "won" and n >= 3:
            items.append({"tag": "Streak", "text": f"{_cap(num(n))} straight wins for {W.the(t)}.", "score": 60 + n})
        if t == loser and prev_k == "won" and prev_n >= 3:
            items.append({"tag": "Snapped", "text": f"{_cap(W.poss(t))} {num(prev_n)}-game winning streak is over.", "score": 62 + prev_n})
        if t == loser and k == "winless" and n >= 3:
            items.append({"tag": "Slump", "text": f"{_cap(W.the(t))} haven’t won in {num(n)}.", "score": 55 + n})
        if t == winner and prev_k == "winless" and prev_n >= 3:
            items.append({"tag": "Snapped", "text": f"{_cap(W.the(t))} won for the first time in {num(prev_n + 1)} games.", "score": 58 + prev_n})
    pg = P.filter((pl.col("season") == season) & (pl.col("date") <= r["date"]))
    tonight = pg.filter(pl.col("game_id") == gid)
    for pr in tonight.filter(pl.col("pts") > 0).iter_rows(named=True):
        hist = pg.filter((pl.col("player_id") == pr["player_id"]) & (pl.col("team") == pr["team"]))["pts"].to_list()
        n = 0
        for x in reversed(hist):
            if x <= 0:
                break
            n += 1
        if n >= 5:
            items.append({"tag": "Streak", "text": f"{N[pr['player_id']]} has a point in {num(n)} straight games." if n < len(hist)
                          else f"{N[pr['player_id']]} has a point in every game this season.", "score": 50 + n})
        if pr["g"] > 0 and len(hist) >= 5 and pg.filter((pl.col("player_id") == pr["player_id"]) & (pl.col("game_id") != gid))["g"].sum() == 0:
            items.append({"tag": "Finally", "text": f"{N[pr['player_id']]} scored his first goal of the season.", "score": 48})
    nxt = _next_games(r["date"], (away, home))
    for sp in r.get("surprises", []):
        if sp.get("kind") == "lineup" and "different line" in sp.get("head", ""):
            team = next((t for t in (away, home) if W.place(t) in sp["head"]), None)
            if team and team in nxt:
                n = nxt[team]
                where = f"in {W.place(n['home'])}" if n["home"] != team else f"at home against {W.place(n['away'])}"
                items.append({"tag": "Keep an eye on", "text": f"Whether {W.the(team)} keep that line together {where} on {weekday(n['date'])}.", "score": 45})
    for t, n in nxt.items():
        opp = n["home"] if n["away"] == t else n["away"]
        for item in _old_team_next(t, opp, n["date"], season, W):
            items.append({**item, "score": 40})
    held = [c for c in r.get("calls", []) if c.get("verdict") in ("held", "partly", "missed")]
    if held:
        h = sum(c["verdict"] == "held" for c in held)
        n = len(held)
        text = (("The preview’s call held." if h else "The preview’s call didn’t hold.") if n == 1 else
                ("Both of the preview’s calls held." if n == 2 else f"All {num(n)} of the preview’s calls held.") if h == n else
                f"{_cap(num(h))} of the preview’s {num(n)} calls held." if h else ("Neither of the preview’s calls held." if n == 2 else f"None of the preview’s {num(n)} calls held."))
        items.append({"tag": "The preview", "text": text, "score": 20})
    items.sort(key=lambda i: -i["score"])
    picked, per = [], {}
    for i in items:
        if per.get(i["tag"], 0) >= (1 if i["tag"] == "Finally" else MAX_PER_TAG) or len(picked) >= MAX_ITEMS:
            continue
        per[i["tag"]] = per.get(i["tag"], 0) + 1
        picked.append({"tag": i["tag"], "text": i["text"]})
    if len(picked) < MIN_ITEMS:
        res = results(G.filter(pl.col("date") <= r["date"]), winner, season)
        picked.append({"tag": "Record", "text": f"{_cap(W.the(winner))} are now {record(res)}."})
    return {"kicker": "What happened", "head": head, "paras": [p for p in (" ".join(s1), " ".join(s2), " ".join(s3)) if p],
            "list_head": "What it means", "items": picked}


def _swing(gid: int) -> list[dict]:
    p = SITE_DATA / "scores" / f"{gid}.json"
    if not p.exists():
        return []
    return json.loads(p.read_text()).get("players", [])


def _next_games(after: str, teams: tuple[str, str]) -> dict[str, dict]:
    try:
        sched = json.loads((SITE_DATA / "schedule.json").read_text())["games"]
    except OSError:
        return {}
    out = {}
    for g in sorted(sched, key=lambda g: g["start"]):
        if g["date"] <= after:
            continue
        for t in teams:
            if t not in out and t in (g["home"], g["away"]):
                out[t] = g
    return out


def _old_team_next(t: str, opp: str, day: str, season: int, W: Words) -> list[dict]:
    """A player who faces his old team in his club's next game, for the first time since leaving."""
    _, P, N = book()
    cur = _current_team(P, season)
    out = []
    for pid, team in cur.items():
        if team != t:
            continue
        g = P.filter(pl.col("player_id") == pid).sort("date")
        teams = g["team"].to_list()
        prev = next((x for x in reversed(teams) if x != t), None)
        if prev != opp:
            continue
        for_them = g.filter(pl.col("team") == opp)
        if for_them.height < 40 or g.filter((pl.col("date") > for_them["date"].max()) & (pl.col("opp") == opp)).height:
            continue
        out.append((for_them.height, pid))
    out.sort(reverse=True)
    return [{"tag": "Up next", "text": f"{N[pid]} faces his old team, {W.place(opp)}, on {weekday(day)}."} for _, pid in out[:1]]


def run_recaps(season: int = CURRENT_SEASON) -> int:
    """Add a story to every post-game file. Runs after `game_scores`, which it reads for the Swing."""
    places = {t["abbr"]: t["place"] for t in json.loads((SITE_DATA / "teams.json").read_text())}
    n = 0
    for p in sorted((SITE_DATA / "recaps").glob("2*.json")):
        r = json.loads(p.read_text())
        if r.get("pending"):  # the story leans on Swing and the graded calls: written once the shift chart is in
            continue
        try:
            story = recap_story(r, places, season)
        except Exception as e:  # a story is a nice-to-have: never lose the page over it
            print(f"story for {r['game_id']} failed: {e!r}")
            story = None
        if story:
            r["story"] = story
            p.write_text(json.dumps(r, separators=(",", ":"), ensure_ascii=False))
            n += 1
    return n
