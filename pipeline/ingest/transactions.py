"""Roster moves from ESPN's league-wide transactions list: injured reserve, long-term injured reserve, the injured
non-roster list, assignments to and recalls from the minors, suspensions and reinstatements.

The NHL publishes no transactions feed (checked 2026-10-11; only a handful of clubs keep a transactions page on
NHL.com), so this is the one source that covers all 32 clubs. It is unofficial and not complete: some injuries never
get a roster move (a player hurt but not placed on injured reserve) and some moves are missing, so it sits beside the
NHL.com Status Report (ingest/status_report.py), never in place of it. A contract holdout is not a transaction and
never appears.

Endpoint: site.api.espn.com/apis/site/v2/sports/hockey/nhl/transactions?dates={calendar year}&limit=1000&page=N,
newest first, each item {date, team {abbreviation}, description}. Descriptions are free text written by hand
("Placed LW Victor Olofsson and D Max Crotty on injured reserve. Recalled D Ville Heinola from Henderson (AHL).").

Raw: data/raw/transactions/{year}.json.gz, [date, team, description] per move. A finished calendar year is fetched
once; the current year again on every run.

Table: data/tables/{season}/transactions.parquet, shaped like status_reports.parquet (date, player_id, name,
section = club abbreviation, status, injury, timeline, note = the sentence, url = null) so availability.py can read
both as "reports". Each sentence is read by fixed rules; players are found by their full name (only names that belong
to one player we know). Statuses:
  long        placed on long-term injured reserve, or on the injured non-roster list (timeline says which)
  ir          placed on injured reserve
  suspended   suspended
  activated   activated from injured reserve or the non-roster list, or reinstated from a suspension
  assigned    assigned, reassigned, loaned or sent to a minor-league club (timeline: "to Chicago (AHL)")
  recalled    recalled or called up
Moves that do not change whether a player can play (signings, trades, waivers, draft picks) are skipped.
"""
import gzip
import json
import re
from datetime import date as Date

import httpx
import polars as pl

from pipeline.config import OFFLINE, RAW, TABLES
from pipeline.ingest.client import get
from pipeline.ingest.status_report import SCHEMA, _fold, known_names

URL = "https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/transactions?dates={year}&limit=1000&page={page}"
ESPN_ABBR = {"LA": "LAK", "NJ": "NJD", "SJ": "SJS", "TB": "TBL"}  # ESPN's short codes that differ from the NHL's


def raw_path(year: int):
    return RAW / "transactions" / f"{year}.json.gz"


def path(season: int):
    return TABLES / str(season) / "transactions.parquet"


def fetch(year: int) -> int:
    moves, page = [], 1
    while True:
        body = get(URL.format(year=year, page=page)).json()
        moves += [[t.get("date", "")[:10], ESPN_ABBR.get((t.get("team") or {}).get("abbreviation"), (t.get("team") or {}).get("abbreviation")),
                   t.get("description") or ""] for t in body.get("transactions", [])]
        if page >= (body.get("pageCount") or 1):
            break
        page += 1
    p = raw_path(year)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".part")
    with gzip.open(tmp, "wt") as f:
        json.dump(moves, f, separators=(",", ":"))
    tmp.replace(p)
    return len(moves)


def _years(season: int, today: Date) -> list[int]:
    """Calendar years a hockey season touches (July to June), up to today."""
    return [y for y in (season, season + 1) if Date(y, 1, 1) <= today]


def update(season: int, today: Date | None = None) -> dict:
    """Fetch the season's calendar years (a finished one only once), then rebuild the table. Safe offline."""
    today = today or Date.today()
    fetched = {}
    if not OFFLINE:
        for y in _years(season, today):
            if raw_path(y).exists() and y < today.year:
                continue
            try:
                fetched[y] = fetch(y)
            except (httpx.HTTPError, RuntimeError) as e:  # tried again next run; the saved copy stands
                print(f"transactions {y} unavailable: {e!r}")
    return {"fetched": fetched, **build(season)}


PLACED = r"^(?:placed|moved|transferred|put)\b"
TO_CLUB = re.compile(r"\bto (?:the )?([A-Z][\w.'/\- ]*?(?: \((?:AHL|ECHL)\))?)(?=[.,;]|$| for | on | and (?:[A-Z]{1,2}|Fs?|Ds?|Gs?) )")


def classify(sentence: str) -> tuple[str, str | None] | None:
    """(status, timeline) for one sentence, or None when it does not change whether anyone can play."""
    s = sentence.lower()
    if re.match(r"^(?:activated|reinstated|returned)\b", s) and re.search(r"injured|non-roster|suspension", s):
        return "activated", None
    if re.match(PLACED, s):
        if "long-term injured reserve" in s or "ltir" in s:
            return "long", "on long-term injured reserve"
        if "non-roster" in s:
            return "long", "on the injured non-roster list" if "injured" in s else "on the non-roster list"
        if "injured reserve" in s:
            return "ir", None
        if "waivers" in s and "loan" in s:  # "placed on waivers for the purpose of loaning them to Laval (AHL)"
            m = TO_CLUB.search(sentence)
            return "assigned", f"to {m.group(1)}" if m else None
        return None
    if re.match(r"^suspended\b", s):
        return "suspended", None
    if re.match(r"^(?:assigned|reassigned|loaned|optioned|sent)\b", s):
        if "exchange" in s or re.search(r"\bfor (?:a|an|the|future|f|d|g|c|lw|rw|fs|ds)\b", s):
            return None  # "Sent D X to Colorado in exchange for a fifth-round pick": a trade
        m = TO_CLUB.search(sentence)
        return "assigned", f"to {m.group(1)}" if m else None
    if re.match(r"^(?:recalled|called up|promoted)\b", s):
        return "recalled", None
    return None


def _sentences(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"(?<=[a-z\)])\.\s+(?=[A-Z])", text.strip()) if p.strip()]


def parse(moves: list[list[str]], known: dict[str, int]) -> list[dict]:
    pattern = re.compile(r"\b(" + "|".join(sorted((re.escape(n) for n in known), key=len, reverse=True)) + r")\b") if known else None
    rows = []
    for day, team, text in moves:
        for sentence in _sentences(text):
            kind = classify(sentence)
            if kind is None or pattern is None:
                continue
            for name in dict.fromkeys(pattern.findall(_name(_fold(sentence)))):
                rows.append({"date": day, "player_id": known[name], "name": name.title(), "section": team, "status": kind[0], "injury": None,
                             "timeline": kind[1], "note": sentence[:240], "url": None})
    return rows


def build(season: int) -> dict:
    """Every saved move between July 1 of the season and June 30 after it, read into transactions.parquet."""
    start, end = f"{season}-07-01", f"{season + 1}-06-30"
    moves = []
    for y in (season, season + 1):
        if raw_path(y).exists():
            with gzip.open(raw_path(y), "rt") as f:
                moves += [m for m in json.load(f) if start <= m[0] <= end]
    rows = parse(moves, {_name(n): p for n, p in known_names(season).items()})
    path(season).parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows, schema=SCHEMA).unique(["date", "player_id", "status"], keep="first", maintain_order=True).sort("date", "player_id").write_parquet(path(season))
    return {"moves": len(moves), "player_moves": len(rows)}


def _name(folded: str) -> str:
    """Folded name with punctuation dropped, the form sentences are matched in."""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z' \-]", " ", folded)).strip()


def load(season: int) -> pl.DataFrame:
    return pl.read_parquet(path(season)) if path(season).exists() else pl.DataFrame(schema=SCHEMA)
