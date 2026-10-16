"""ESPN's league-wide injury list, saved once a day and read as reports beside the NHL.com Status Report
(ingest/status_report.py) and roster moves (ingest/transactions.py). Sora approved it 2026-10-13 (item 14b of the
eye-test plan): the Status Report and roster moves miss long absences like Andersen's, Demko's and Hyman's, which this
list carries.

Endpoint: site.api.espn.com/apis/site/v2/sports/hockey/nhl/injuries, the list as it stands now (no history), one entry
per player per club: status (Injured Reserve, Out, Day-To-Day, Suspension), the date ESPN last changed the entry, and
details {type = body part or reason, detail (Surgery), returnDate (ESPN's estimate), fantasyStatus (IR, IR-LT long-term
injured reserve, IR-NR injured non-roster, OUT, Day-To-Day)}. The written comments are not saved or used.

Raw: data/raw/injuries/{season}/{date}.json.gz, one slimmed copy of the list per day (the run day, Eastern); a day's
copy is replaced by later runs that day. History therefore starts with the first run after this was added, but each
entry carries the date it was last changed, so long absences already on the list are dated from when ESPN listed them.

Table: data/tables/{season}/injuries.parquet, shaped like status_reports.parquet (date, player_id, name, section = club
abbreviation, status, injury, timeline, note, url = ESPN's injuries page, which marks the source for the site). Each
entry becomes a report dated when ESPN last changed it:
  season      injured reserve with ESPN's return estimate after the season ends
  long        long-term injured reserve or the injured non-roster list (timeline says which)
  ir          injured reserve
  out         out
  day         day to day
  suspended   suspended (also ESPN's non-roster entries given the reason "Suspension")
  personal    away for personal reasons
  cleared     on one day's list and gone from the next (dated that next day): no longer reported injured
  unsigned    a contract dispute: an unsigned restricted free agent whose rights the club holds (never an injury; ESPN
              moves these between "Out" and the injured non-roster list as bookkeeping, as it did with Nikishin and
              Edvinsson on 2026-10-14); availability.py keeps it from reading as an injury
Players are matched by full name on the club's roster listing or game rosters, then by a full name that belongs to one
player only.
"""
import gzip
import json
from datetime import date as Date

import httpx
import polars as pl

from pipeline.config import OFFLINE, RAW, TABLES
from pipeline.ingest.client import get
from pipeline.ingest.rosters import today
from pipeline.ingest.status_report import SCHEMA, _fold, known_names
from pipeline.ingest.transactions import ESPN_ABBR

URL = "https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/injuries"
PAGE = "https://www.espn.com/nhl/injuries"  # stored as each report's url, so pages can credit the source
MIN_ENTRIES = 40  # a list shorter than this is treated as a failed load: no "cleared" reports are read from it


def raw_dir(season: int):
    return RAW / "injuries" / str(season)


def path(season: int):
    return TABLES / str(season) / "injuries.parquet"


def _slim(body: dict) -> list[dict]:
    out = []
    for club in body.get("injuries", []):
        for e in club.get("injuries", []):
            a, det = e.get("athlete") or {}, e.get("details") or {}
            team = (a.get("team") or {}).get("abbreviation")
            out.append({"team": ESPN_ABBR.get(team, team), "name": a.get("displayName"), "pos": (a.get("position") or {}).get("abbreviation"),
                        "status": e.get("status"), "date": (e.get("date") or "")[:10], "type": det.get("type"),
                        "detail": det.get("detail"), "side": det.get("side"), "return": det.get("returnDate"),
                        "fantasy": (det.get("fantasyStatus") or {}).get("abbreviation")})
    return out


def fetch(season: int, day: str) -> int:
    entries = _slim(get(URL).json())
    p = raw_dir(season) / f"{day}.json.gz"
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".part")
    with gzip.open(tmp, "wt") as f:
        json.dump(entries, f, separators=(",", ":"))
    tmp.replace(p)
    return len(entries)


def update(season: int, day: str | None = None) -> dict:
    """Save today's list (unless offline), then rebuild the table from every saved day."""
    day = day or today()
    fetched = None
    if not OFFLINE:
        try:
            fetched = fetch(season, day)
        except (httpx.HTTPError, RuntimeError, ValueError) as e:  # tried again next run; earlier days stand
            print(f"injuries unavailable: {e!r}")
    return {"fetched": fetched, **build(season)}


def _injury(e: dict) -> str | None:
    t = (e.get("type") or "").strip().lower()
    if not t or t in ("undisclosed", "not specified", "suspension", "personal"):
        return None
    t = t.replace("upper body", "upper-body").replace("lower body", "lower-body").replace("lower leg", "lower-leg")
    return f"{t} surgery" if (e.get("detail") or "").lower() == "surgery" else t


def classify(e: dict, season: int) -> tuple[str, str | None] | None:
    """(status, timeline) for one entry, or None when it is skipped."""
    kind, st, fan = (e.get("type") or "").lower(), (e.get("status") or "").lower(), (e.get("fantasy") or "").upper()
    if kind == "contract dispute":
        return "unsigned", None
    if kind == "suspension" or st == "suspension":
        return "suspended", None
    if kind == "personal":
        return "personal", None
    if st == "injured reserve":
        if (e.get("return") or "")[:10] > f"{season + 1}-06-30":
            return "season", None
        if fan == "IR-LT":
            return "long", "on long-term injured reserve"
        if fan == "IR-NR":
            return "long", "on the injured non-roster list"
        return "ir", None
    if st == "day-to-day":
        return "day", None
    if st == "out":
        return "out", None
    return None


def _matcher(season: int):
    """(club, folded name) -> player, from the club's roster listings and game rosters this season, plus names that
    belong to one player only."""
    by_club: dict[tuple[str, str], set[int]] = {}
    by_last: dict[tuple[str, str], set[tuple[int, str]]] = {}
    for f in ("rosters.parquet", "game_rosters.parquet", "availability.parquet"):
        t = TABLES / str(season) / f
        if t.exists():
            for pid, team, first, last in pl.read_parquet(t, columns=["player_id", "team", "first", "last"]).drop_nulls().unique().iter_rows():
                by_club.setdefault((team, _fold(f"{first} {last}")), set()).add(pid)
                by_last.setdefault((team, _fold(last)), set()).add((pid, _fold(first)))
    unique = known_names(season)

    def find(team: str | None, name: str | None) -> int | None:
        if not name:
            return None
        n = _fold(name)
        ids = by_club.get((team, n), set())
        if len(ids) == 1:
            return next(iter(ids))
        if n in unique:
            return unique[n]
        # "Sam" for Samuel, "Joe" for Joseph: one player on the club with that last name and the same first initial
        first, _, last = n.partition(" ")
        hits = {pid for pid, f in by_last.get((team, last), set()) if f[:1] == first[:1]}
        return next(iter(hits)) if len(hits) == 1 else None
    return find


def build(season: int) -> dict:
    files = sorted(raw_dir(season).glob("*.json.gz")) if raw_dir(season).exists() else []
    find = _matcher(season)
    rows, prev, unmatched = [], None, set()
    for f in files:
        day = f.name.split(".")[0]
        with gzip.open(f, "rt") as fh:
            entries = json.load(fh)
        now = {}
        for e in entries:
            pid = find(e.get("team"), e.get("name"))
            if pid is None:
                unmatched.add(e.get("name"))
                continue
            kind = classify(e, season)
            if kind is None:
                continue
            now[pid] = e
            rows.append({"date": min(e.get("date") or day, day), "player_id": pid, "name": e.get("name"), "section": e.get("team"),
                         "status": kind[0], "injury": _injury(e) if kind[0] not in ("suspended", "personal", "unsigned") else None,
                         "timeline": kind[1], "note": None, "url": PAGE})
        if len(entries) >= MIN_ENTRIES:
            if prev is not None:
                for pid, e in prev.items():
                    if pid not in now:
                        rows.append({"date": day, "player_id": pid, "name": e.get("name"), "section": e.get("team"),
                                     "status": "cleared", "injury": None, "timeline": None, "note": None, "url": PAGE})
            prev = now
    path(season).parent.mkdir(parents=True, exist_ok=True)
    table = (pl.DataFrame(rows, schema=SCHEMA).unique(["date", "player_id", "status"], keep="last", maintain_order=True)
             .sort("date", "player_id"))
    table.write_parquet(path(season))
    return {"days": len(files), "reports": table.height, "unmatched": sorted(n for n in unmatched if n)}


def load(season: int) -> pl.DataFrame:
    return pl.read_parquet(path(season)) if path(season).exists() else pl.DataFrame(schema=SCHEMA)
