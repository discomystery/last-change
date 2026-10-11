"""Small wording fixes shared by the generated sentences.

"NY Rangers" reads badly as a place ("We gave NY Rangers a 51% chance", "NY Rangers’s power play"), so in sentences
the two New York clubs go by nickname: "the Rangers", "the Rangers’", and nicknames take plural verbs. Compact
stat lines ("NY Rangers 5th for power play") keep the place.
"""
import re

PLURAL = {"has": "have", "is": "are", "was": "were", "does": "do", "wins": "win", "faces": "face", "starts": "start"}
_NY = r"(?:Rangers|Islanders)"


def place(p: str) -> str:
    """The name to use in a sentence: the place, or "the Rangers" for the New York clubs."""
    return "the " + p[3:] if p.startswith("NY ") else p


def places(ps: dict[str, str]) -> dict[str, str]:
    return {t: place(p) for t, p in ps.items()}


def tidy(text: str | None) -> str | None:
    """Fixes what `place` leaves behind: possessives, verb agreement and a "the" that starts a sentence."""
    if not text:
        return text
    text = re.sub(rf"\b(the {_NY})(’|')s\b", r"\1\2", text)
    text = re.sub(rf"\b(the {_NY}) ({'|'.join(PLURAL)})\b", lambda m: f"{m[1]} {PLURAL[m[2]]}", text)
    text = re.sub(rf"(^|[.!?]\s+)the ({_NY})\b", r"\1The \2", text)
    return text


def number_clashes(season: int, fmt) -> dict[int, str]:
    """Names that would be ambiguous: two teammates this season who read the same once formatted by `fmt` (a row of
    players.parquet to a name), like Vancouver's two Elias Petterssons or "A. Johansson" in Detroit. Each gets his
    sweater number added, "E. Pettersson (#40)". Returns only those players."""
    from collections import defaultdict

    import polars as pl

    from pipeline.config import TABLES
    path = TABLES / str(season) / "players.parquet"
    if not path.exists():
        return {}
    p = pl.read_parquet(path).sort("game_id").unique(["player_id", "team_id"], keep="last")
    out = {}
    for _, g in p.group_by("team_id"):
        rows = {r["player_id"]: r for r in g.iter_rows(named=True)}
        labels = defaultdict(set)
        for pid, r in rows.items():
            labels[fmt(r)].add(pid)
        for lab, ids in labels.items():
            if len(ids) > 1:
                for pid in ids:
                    out[pid] = f"{lab} (#{rows[pid]['number']})" if rows[pid].get("number") is not None else lab
    return out


def initials(r: dict) -> str:
    return f"{(r.get('first') or '?')[0]}. {r['last']}"


def full(r: dict) -> str:
    return f"{r['first']} {r['last']}"
