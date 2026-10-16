import numpy as np
import polars as pl

from pipeline.metrics import faceoffs


def _synthetic(n=40000, seed=1):
    rng = np.random.default_rng(seed)
    skill = {1: 0.4, 2: 0.0, 3: 0.0, 4: -0.4, 5: 0.0, 6: 0.0}
    hp, ap = rng.choice(list(skill), n), rng.choice(list(skill), n)
    keep = hp != ap
    hp, ap = hp[keep], ap[keep]
    pp = rng.random(len(hp)) < 0.1
    pk = ~pp & (rng.random(len(hp)) < 0.1)
    z = 0.02 + np.array([skill[p] for p in hp]) - np.array([skill[p] for p in ap]) + 0.2 * pp - 0.2 * pk
    y = rng.random(len(hp)) < 1 / (1 + np.exp(-z))
    return pl.DataFrame({"hp": hp, "ap": ap, "y": y, "hz": ["N"] * len(hp), "pp": pp, "pk": pk})


def test_planted_skills_and_manpower_recovered():
    skill, ctx, _ = faceoffs.fit(_synthetic())
    assert skill[1] > skill[2] + 0.25 and skill[4] < skill[2] - 0.25
    assert ctx[0] > 0.1 and ctx[1] < -0.1  # the power-play side wins more draws, the shorthanded side fewer
