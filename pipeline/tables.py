"""Saved tables read once per run. Story code asks for the same season table for every game; reading the file again
each time was most of a post-game page's cost. A copy is kept while the file is unchanged on disk (path, modified
time and size), so a table rebuilt later in the same run is read fresh."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import polars as pl


def stamp(*paths: Path) -> tuple:
    """What identifies the files' current contents; a missing file counts too (it may appear later in the run)."""
    out = []
    for p in paths:
        try:
            s = p.stat()
            out.append((str(p), s.st_mtime_ns, s.st_size))
        except FileNotFoundError:
            out.append((str(p), None, None))
    return tuple(out)


@lru_cache(maxsize=48)
def _read(path: str, mtime: int, size: int) -> pl.DataFrame:
    return pl.read_parquet(path)


def read(path: Path, columns: list[str] | None = None) -> pl.DataFrame:
    """`pl.read_parquet(path, columns=...)`, from memory when the file has not changed. Raises like it when missing."""
    s = Path(path).stat()
    df = _read(str(path), s.st_mtime_ns, s.st_size)
    return df.select(columns) if columns else df
