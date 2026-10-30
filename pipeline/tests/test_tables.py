"""Saved tables kept in memory for a run (pipeline/tables.py): read again once the file changes on disk."""
import os

import polars as pl

from pipeline import tables


def test_a_rewritten_table_is_read_fresh(tmp_path):
    p = tmp_path / "t.parquet"
    pl.DataFrame({"a": [1], "b": ["x"]}).write_parquet(p)
    assert tables.read(p, columns=["b", "a"]).columns == ["b", "a"]
    assert tables.read(p)["a"].to_list() == [1]
    pl.DataFrame({"a": [1, 2], "b": ["x", "y"]}).write_parquet(p)
    os.utime(p, ns=(1, 1))  # even with the old time, the size gives it away; a new time alone does too
    assert tables.read(p)["a"].to_list() == [1, 2]
