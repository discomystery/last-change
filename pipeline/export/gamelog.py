"""Recent games on player pages (Sora, 2026-10-18): each player's last ten NHL games, newest first, with the
league's own boxscore line for each, so every number matches NHL.com.

Games come from the saved boxscores of this season and last (regular season and playoffs), so a player early in the
season, or one who hasn't played yet this season, still gets ten games with last season's at the end. A goalie's game
counts only if he played (the boxscore also lists the backup with 0:00).

Written to site/public/data/gamelog/{id}.json for every player with a page (players/index.json):
{"cols": [...], "rows": [[...], ...]}
Also gamelog/teams.json, who has been in net (Sora, 2026-10-18: she wanted to see quickly which of a club's goalies
had been starting and who played last): {team: [[gid, date, opp, home, us, them, end, starter id, [relief ids]], ...]}
for every game this season, newest first.
Every row starts with game id, date, season, game type (2 regular, 3 playoffs), his club, the opponent, home (1/0),
goals for, goals against, and how it ended (REG, OT, SO). Skaters then: G, A, P, +/-, shots on goal, hits, blocks,
PIM, power-play goals, ice time (seconds). Goalies: decision (W, L, O or null), started (1/0), shots against, saves,
goals against, ice time (seconds).
"""
import gzip
import json

from pipeline.config import CURRENT_SEASON, RAW, SITE_DATA

OUT = SITE_DATA / "gamelog"
GAMES = 10
GAME = ["gid", "date", "yr", "gt", "team", "opp", "home", "us", "them", "end"]
SKATER = GAME + ["g", "a", "p", "pm", "s", "hit", "blk", "pim", "ppg", "toi"]
GOALIE = GAME + ["dec", "gs", "sa", "sv", "ga", "toi"]
FINAL = ("OFF", "FINAL")


def _secs(v: str | None) -> int:
    if not v or ":" not in v:
        return 0
    m, s = v.split(":")
    return int(m) * 60 + int(s)


def _boxes(season: int):
    folder = RAW / str(season) / "box"
    for f in sorted(folder.glob("*.json.gz")) if folder.exists() else []:
        try:
            box = json.loads(gzip.decompress(f.read_bytes()))
        except (OSError, ValueError):
            continue
        if box.get("gameType") in (2, 3) and box.get("gameState") in FINAL and box.get("playerByGameStats"):
            yield box


def lines(seasons: list[int]) -> dict[int, list[list]]:
    """player id -> his boxscore lines over these seasons, oldest first."""
    out: dict[int, list[list]] = {}
    for season in seasons:
        for box in _boxes(season):
            end = (box.get("gameOutcome") or {}).get("lastPeriodType") or "REG"
            for side, other in (("homeTeam", "awayTeam"), ("awayTeam", "homeTeam")):
                us, them = box[side], box[other]
                game = [box["id"], box["gameDate"], season, box["gameType"], us["abbrev"], them["abbrev"],
                        int(side == "homeTeam"), us.get("score", 0), them.get("score", 0), end]
                groups = box["playerByGameStats"].get(side, {})
                for pl in groups.get("forwards", []) + groups.get("defense", []):
                    out.setdefault(pl["playerId"], []).append(game + [
                        pl.get("goals", 0), pl.get("assists", 0), pl.get("points", 0), pl.get("plusMinus", 0), pl.get("sog", 0),
                        pl.get("hits", 0), pl.get("blockedShots", 0), pl.get("pim", 0), pl.get("powerPlayGoals", 0), _secs(pl.get("toi"))])
                played = [pl for pl in groups.get("goalies", []) if _secs(pl.get("toi"))]
                # A box saved as FINAL, before the NHL marks it OFF, has no starter flags: the busiest goalie started.
                flagged = any(pl.get("starter") is not None for pl in played)
                first = max(played, key=lambda pl: _secs(pl.get("toi")), default=None)
                for pl in groups.get("goalies", []):
                    toi = _secs(pl.get("toi"))
                    if not toi:
                        continue  # dressed as the backup and never went in
                    sa = pl.get("shotsAgainst", 0)
                    ga = pl.get("goalsAgainst", 0)
                    out.setdefault(pl["playerId"], []).append(game + [
                        pl.get("decision"), int(bool(pl.get("starter")) if flagged else pl is first), sa, pl.get("saves", sa - ga), ga, toi])
    for rows in out.values():
        rows.sort(key=lambda r: (r[1], r[0]))
    return out


def net(season: int) -> dict[str, list[list]]:
    """team -> every game this season, newest first, with who started in net and who came on in relief."""
    out: dict[str, list[list]] = {}
    for box in _boxes(season):
        if box["gameType"] != 2:
            continue
        end = (box.get("gameOutcome") or {}).get("lastPeriodType") or "REG"
        for side, other in (("homeTeam", "awayTeam"), ("awayTeam", "homeTeam")):
            played = [pl for pl in box["playerByGameStats"].get(side, {}).get("goalies", []) if _secs(pl.get("toi"))]
            if not played:
                continue
            flagged = any(pl.get("starter") is not None for pl in played)
            starter = next((pl for pl in played if pl.get("starter")), None) if flagged else max(played, key=lambda pl: _secs(pl.get("toi")))
            us, them = box[side], box[other]
            out.setdefault(us["abbrev"], []).append([
                box["id"], box["gameDate"], them["abbrev"], int(side == "homeTeam"), us.get("score", 0), them.get("score", 0), end,
                starter["playerId"] if starter else None, [pl["playerId"] for pl in played if pl is not starter]])
    for rows in out.values():
        rows.sort(key=lambda r: (r[1], r[0]), reverse=True)
    return out


def run(season: int = CURRENT_SEASON) -> dict:
    index = SITE_DATA / "players" / "index.json"
    if not index.exists():
        return {"skipped": "no players index"}
    players = json.loads(index.read_text())
    every = lines([season - 1, season])
    OUT.mkdir(parents=True, exist_ok=True)
    keep = set()
    for pl_ in players:
        pid, goalie = pl_["id"], pl_.get("pos") == "G"
        cols = GOALIE if goalie else SKATER
        rows = [r for r in every.get(pid, []) if len(r) == len(cols)][-GAMES:][::-1]
        if not rows:
            continue
        (OUT / f"{pid}.json").write_text(json.dumps({"cols": cols, "rows": rows}, separators=(",", ":"), ensure_ascii=False))
        keep.add(f"{pid}.json")
    (OUT / "teams.json").write_text(json.dumps(net(season), separators=(",", ":")))
    keep.add("teams.json")
    for f in OUT.glob("*.json"):  # players who no longer have a page
        if f.name not in keep:
            f.unlink()
    return {"players": len(keep)}
