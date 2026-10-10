"""Paths and constants shared by the pipeline."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Both can be pointed elsewhere, e.g. at a copy of the tables cut off at a past date (see export/pregame.py).
DATA = Path(os.environ.get("LAST_CHANGE_DATA", ROOT / "data"))
RAW = DATA / "raw"
TABLES = DATA / "tables"
SITE_DATA = Path(os.environ.get("LAST_CHANGE_SITE_DATA", ROOT / "site" / "public" / "data"))

NHL_WEB = "https://api-web.nhle.com"
NHL_STATS = "https://api.nhle.com/stats/rest/en"
MONEYPUCK_DL = "https://peter-tanner.com/moneypuck/downloads"
USER_AGENT = "last-change-hockey-site/0.1 (non-commercial fan project; github.com/discomystery/last-change)"
REQUESTS_PER_SECOND = 2.0
# Offline: recompute and republish from data already saved, without contacting the NHL or MoneyPuck (set by the
# "Publish changes" workflow). Anything that would fetch keeps the last saved copy instead.
OFFLINE = os.environ.get("LC_OFFLINE") == "1"

# Seasons are named by start year: 2026 means 2026-27.
FULL_SEASONS = [2023, 2024, 2025]
CURRENT_SEASON = 2026
FINAL_STATES = {"OFF", "FINAL"}
REGULAR, PLAYOFFS = 2, 3


def season_id(start_year: int) -> str:
    return f"{start_year}{start_year + 1}"
