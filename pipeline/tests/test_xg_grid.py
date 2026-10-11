import polars as pl

from pipeline.export.xg_grid import CELL, X0, grid


def test_grid_shrinks_thin_types_toward_the_square():
    # 20 wrist shots at 10% and 2 slap shots at 40% in one square: the slap value leans on the square's average.
    rows = [{"x": X0 + 1.0, "y_abs": 1.0, "shot_type": "wrist", "xg": 0.10}] * 20 + [{"x": X0 + 1.0, "y_abs": 1.0, "shot_type": "slap", "xg": 0.40}] * 2
    g = grid(pl.DataFrame(rows))
    wrist, slap = g["grid"]["wrist"][0][0], g["grid"]["slap"][0][0]
    avg = 100 * (20 * 0.10 + 2 * 0.40) / 22
    assert wrist < avg < slap < 40
    assert g["grid"]["wrist"][0][1] is None  # an empty square stays blank
    assert g["cell"] == CELL
