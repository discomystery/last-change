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
CURRENT_SEASON = int(os.environ.get("LC_SEASON", 2026))  # LC_SEASON: replay a past season (the playoff test build)
FINAL_STATES = {"OFF", "FINAL"}
REGULAR, PLAYOFFS = 2, 3
# Game types the season being built covers (per-game tables, grading, Swing, report cards). The live site is regular
# season only; the playoff test build (pipeline/playoffs) sets LC_GAME_TYPES=2,3. Fingerprints, lines and goalies stay
# regular-season there because that stage runs on tables cut before the first playoff game.
GAME_TYPES = tuple(int(t) for t in os.environ.get("LC_GAME_TYPES", "2").split(","))


def types_for(season: int) -> tuple[int, ...]:
    """Game types a table for `season` covers: LC_GAME_TYPES for the season being built (the playoff replay adds 3),
    regular season only for earlier seasons, so baselines stay regular-season."""
    return GAME_TYPES if season == CURRENT_SEASON else (REGULAR,)


# Game types that get post-game pages (the playoff test build: playoffs only, with the regular season's games still in
# the tables behind them).
PAGE_TYPES = tuple(int(t) for t in os.environ.get("LC_PAGE_TYPES", ",".join(map(str, GAME_TYPES))).split(","))


def season_id(start_year: int) -> str:
    return f"{start_year}{start_year + 1}"
