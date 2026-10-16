"""NHL.com's own game previews, for the starting goalies it projects.

Each preview (nhl.com/news/{away club}-{home club}-game-preview-{month}-{day}-{year}) lists both teams' projected
lineups, goalies last, with the expected starter first. Checked over the first 141 team-games of 2026-27, the first goalie
listed started 128 times (91%), against 69% for our own guess from recent usage (pipeline/metrics/starters.py). The
articles go up on game day, usually 2 to 7 hours before puck drop (most between 16:00 and 19:00 UTC) and are sometimes
edited after, so each run asks again for every game today or tomorrow that hasn't started (a few dozen requests).

Saved per game at raw/nhl_previews/{season}/{game_id}.json.gz: {"fetched", "url", "published", "starters": {team: id},
"lineups": {team: {"lines", "pairs", "extra_d", "goalies", "scratched", "injured", "missed"}}, "status"} ("starters"
empty when the page isn't up yet). Lineups are player ids only (`lineups_from`): forward lines in the order listed (a
two-name fourth "line" when a club dresses 11 forwards), defense pairs, a defenseman listed alone (the seventh), the
goalies, and the scratched and injured lists without their reasons; `missed` counts names we could not match. "status"
is the "Status report" paragraph as written, kept so fixed rules (`status_facts`) can read it again; only the facts
those rules name (a projected 11 forwards, the same lineup, game-time decisions, a coach's line changes, a suspension,
a healthy scratch) reach the site, never the article's words.
"""
from __future__ import annotations

import difflib
import gzip
import json
import re
from datetime import date as Date, datetime, timedelta, timezone

import httpx
import polars as pl

from pipeline.config import CURRENT_SEASON, FINAL_STATES, OFFLINE, RAW, REGULAR, SITE_DATA, TABLES
from pipeline.ingest import names
from pipeline.ingest.client import get

MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"]
LD = re.compile(r'<script type="application/ld(?:&#x2B;|\+)json">(.*?)</script>', re.S)
LINEUP = re.compile(r"\*\*[^*\n]*projected lineup\*\*", re.I)
DAYS_AHEAD = 1  # today's and tomorrow's games (by the schedule's date)


def fold(s: str) -> str:
    s = names.fold(s)
    return re.sub(r"[^a-z]+", " ", s).strip()


def slug(name: str) -> str:
    return fold(name).replace(" ", "-")


def url_for(away_name: str, home_name: str, day: str) -> str:
    d = Date.fromisoformat(day)
    return f"https://www.nhl.com/news/{slug(away_name)}-{slug(home_name)}-game-preview-{MONTHS[d.month - 1]}-{d.day}-{d.year}"


def raw_path(season: int, gid: int):
    return RAW / "nhl_previews" / str(season) / f"{gid}.json.gz"


def read(season: int, gid: int) -> dict | None:
    p = raw_path(season, gid)
    return json.loads(gzip.open(p).read()) if p.exists() else None


def _article(html: str) -> dict:
    for m in LD.finditer(html):
        try:
            d = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        if isinstance(d, dict) and d.get("articleBody") and LINEUP.search(d["articleBody"]):
            return d
    return {}


def goalie_names(body: str) -> list[list[str]]:
    """The lines of each projected lineup (away first, then home) that are a single name, in order: the goalies, plus
    the odd seventh defenseman listed alone. Forward lines and pairs are joined by dashes."""
    out = []
    for part in LINEUP.split(body)[1:3]:
        part = re.split(r"Scratched|Injured|Suspended|Status report", part)[0]
        names = [ln.strip(" *_ ") for ln in part.split("\n")]
        out.append([n for n in names if n and not re.search(r"--|–|—", n)])
    return out


SPLIT = re.compile(r"\s*(?:--|–|—)\s*")
ROW_END = re.compile(r"\*{0,3}_?(Scratched|Injured|Suspended|Status report)", re.I)
STATUS = re.compile(r"\*+\s*Status report:?\s*\*+:?|^\s*Status report:?\s*$", re.I | re.M)  # "**Status report**", "**Status report:**", a bare line


def _sections(body: str) -> list[str]:
    """Each team's projected-lineup block (away first, then home), cut at the status report."""
    return [STATUS.split(part)[0] for part in LINEUP.split(body)[1:3]]


def _list(part: str, label: str) -> list[str]:
    """Names in a "Scratched:" or "Injured:" line, reasons in brackets dropped."""
    m = re.search(rf"{label}:\**\s*(.*)", part, re.I)
    if not m:
        return []
    text = re.sub(r"\([^)]*\)", "", m.group(1)).strip(" *_")
    return [n.strip(" *_.") for n in re.split(r",|\band\b", text) if n.strip(" *_.") and fold(n) not in ("none", "")]


def known_skaters(season: int) -> dict[str, dict[str, tuple[int, str]]]:
    """{team: {folded full name: (player id, position)}} for everyone a club has listed or dressed this season and
    last, plus "*" for anyone, and "~{team}" keyed by folded last name when that last name is unique on the club."""
    out: dict[str, dict] = {}
    rows = []
    r = TABLES / str(season) / "rosters.parquet"
    if r.exists():
        rows += pl.read_parquet(r, columns=["team", "player_id", "first", "last", "pos"]).unique(["team", "player_id"]).to_dicts()
    for s in (season - 1, season):
        d = TABLES / str(s)
        if (d / "players.parquet").exists() and (d / "games.parquet").exists():
            g = pl.read_parquet(d / "games.parquet")
            abbr = {**dict(zip(g["home_id"], g["home"])), **dict(zip(g["away_id"], g["away"]))}
            for x in pl.read_parquet(d / "players.parquet").unique(["team_id", "player_id"]).iter_rows(named=True):
                rows.append({"team": abbr.get(x["team_id"]), "player_id": x["player_id"], "first": x["first"], "last": x["last"], "pos": x["pos"]})
    lasts: dict[str, dict[str, set]] = {}
    for x in rows:
        if not x["team"]:
            continue
        full = fold(f"{x['first']} {x['last']}")
        out.setdefault(x["team"], {})[full] = (x["player_id"], x["pos"])
        out.setdefault("*", {})[full] = (x["player_id"], x["pos"])
        lasts.setdefault(x["team"], {}).setdefault(fold(x["last"]), set()).add((x["player_id"], x["pos"]))
    for t, by in lasts.items():
        out["~" + t] = {k: next(iter(v)) for k, v in by.items() if len(v) == 1}
    return out


def _match(name: str, team: str, known: dict) -> tuple[int, str] | None:
    """A written name to (id, position): full name on the club, then anywhere, then a last name unique on the club
    (NHL.com drops accents and sometimes writes a shorter first name)."""
    n = fold(name)
    if not n:
        return None
    got = known.get(team, {}).get(n) or known.get("*", {}).get(n) or known.get("~" + team, {}).get(n.split(" ")[-1])
    if got:
        return got
    close = difflib.get_close_matches(n, list(known.get(team, {})), n=1, cutoff=0.88)  # a typo ("Brenden Dillion")
    return known[team][close[0]] if close else None


def lineups_from(body: str, away: str, home: str, known: dict) -> dict[str, dict]:
    """Each team's projected lineup as player ids: rows with dashes are forward lines or defense pairs (told apart by
    the players' listed positions, a two-forward row being the fourth line of an 11-forward lineup), names alone are
    goalies or a seventh defenseman."""
    out = {}
    for team, part in zip((away, home), _sections(body)):
        rows = []
        for ln in part.split("\n"):
            ln = ln.replace("\xa0", " ").strip(" *_ ")
            if not ln or ROW_END.match(ln) or ln.lower().startswith(("scratched", "injured", "suspended")):
                continue
            rows.append([n.strip(" *_") for n in SPLIT.split(ln) if n.strip(" *_")])
        got = {"lines": [], "pairs": [], "extra_d": [], "goalies": [], "scratched": [], "injured": [], "missed": 0}
        for row in rows:
            ids = [_match(n, team, known) for n in row]
            got["missed"] += sum(i is None for i in ids)
            ids = [i for i in ids if i]
            if not ids:
                continue
            pos = [p for _, p in ids]
            if len(ids) == 1 and len(row) == 1:
                pid, p = ids[0]
                if p == "G":
                    got["goalies"].append(pid)
                elif p == "D":
                    got["extra_d"].append(pid)
                else:
                    got["lines"].append([pid])
                continue
            kind = "pairs" if sum(p == "D" for p in pos) * 2 > len(pos) else "lines"
            got[kind].append([i for i, _ in ids])
        for label, key in (("Scratched", "scratched"), ("Injured", "injured")):
            for n in _list(part, label):
                m = _match(n, team, known)
                if m:
                    got[key].append(m[0])
                else:
                    got["missed"] += 1
        out[team] = got
    return out


def status_text(body: str) -> str | None:
    """The "Status report" paragraph, as written."""
    parts = STATUS.split(body, maxsplit=1)
    return (parts[1].strip(" *\n") or None) if len(parts) > 1 else None


def shape(lineup: dict) -> tuple[int, int]:
    """(forwards, defensemen) in a projected lineup."""
    return sum(len(x) for x in lineup.get("lines", [])), 2 * len(lineup.get("pairs", [])) + len(lineup.get("extra_d", []))


def known_goalies(season: int) -> dict[str, dict[str, int]]:
    """{team: {folded full name: player id}} for every goalie a club has listed or dressed this season and last."""
    out: dict[str, dict[str, int]] = {}
    r = TABLES / str(season) / "rosters.parquet"
    if r.exists():
        for x in pl.read_parquet(r).filter(pl.col("pos") == "G").unique(["team", "player_id"]).iter_rows(named=True):
            out.setdefault(x["team"], {})[fold(f"{x['first']} {x['last']}")] = x["player_id"]
    anyteam: dict[str, int] = {}
    for s in (season - 1, season):
        p = TABLES / str(s) / "players.parquet"
        if p.exists():
            for x in pl.read_parquet(p).filter(pl.col("pos") == "G").unique("player_id").iter_rows(named=True):
                anyteam[fold(f"{x['first']} {x['last']}")] = x["player_id"]
    out["*"] = anyteam
    return out


def starters_from(body: str, away: str, home: str, known: dict[str, dict[str, int]]) -> dict[str, int]:
    """{team: player id} for the first listed name in each lineup that is a goalie we know."""
    out = {}
    for team, names in zip((away, home), goalie_names(body)):
        for n in names:
            pid = known.get(team, {}).get(fold(n)) or known["*"].get(fold(n))
            if pid:
                out[team] = pid
                break
    return out


def update(season: int = CURRENT_SEASON, now: datetime | None = None) -> dict:
    """Read NHL.com's preview for every game today or tomorrow that hasn't started."""
    if OFFLINE:
        return {"skipped": "offline"}
    now = now or datetime.now(timezone.utc)
    games = json.loads(gzip.open(RAW / str(season) / "schedule.json.gz").read())  # refreshed by every online ingest
    names = {t["abbr"]: t["name"] for t in json.loads((SITE_DATA / "teams.json").read_text())}
    last = (now.date() + timedelta(days=DAYS_AHEAD)).isoformat()
    known = known_goalies(season)
    skaters = known_skaters(season)
    asked = found = 0
    for g in games:
        if g["game_type"] != REGULAR:
            continue
        start = datetime.fromisoformat(g["start_utc"].replace("Z", "+00:00"))
        if g["state"] in FINAL_STATES or start <= now or g["date"] > last or g["away"] not in names or g["home"] not in names:
            continue
        g["id"] = g["game_id"]
        old = read(season, g["id"]) or {}
        url = url_for(names[g["away"]], names[g["home"]], g["date"])
        asked += 1
        try:
            art = _article(get(url).text)
        except (httpx.HTTPStatusError, RuntimeError):
            art = {}
        text = art.get("articleBody") or ""
        body = {"fetched": now.isoformat(timespec="seconds"), "url": url, "published": art.get("datePublished"),
                "starters": starters_from(text, g["away"], g["home"], known),
                **({"lineups": lineups_from(text, g["away"], g["home"], skaters), "status": status_text(text)} if text else {})}
        if old.get("starters") and not body["starters"]:  # a page that failed to load doesn't undo an earlier find
            continue
        found += len(body["starters"]) == 2
        p = raw_path(season, g["id"])
        p.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(p, "wt") as f:
            json.dump(body, f)
    return {"asked": asked, "found": found}


def projected(season: int, gid: int, before: str | None = None) -> dict[str, int]:
    """NHL.com's projected starters for one game, if its preview was found before `before` (an ISO time)."""
    got = read(season, gid)
    if not got or (before and got.get("fetched", "") >= before):
        return {}
    return {t: int(p) for t, p in (got.get("starters") or {}).items()}


# ---------------------------------------------------------------- the status report, by fixed rules
#
# The paragraph is free text, so it is only read for a handful of fixed phrases, and only the fact each names reaches
# the site (in our own words): never a quote, and never a reason for an absence beyond a suspension, which the league
# announces itself. Checked on the 49 previews of 2026-10-10 to 10-15.

SENTENCE = re.compile(r"\s*(?:\.{2,3}|…)\s*|(?<=[a-z0-9)”\"’])[.;]\s+(?=[A-Z])")
ELEVEN_SAID = re.compile(r"\b(11|eleven)[- ]forwards?,? and (7|seven)[- ]defensem[ae]n\b", re.I)
SAME_SAID = re.compile(r"\bsame (?:lineup|\d+ skaters|eighteen skaters)\b|\bonly lineup change\b", re.I)
GTD_SAID = re.compile(r"\bgame[- ]time decision|\bquestionable\b", re.I)
LINES_SAID = re.compile(r"\b(?:mov(?:ed|ing|es)|shuffl\w*|swap\w*|shift\w*|putting|promot\w*|bump\w*|reunit\w*|together again)\b", re.I)
LINE_WORD = re.compile(r"\blines?\b(?! rushes)", re.I)
SUSPENSION = re.compile(r"\bsuspen(?:ded|sion)\b", re.I)
SUSP_LEN = re.compile(r"\b(\w+)[- ]game suspension|\bsuspended (?:for )?(\w+) games?", re.I)
SUSP_RETURN = re.compile(r"\breturns? (?:after|from) (?:serving|(?:a|his) \w+[- ]game suspension)|\bcompletion of (?:a|his) .*suspension|\bwill return from (?:a|his) .*suspension", re.I)
SUSP_SERVE = re.compile(r"\bserve the (?:\w+ and )?(?:final|last|\w+) game of (?:a|his)\b", re.I)
SUSP_LAST = re.compile(r"\b(?:final|last) game of\b", re.I)
HEALTHY = re.compile(r"\bhealthy scratch", re.I)
WORDS_N = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
CERTAIN = re.compile(r"\bwill\b|\bexpected to\b|\bare going\b", re.I)


def _n(word: str | None) -> int | None:
    if not word:
        return None
    return int(word) if word.isdigit() else WORDS_N.get(word.lower())


def sentences(text: str) -> list[str]:
    return [x.strip() for x in SENTENCE.split(text or "") if x and x.strip()]


def _who(sentence: str, people: dict[int, tuple[str, str, str]]) -> list[int]:
    """Players of tonight's two clubs named in a sentence (last name as a whole, capitalized word; a full name wins
    over a teammate with the same last name), in the order they appear."""
    plain = names.strip(sentence)
    found = []
    for pid, (_, first, last) in people.items():
        m = re.search(rf"(?<![\w-]){re.escape(names.strip(last))}(?![\w-])", plain)
        if m:
            full = re.search(rf"{re.escape(names.strip(first))}\s+{re.escape(names.strip(last))}", plain)
            found.append((m.start() if not full else full.start(), bool(full), pid, last))
    out = []
    for pos, full, pid, last in sorted(found):
        if not full and any(f and l == last and p != pid for _, f, p, l in found):
            continue  # "Nick Foligno" named: not his brother Marcus
        out.append(pid)
    return out


def _teams(sentence: str, aliases: dict[str, list[str]], players: list[int], people: dict) -> list[str]:
    if re.search(r"\b(?:each|both|neither) team", sentence, re.I):
        return list(aliases)
    hit = [t for t, al in aliases.items() if any(re.search(rf"\b{re.escape(a)}\b", sentence) for a in al)]
    return hit or list(dict.fromkeys(people[p][0] for p in players))


def status_facts(text: str | None, aliases: dict[str, list[str]], people: dict[int, tuple[str, str, str]]) -> list[dict]:
    """The facts the rules know how to read, one dict each: {"kind", "team", "players", ...}. `aliases` names each of
    the two clubs ({team: ["Tampa Bay", "Lightning", ...]}), `people` their players ({id: (team, first, last)})."""
    out = []
    for s in sentences(text or ""):
        who = _who(s, people)
        teams = _teams(s, aliases, who, people)
        if ELEVEN_SAID.search(s):
            for t in teams or [None]:  # no club named ("The Lighting are expected to..."): the projection decides
                out.append({"kind": "eleven", "team": t, "players": [], "sure": bool(CERTAIN.search(s)) and not re.search(r"\bcould\b|\bconsider", s, re.I)})
        if SAME_SAID.search(s) and len(teams) == 1:
            out.append({"kind": "same", "team": teams[0], "players": []})
        if GTD_SAID.search(s):
            for p in who[:1] if re.search(r"\bif\b", s) else who:  # "If he can't play, X will take his spot": only the first
                out.append({"kind": "gtd", "team": people[p][0], "players": [p]})
        if LINE_WORD.search(s) and LINES_SAID.search(s) and who:
            for t in dict.fromkeys(people[p][0] for p in who):
                out.append({"kind": "lines", "team": t, "players": [p for p in who if people[p][0] == t]})
        if SUSPENSION.search(s) and who:
            p = who[0]
            m = SUSP_LEN.search(s)
            games = _n(m and (m.group(1) or m.group(2)))
            state = "returns" if SUSP_RETURN.search(s) else "serving" if SUSP_SERVE.search(s) else "suspended"
            out.append({"kind": "suspension", "team": people[p][0], "players": [p], "games": games, "state": state,
                        "last": bool(SUSP_LAST.search(s)) if state == "serving" else None})
        if HEALTHY.search(s) and who:
            out.append({"kind": "healthy", "team": people[who[0]][0], "players": [who[0]]})
    seen, unique = set(), []
    for f in out:  # the paragraph is sometimes printed twice
        key = (f["kind"], f["team"], tuple(f["players"]))
        if key not in seen:
            seen.add(key)
            unique.append(f)
    return unique


def people_for(season: int, teams: tuple[str, str]) -> dict[int, tuple[str, str, str]]:
    """{id: (team, first, last)} for everyone the two clubs list on their latest roster day or dressed this season."""
    out: dict[int, tuple[str, str, str]] = {}
    d = TABLES / str(season)
    if (d / "players.parquet").exists() and (d / "games.parquet").exists():
        g = pl.read_parquet(d / "games.parquet")
        abbr = {**dict(zip(g["home_id"], g["home"])), **dict(zip(g["away_id"], g["away"]))}
        for x in pl.read_parquet(d / "players.parquet").sort("game_id").iter_rows(named=True):
            t = abbr.get(x["team_id"])
            if t in teams and x["pos"] != "G":
                out[x["player_id"]] = (t, x["first"], x["last"])
    if (d / "rosters.parquet").exists():
        r = pl.read_parquet(d / "rosters.parquet")
        latest = r.filter(pl.col("team").is_in(list(teams))).group_by("team").agg(pl.col("date").max())
        r = r.join(latest, on=["team", "date"])
        for x in r.filter(pl.col("pos") != "G").iter_rows(named=True):
            out[x["player_id"]] = (x["team"], x["first"], x["last"])
    return out


def projection(season: int, gid: int, before: str | None = None) -> dict | None:
    """NHL.com's projected lineups and status paragraph for one game, if its preview was found (before `before`, an
    ISO time). Saves made before lineups were kept have none."""
    got = read(season, gid)
    if not got or not got.get("lineups") or (before and got.get("fetched", "") >= before):
        return None
    return got
