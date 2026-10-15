"""The career ledger on player pages (Sora, 2026-10-15): every NHL season he has played, regular season and playoffs,
with the league's own season lines, plus the trophies he won.

Finished seasons come from the NHL player landing page saved by ingest/careers.py (one line per club, so a season
split by a trade shows each club). This season's regular-season line comes from the league's stats reports
(ingest/official_stats.py), which every update refreshes; it is one line for the season, with every club he played
for. A player whose landing page is not saved yet gets last season's line from the same reports, and `complete` is
false so the page can say the older seasons are on their way.

Written to site/public/data/careers/{id}.json for every player with a page (players/index.json):
{"cols": [...], "rows": [[...], ...], "awards": [{"name", "short", "yrs"}], "complete": bool}
Skater rows: season, game type (2 regular, 3 playoffs), club(s), GP, G, A, P, +/-, PIM, PPG, PPP, SHG, SHP, GWG, OTG,
S, ice time a game (seconds), faceoff win share. Goalie rows: season, game type, club(s), GP, GS, W, L, OTL, shots
against, goals against, shutouts, ice time (total seconds).
"""
import json
import re

from pipeline.config import CURRENT_SEASON, RAW, REGULAR, SITE_DATA
from pipeline.export import stats
from pipeline.ingest import careers

OUT = SITE_DATA / "careers"
SKATER = ["yr", "gt", "team", "gp", "g", "a", "p", "pm", "pim", "ppg", "ppp", "shg", "shp", "gwg", "otg", "s", "toi", "fo"]
GOALIE = ["yr", "gt", "team", "gp", "gs", "w", "l", "otl", "sa", "ga", "so", "toi"]
LANDING = {"gp": "gamesPlayed", "g": "goals", "a": "assists", "p": "points", "pm": "plusMinus", "pim": "pim", "ppg": "powerPlayGoals",
           "ppp": "powerPlayPoints", "shg": "shorthandedGoals", "shp": "shorthandedPoints", "gwg": "gameWinningGoals", "otg": "otGoals",
           "s": "shots", "fo": "faceoffWinningPctg", "gs": "gamesStarted", "w": "wins", "l": "losses", "otl": "otLosses",
           "sa": "shotsAgainst", "ga": "goalsAgainst", "so": "shutouts"}
SKIP_AWARDS = ("McGuire",)  # the draft combine's prospect award, not an NHL trophy
KEEP_WHOLE = ("Art Ross", "Conn Smythe", "Lady Byng", "Stanley Cup", "King Clancy", "Jack Adams")


def _clock(v: str | None) -> int | None:
    if not v or ":" not in v:
        return None
    m, s = v.split(":")
    return int(m) * 60 + int(s)


def _abbrs() -> dict[str, str]:
    p = RAW / "history" / "teams.json"
    return {t["fullName"]: t["triCode"] for t in json.loads(p.read_text())} if p.exists() else {}


def short(name: str) -> str:
    """A trophy's name as a short pill: Hart, Vezina, Jennings, Richard, Art Ross, Conn Smythe."""
    n = re.sub(r"\s*\b(Memorial|Trophy|Award)\b", "", name.replace("“", '"').replace("”", '"')).strip()
    for whole in KEEP_WHOLE:
        if whole in n:
            return whole
    n = re.sub(r'"[^"]*"\s*', "", n).strip()  # Maurice "Rocket" Richard -> Maurice Richard
    return n.split()[-1] if n else name


def _landing_rows(land: dict, goalie: bool, abbr: dict[str, str]) -> list[list]:
    rows = []
    for s in sorted(land.get("seasonTotals", []), key=lambda x: (x["season"], x["gameTypeId"], x.get("sequence", 1))):
        yr, gt = s["season"] // 10000, s["gameTypeId"]
        if gt not in (2, 3) or yr >= CURRENT_SEASON:  # this season comes from the league's reports, refreshed every update
            continue
        name = (s.get("teamName") or {}).get("default", "")
        team = abbr.get(name) or "".join(w[0] for w in name.split())[:3].upper()
        rec = {"yr": yr, "gt": gt, "team": team, **{k: s.get(v) for k, v in LANDING.items()}}
        rec["toi"] = _clock(s.get("timeOnIce" if goalie else "avgToi"))
        if rec.get("fo") is not None:
            rec["fo"] = round(rec["fo"], 4)
        rows.append([rec.get(k) for k in (GOALIE if goalie else SKATER)])
    return rows


def _official(season: int, goalie: bool) -> dict[int, list]:
    """One line per player from the league's reports for a regular season, in our column order."""
    cols, data = (stats.GOALIE, stats.goalies(season, {})) if goalie else (stats.SKATER, stats.skaters(season, {}))
    out = {}
    for r in data:
        rec = dict(zip(cols, r))
        rec |= {"yr": season, "gt": REGULAR, "fo": rec.get("fopct")}
        out[rec["id"]] = [rec.get(k) for k in (GOALIE if goalie else SKATER)]
    return out


def run(season: int = CURRENT_SEASON) -> dict:
    index = SITE_DATA / "players" / "index.json"
    if not index.exists():
        return {"skipped": "no players index"}
    players = json.loads(index.read_text())
    abbr = _abbrs()
    now = {g: _official(season, g) for g in (False, True)}
    last = {g: _official(season - 1, g) for g in (False, True)}
    OUT.mkdir(parents=True, exist_ok=True)
    keep, complete = set(), 0
    for pl_ in players:
        pid, goalie = pl_["id"], pl_.get("pos") == "G"
        land = (careers.read(pid) or {}).get("landing") or {}
        full = bool(land.get("full"))
        rows = _landing_rows(land, goalie, abbr) if full else []
        if not full and pid in last[goalie]:
            rows.append(last[goalie][pid])
        if pid in now[goalie]:
            rows.append(now[goalie][pid])
        if not rows:
            continue
        awards = [{"name": a["trophy"], "short": short(a["trophy"]), "yrs": [s // 10000 for s in a["seasons"]]}
                  for a in land.get("awards", []) if a.get("trophy") and not any(x in a["trophy"] for x in SKIP_AWARDS)]
        body = {"cols": GOALIE if goalie else SKATER, "rows": rows, "awards": awards, "complete": full}
        (OUT / f"{pid}.json").write_text(json.dumps(body, separators=(",", ":"), ensure_ascii=False))
        keep.add(f"{pid}.json")
        complete += full
    for f in OUT.glob("*.json"):  # players who no longer have a page
        if f.name not in keep:
            f.unlink()
    return {"players": len(keep), "complete": complete}
