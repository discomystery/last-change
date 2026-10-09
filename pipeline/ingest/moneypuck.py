"""MoneyPuck shot files (expected goals). Non-commercial use, credit MoneyPuck.com on the site.

Only the files listed on https://moneypuck.com/data.htm are downloaded.
"""
import io
import zipfile

import polars as pl

from pipeline.config import CURRENT_SEASON, MONEYPUCK_DL, RAW
from pipeline.ingest.client import get


def shots_path(season: int):
    return RAW / "moneypuck" / f"shots_{season}.zip"


def download_shots(season: int, *, refresh: bool | None = None) -> bool:
    """Past seasons are fetched once; the current season's file changes nightly."""
    path = shots_path(season)
    refresh = season == CURRENT_SEASON if refresh is None else refresh
    if path.exists() and not refresh:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(get(f"{MONEYPUCK_DL}/shots_{season}.zip").content)
    return True


def read_shots(season: int) -> pl.DataFrame:
    with zipfile.ZipFile(shots_path(season)) as z:
        name = next(n for n in z.namelist() if n.endswith(".csv"))
        return pl.read_csv(io.BytesIO(z.read(name)), infer_schema_length=20000)
