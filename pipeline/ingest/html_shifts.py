"""Backup shift source: the NHL's official HTML time-on-ice reports.

Used only when the shift-chart API returns nothing for a game. Output rows mimic the API shape so
the rest of the pipeline does not care where shifts came from.
"""
import re

from pipeline.config import season_id
from pipeline.ingest.client import get

REPORTS = "https://www.nhl.com/scores/htmlreports"
_HEADING = re.compile(r'playerHeading[^>]*>\s*(\d+)\s+([^<]+)<')
_ROW = re.compile(r"<td[^>]*>(\d+)</td>\s*<td[^>]*>(\d+|OT)</td>\s*<td[^>]*>(\d+:\d+) / [\d:]+</td>\s*<td[^>]*>(\d+:\d+) / [\d:]+</td>")


def _mmss(clock: str) -> str:
    m, s = clock.split(":")
    return f"{int(m):02d}:{s}"


def parse_report(html: str, team_id: int, id_by_number: dict[int, int]) -> list[dict]:
    rows = []
    blocks = re.split(r'(?=class="playerHeading)', html)[1:]
    for block in blocks:
        head = _HEADING.search(block)
        if not head:
            continue
        player_id = id_by_number.get(int(head.group(1)))
        if player_id is None:
            continue
        for num, per, start, end in _ROW.findall(block):
            rows.append({"playerId": player_id, "teamId": team_id, "period": 4 if per == "OT" else int(per),
                         "startTime": _mmss(start), "endTime": _mmss(end), "shiftNumber": int(num), "typeCode": 517})
    return rows


def fetch(season: int, game_id: int, pbp: dict) -> dict:
    code = str(game_id)[4:]
    data = []
    for prefix, side in (("TH", "homeTeam"), ("TV", "awayTeam")):
        team_id = pbp[side]["id"]
        numbers = {r["sweaterNumber"]: r["playerId"] for r in pbp["rosterSpots"] if r["teamId"] == team_id}
        html = get(f"{REPORTS}/{season_id(season)}/{prefix}{code}.HTM").content.decode("latin-1")
        data.extend(parse_report(html, team_id, numbers))
    return {"source": "html-toi-report", "data": data}
