"""Player names spelled the way the players spell them (Sora, 2026-10-13: the league drops accents from many names).

The NHL's own feeds carry each name as `{"default": "Teravainen", "fi": "Teräväinen", "cs": ...}`: the plain
`default` is what most clubs show, and the language versions often hold the real spelling. A language version is used
only when it is the same name with accents added (same letters once the marks are taken off, more marks than the
plain one), so a nickname's full form ("Will" / "William"), a different spelling or a typo in the feed is never
taken. Players born in Russia, Belarus, Ukraine or Kazakhstan keep the English spelling: their language versions are
Czech or Finnish transliterations of Cyrillic ("Naměstnikov"), not their own spelling.

Where the NHL has no accented version, Wikidata fills in: its player entries carry the NHL player id (property
P3522), so each English label is matched by id, never by name, and used by the same rule (Merzļikins, Fleury's
Marc-André, Hämeenaho). Checked 2026-10-13: 1,508 of our 1,548 players are on it; it agreed with 1,392, added accents
to 46, and every hand-made spelling below appears there in some language. `FIXES` holds the few reviewed spellings
neither source gives in English, checked by the same rule.

Names are spelled this way where they enter the tables (build/games.py, ingest/rosters.py, ingest/game_rosters.py),
and `respell` brings tables saved earlier into line, so every page, sentence and search reads the same spelling.
Player ids and page addresses never change. Name matching against other sources (NHL.com Status Report, ESPN) folds
accents away (`fold`), so it is unaffected.

Saved: data/raw/names/fields.json (each player's name fields as the NHL sent them in the play-by-play, and which seasons
were read), data/raw/names/countries.json (birth country, from the daily roster listing or the player's landing) and
data/raw/names/wikidata.json (accented English labels by NHL id, refreshed weekly; one query, skipped offline).
"""
import gzip
import json
import time
import unicodedata
from collections import Counter

import polars as pl

from pipeline.config import NHL_WEB, OFFLINE, RAW, TABLES

FOLDER = RAW / "names"
NOT_NATIVE = {"RUS", "BLR", "UKR", "KAZ"}  # their language versions are transliterations of Cyrillic
NATIVE = {"FIN": ("fi", "sv"), "SWE": ("sv",), "CZE": ("cs",), "SVK": ("sk",), "DEU": ("de",), "AUT": ("de",), "CHE": ("de", "fr"), "FRA": ("fr",)}
# Letters that do not come apart into a base letter and a mark
SPECIAL = str.maketrans({"ø": "o", "Ø": "O", "æ": "ae", "Æ": "Ae", "œ": "oe", "Œ": "Oe", "ß": "ss", "đ": "d", "Đ": "D", "ł": "l", "Ł": "L", "ı": "i"})

WIKIDATA = "https://query.wikidata.org/sparql"
WIKIDATA_DAYS = 7

# Reviewed spellings neither the NHL nor Wikidata's English label gives (id: (first, last)); Wikidata has each of these
# in other languages. Each must be the NHL's own name with accents added, or it is ignored (`_same`). French-Canadian
# names are included (Sora's choice, 2026-10-13).
FIXES: dict[int, tuple[str, str]] = {
    8484164: ("Dalibor", "Dvorský"),
    8480007: ("Jonas", "Røndbjerg"),  # Danish: the NHL's versions give the Swedish ö
    8483017: ("Frédéric", "Brunet"),
    8480426: ("Charle-Édouard", "D'Astous"),  # Wikidata's English label has "Charles"
}


def strip(s: str) -> str:
    """The name with every accent taken off ("Søgaard" -> "Sogaard")."""
    return "".join(c for c in unicodedata.normalize("NFKD", s.translate(SPECIAL)) if not unicodedata.combining(c))


def fold(s: str) -> str:
    """For matching names between sources: no accents, lower case."""
    return strip(s).lower()


def _marks(s: str) -> int:
    return sum(1 for c in s if ord(c) > 127)


def _same(plain: str, accented: str) -> bool:
    return bool(plain) and bool(accented) and strip(accented) == strip(plain) and _marks(accented) > _marks(plain)


def pick(field, country: str | None) -> str | None:
    """One name part from the NHL's field ({"default": ..., "fi": ...}, or a plain string)."""
    if not isinstance(field, dict):
        return field
    plain = field.get("default")
    if not plain or not country or country in NOT_NATIVE:
        return plain
    ok = [v for k, v in field.items() if k != "default" and _same(plain, v)]
    if not ok:
        return plain
    for lang in NATIVE.get(country, ()):  # the player's own language first, else the version most languages agree on
        if field.get(lang) in ok:
            return field[lang]
    return Counter(ok).most_common(1)[0][0]


def _split(full: str, first: str, last: str) -> tuple[str, str] | None:
    """Cut an accented full name where the plain first and last names meet ("Marc-André Fleury")."""
    for i, c in enumerate(full):
        if c == " " and strip(full[:i]) == strip(first) and strip(full[i + 1:]) == strip(last):
            return full[:i], full[i + 1:]
    return None


def spell(pid: int, first, last, country: str | None = None) -> tuple[str | None, str | None]:
    """A player's first and last name as he spells them."""
    country = country or countries().get(str(pid))
    plain = (pick(first, None), pick(last, None))
    names = [pick(first, country), pick(last, country)]
    fix = FIXES.get(pid)
    if fix:  # checked against the NHL's plain spelling, so a fix can also correct its accents ("Röndbjerg" -> "Røndbjerg")
        names = [x if _same(p, x) else n for p, x, n in zip(plain, fix, names)]
    label = wikidata().get(str(pid))
    parts = _split(label, *names) if label and all(names) else None
    if parts:
        names = [w if _marks(w) > _marks(n) else n for w, n in zip(parts, names)]
    return names[0], names[1]


# --- saved fields and countries ---------------------------------------------------------------------------------

_cache: dict[str, dict] = {}


def _load(name: str) -> dict:
    if name not in _cache:
        p = FOLDER / f"{name}.json"
        _cache[name] = json.loads(p.read_text()) if p.exists() else {}
    return _cache[name]


def _save(name: str) -> None:
    FOLDER.mkdir(parents=True, exist_ok=True)
    (FOLDER / f"{name}.json").write_text(json.dumps(_cache[name], ensure_ascii=False, sort_keys=True))


def wikidata() -> dict[str, str]:
    """NHL id -> Wikidata's English label, for labels with accents."""
    return _load("wikidata").get("labels", {})


def fetch_wikidata() -> dict:
    """Refresh the saved labels once a week; offline runs and failures keep the saved copy."""
    saved = _load("wikidata")
    if OFFLINE or time.time() - saved.get("fetched", 0) < WIKIDATA_DAYS * 86400:
        return {"skipped": True, "labels": len(saved.get("labels", {}))}
    import httpx

    query = 'SELECT ?id ?en WHERE { ?p wdt:P3522 ?id . ?p rdfs:label ?en FILTER(lang(?en) = "en") }'
    try:
        res = httpx.get(WIKIDATA, params={"query": query, "format": "json"}, timeout=120,
                        headers={"User-Agent": "Spare Line Change (github.com/discomystery/last-change)"})
        res.raise_for_status()
        rows = res.json()["results"]["bindings"]
    except Exception as e:  # a missing refresh only means a week-old copy
        print(f"wikidata unavailable: {e!r}")
        return {"failed": True, "labels": len(saved.get("labels", {}))}
    labels = {}
    for r in rows:
        name = unicodedata.normalize("NFC", r["en"]["value"])
        if _marks(name):
            labels.setdefault(r["id"]["value"], name)
    _cache["wikidata"] = {"fetched": time.time(), "labels": labels}
    _save("wikidata")
    return {"labels": len(labels)}


def countries() -> dict[str, str]:
    return _load("countries")


def note_country(pid: int, country: str | None) -> None:
    if country and countries().get(str(pid)) != country:
        countries()[str(pid)] = country
        _save("countries")


def read_seasons(seasons: list[int], again: tuple[int, ...] = ()) -> None:
    """Read the name fields from the saved play-by-play of seasons not read yet (once each; `again` re-reads a season
    still being played, about 2 seconds)."""
    from pipeline.ingest import nhl

    fields = _load("fields")
    done = set(fields.get("seasons", []))
    for s in seasons:
        if s in done and s not in again:
            continue
        folder = nhl.raw_path(s, "pbp", 0).parent
        if not folder.exists():
            continue
        for f in sorted(folder.glob("*.json.gz")):
            with gzip.open(f, "rt") as fh:
                for r in json.load(fh).get("rosterSpots", []):
                    fields.setdefault("players", {})[str(r["playerId"])] = [r.get("firstName"), r.get("lastName")]
        fields["seasons"] = sorted(done | {s})
        done.add(s)
        _save("fields")


def _fill_countries(limit: int = 150) -> int:
    """Birth country for players whose spelling would change but whose country we have not seen (from his landing)."""
    if OFFLINE:
        return 0
    from pipeline.ingest.client import get

    known = countries()
    todo = [pid for pid, (f, l) in _load("fields").get("players", {}).items()
            if pid not in known and (pick(f, "?") != pick(f, None) or pick(l, "?") != pick(l, None))]
    for pid in todo[:limit]:
        try:
            note_country(int(pid), get(f"{NHL_WEB}/v1/player/{pid}/landing").json().get("birthCountry"))
        except Exception as e:  # one player failing should not stop the run; he keeps the plain spelling
            print(f"no country for {pid}: {e!r}")
    return min(len(todo), limit)


def spellings(seasons: list[int], again: tuple[int, ...] = ()) -> dict[int, tuple[str | None, str | None]]:
    """Every player seen in a game: id -> (first, last) as he spells them (for most, the NHL's plain spelling)."""
    read_seasons(seasons, again)
    _fill_countries()
    return {int(pid): spell(int(pid), f, l) for pid, (f, l) in _load("fields").get("players", {}).items()}


def respell(seasons: list[int], again: tuple[int, ...] = ()) -> dict:
    """Bring the saved player, roster and game-roster tables into line with `spellings`, both ways (a spelling taken out
    of `FIXES` goes back to the NHL's). A player only seen in roster listings is spelled from the listed name. Only
    rewrites a name whose letters are the same once accents are taken off, so it can never swap one player's name for
    another's."""
    names = spellings(seasons, again)

    def target(pid: int, first: str | None, last: str | None) -> tuple[str | None, str | None]:
        new = names.get(pid) or spell(pid, first, last)
        return tuple(n if n and o and n != o and strip(n) == strip(o) else o for n, o in zip(new, (first, last)))

    out = {}
    for s in seasons:
        for table in ("players", "rosters", "game_rosters"):
            p = TABLES / str(s) / f"{table}.parquet"
            if not p.exists():
                continue
            t = pl.read_parquet(p)
            if not {"player_id", "first", "last"} <= set(t.columns):
                continue
            keys = t.select("player_id", "first", "last").unique()
            fixed = [(pid, f, l, *target(pid, f, l)) for pid, f, l in keys.iter_rows()]
            fixed = [r for r in fixed if r[1:3] != r[3:5]]
            if not fixed:
                continue
            look = pl.DataFrame(fixed, schema={"player_id": pl.Int64, "first": pl.Utf8, "last": pl.Utf8, "_f": pl.Utf8, "_l": pl.Utf8}, orient="row")
            j = t.join(look, on=["player_id", "first", "last"], how="left", maintain_order="left", nulls_equal=True)
            n = j.filter(pl.col("_f").is_not_null() | pl.col("_l").is_not_null()).height
            j = j.with_columns(pl.coalesce("_f", "first").alias("first"), pl.coalesce("_l", "last").alias("last"))
            j.select(t.columns).write_parquet(p)
            out[f"{s}/{table}"] = n
    return out
