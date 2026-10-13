"""Small wording fixes shared by the generated sentences.

"NY Rangers" reads badly as a place ("We gave NY Rangers a 51% chance", "NY Rangers’s power play"), so in sentences
the two New York clubs go by nickname: "the Rangers", "the Rangers’", and nicknames take plural verbs. Compact
stat lines ("NY Rangers 5th for power play") keep the place.
"""
import re
import zlib

PLURAL = {"has": "have", "is": "are", "was": "were", "does": "do", "wins": "win", "faces": "face", "starts": "start", "plays": "play",
          "gets": "get", "gives": "give", "takes": "take", "throws": "throw", "allows": "allow", "leans": "lean", "rides": "ride",
          "uses": "use", "hits": "hit", "shoots": "shoot", "kills": "kill", "controls": "control", "makes": "make", "turns": "turn"}
_NY = r"(?:Rangers|Islanders)"


def place(p: str) -> str:
    """The name to use in a sentence: the place, or "the Rangers" for the New York clubs."""
    return "the " + p[3:] if p.startswith("NY ") else p


# Where a club plays, for "in ..." sentences. "NY Rangers" is a team, not a place, so "5-1 in the Rangers" and "Round two
# in the Rangers" were wrong (Sora, 2026-10-13). The Rangers play in New York; the Islanders on Long Island ("on", not "in").
TOWNS = {"NY Rangers": ("in", "New York"), "NY Islanders": ("on", "Long Island")}


def town(p: str) -> str:
    """The place itself, for "came to ..." or "a stop in ...": "Carolina", "New York", "Long Island"."""
    return TOWNS.get(p, ("in", p))[1]


def at(p: str) -> str:
    """Where a game was played or a player spent time: "in Carolina", "in New York", "on Long Island"."""
    prep, name = TOWNS.get(p, ("in", p))
    return f"{prep} {name}"


def places(ps: dict[str, str]) -> dict[str, str]:
    return {t: place(p) for t, p in ps.items()}


# What fans call their club in conversation (Sora, 2026-10-13: "the Canes", "the Bolts"). Only where a nickname is
# widely used in plain talk; clubs without one (Ducks, Kings, Wild, Jets...) always go by their name. Not the Rangers:
# "Blueshirts" is rarer and longer than the name, and "Rags" is tongue in cheek (Sora, 2026-10-13). Plural verbs, like the names.
SHORT = {"Hurricanes": "Canes", "Lightning": "Bolts", "Maple Leafs": "Leafs", "Canadiens": "Habs", "Senators": "Sens",
         "Penguins": "Pens", "Capitals": "Caps", "Blue Jackets": "Jackets", "Islanders": "Isles", "Predators": "Preds",
         "Avalanche": "Avs", "Blackhawks": "Hawks", "Red Wings": "Wings", "Golden Knights": "Knights", "Panthers": "Cats",
         "Oilers": "Oil"}
_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9“\"])")


def _short_poss(short: str) -> str:
    return short + ("’" if short.endswith("s") else "’s")


def nickname_once(sents: list[str], key: str = "", skip_first: bool = False, places: dict[str, str] | None = None) -> list[str]:
    """Swaps at most ONE "the Hurricanes" in a paragraph (a list of sentences) for "the Canes", so a casual paragraph that
    names a club again and again reads like a fan talking. The rule, kept sparing on purpose:
    - only a club already named at least twice in the paragraph as "the <Name>", and never its first mention;
    - never in a sentence that also has the club's full name or its place ("the Hurricanes", "Carolina");
    - one nickname per paragraph in all (stories go further: one per story, see `stories.Words.nicknames`), chosen by a stable hash of `key` so the page reads the same every run.
    `skip_first` keeps the first sentence formal (a team intro's first sentence is set as its headline).
    Callers use it only on story paragraphs and intros: never headlines, labels, tags, stat lines or call cards.
    `places` maps a full name to its place ("Hurricanes": "Carolina")."""
    places = places or {}
    found = []
    for full, short in SHORT.items():
        pat = re.compile(rf"\b([Tt]he) {full}(’s|’|'s|')?(?![\w-])")
        hits = [(i, m) for i, s in enumerate(sents) for m in pat.finditer(s)]
        if len(hits) < 2:
            continue
        first = hits[0][0]
        for i, m in hits[1:]:
            if i == first or (skip_first and i == 0):
                continue
            s = sents[i]
            if len(re.findall(rf"\b{full}\b", s)) > 1 or (places.get(full) and places[full] in s):
                continue
            found.append((i, m, short))
    if not found:
        return sents
    i, m, short = found[zlib.crc32(key.encode()) % len(found)]
    new = f"{m[1]} {_short_poss(short) if m[2] else short}"
    out = list(sents)
    out[i] = out[i][:m.start()] + new + out[i][m.end():]
    return out


def nickname_para(text: str, key: str = "", places: dict[str, str] | None = None) -> str:
    """`nickname_once` for a paragraph held as one string."""
    return " ".join(nickname_once(_SENT.split(text), key, places=places)) if text else text


def tidy(text: str | None) -> str | None:
    """Fixes what `place` leaves behind: possessives, verb agreement and a "the" that starts a sentence."""
    if not text:
        return text
    text = re.sub(rf"\b(the {_NY})(’|')s\b", r"\1\2", text)
    text = re.sub(rf"\b([Tt]he {_NY})( usually)? ({'|'.join(PLURAL)})\b", lambda m: f"{m[1]}{m[2] or ''} {PLURAL[m[3]]}", text)
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
