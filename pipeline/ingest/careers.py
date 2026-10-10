"""NHL career history per player: the landing page (season totals by club, draft) and every NHL game log.

Used for the plain-English intros on player pages: where a player has played, how good he has been, and storylines
against tonight's opponent (a first game back, a run of points against them, a career record against them).

Saved per player at raw/careers/{id}.json.gz as {"fetched", "landing", "logs": {"{season}-{type}": [games]}}.
Past seasons never change, so each is fetched once; the current season's log is refetched when the player has
dressed since the last fetch.
"""
from __future__ import annotations

import gzip
import json

from pipeline.config import RAW

DIR = RAW / "careers"


def load_all() -> dict[int, dict]:
    out = {}
    if DIR.exists():
        for f in DIR.glob("*.json.gz"):
            out[int(f.name.split(".")[0])] = json.loads(gzip.open(f).read())
    return out
