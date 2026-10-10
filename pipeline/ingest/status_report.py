"""Injury and lineup news from the NHL.com Status Report, one article a day ("NHL Status Report", at
www.nhl.com/news/nhl-status-report-news-and-notes-{month}-{day}-{year}).

It is the only public league source that says WHY a player is out and for how long. It covers only the players the
league writes about that day (roughly 10 to 30), so silence means "nothing reported", never "healthy". Each player
named is read by fixed rules (no free-text generation): the sentence that first names him is matched against phrase
lists in a set order, and the injury type and timeline are pulled out as short phrases.

Raw: data/raw/status/{season}/{date}.json.gz holds the article's headline, body (the page's own JSON-LD articleBody)
and its player links, so the rules can be re-run offline. Past days are fetched once (a day with no report is saved
empty); today's article is fetched again on every run because it is updated through the day.

Table: data/tables/{season}/status_reports.parquet with date, player_id, name, section (the club heading it sat
under), status, injury, timeline, note (the sentence, at most 240 characters), url. Statuses, most serious first:
  season       out for the season
  long         out for weeks or months, surgery, long-term injured reserve, re-evaluated in N weeks
  ir           placed on (or remains on) injured reserve
  week         week to week
  personal     personal or family reasons
  suspended    suspended
  day          day to day, game-time decision, questionable
  out          will not play / did not play, no timeline given
  playing      will play, returns, activated, makes his debut, moves into the lineup
"""
import gzip
import json
import re
import unicodedata
from datetime import date as Date, timedelta
from html import unescape

import httpx
import polars as pl

from pipeline.config import CURRENT_SEASON, OFFLINE, PLAYOFFS, RAW, REGULAR, TABLES
from pipeline.ingest.client import get
from pipeline.ingest.rosters import today

SCHEMA = {"date": pl.Utf8, "player_id": pl.Int64, "name": pl.Utf8, "section": pl.Utf8, "status": pl.Utf8, "injury": pl.Utf8,
          "timeline": pl.Utf8, "note": pl.Utf8, "url": pl.Utf8}
MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"]
_NUM = r"(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|a few|several)"
# Checked in this order; the first match wins (most serious first, so "day to day ... placed on injured reserve" is ir).
RULES = [
    ("playing", r"activated (?:off|from) (?:long-term )?injured reserve|made (?:his|her) (?:season |NHL )?debut|"
                r"was in the lineup|returned to the lineup|\bstarted (?:for|in goal)|cleared to (?:play|return)"),
    ("season", r"out for the (?:rest of the |remainder of the )?season|season-ending|miss the (?:rest|remainder) of the season"),
    ("long", rf"long-term injured reserve|\bLTIR\b|\bout (?:at least |about |approximately |another |up to )?{_NUM}(?:[- ]to[- ]{_NUM}|-\d+)? (?:weeks|months)"
             rf"|re-evaluated in {_NUM}(?:[- ]to[- ]{_NUM}|-\d+)? (?:weeks|months)|\bout indefinitely|extended period|"
             r"\bsurgery\b|\bmonths\b"),
    ("ir", r"injured reserve|\bIR\b"),
    ("week", r"week[- ]to[- ]week"),
    ("personal", r"personal reasons|family reasons|birth of|paternity|bereavement"),
    ("suspended", r"\bsuspended\b|\bsuspension\b"),
    ("assigned", r"assigned to|loaned to|sent (?:down )?to (?:the )?AHL"),
    ("recalled", r"\brecalled\b|called up"),
    ("day", r"day[- ]to[- ]day|game-time decision|\bquestionable\b|\bdoubtful\b|not (?:been )?ruled out|"
            r"(?:could|may|might|hopes to|hopeful to) (?:return|play)|\bhopeful\b"),
    ("out", r"will not play|won't play|did not play|is out\b|will be out|ruled out|will miss|\bunavailable\b|\bsidelined\b|"
            r"not expected to play|out of the lineup|remain(?:s|ed)? out|\billness\b"),
    ("playing", r"will play|will return|is expected to play|expected to return|is set to return|will make (?:his|her) (?:season |NHL )?debut|"
                r"return(?:s|ed)? to the lineup|into the lineup|in the lineup|available to play|will start\b"),
]
INJURY = re.compile(r"(?:\(([a-z][a-z\- ]{2,25})\))|((?:upper|lower)[- ]body injury|undisclosed injury|illness|concussion|"
                    r"(?:knee|ankle|shoulder|hand|wrist|foot|groin|back|neck|hip|head|leg|arm|finger|thumb|elbow|abdominal|"
                    r"adductor|hamstring|oblique|rib|jaw|eye|face|facial|achilles|quad|calf|core) (?:injury|surgery|fracture|"
                    r"strain|sprain|issue))", re.I)
TIMELINE = re.compile(rf"(out (?:for the (?:rest of the |remainder of the )?season|indefinitely|(?:at least |about |approximately |another |up to )?"
                      rf"{_NUM}(?:[- ]to[- ]{_NUM}|-\d+)? (?:weeks|months))|re-evaluated in {_NUM}(?:[- ]to[- ]{_NUM}|-\d+)? (?:weeks|months)|"
                      r"week[- ]to[- ]week|day[- ]to[- ]day|season-ending)", re.I)
LINK = re.compile(r'<a href="(?:https://www\.nhl\.com)?/player/[a-z0-9\-]+-(\d{7})"[^>]*>([^<]+)</a>')


def url_for(day: str) -> str:
    d = Date.fromisoformat(day)
    return f"https://www.nhl.com/news/nhl-status-report-news-and-notes-{MONTHS[d.month - 1]}-{d.day}-{d.year}"


def raw_path(season: int, day: str):
    return RAW / "status" / str(season) / f"{day}.json.gz"


def path(season: int):
    return TABLES / str(season) / "status_reports.parquet"


def _extract(html: str) -> dict:
    """Headline, body and player links from the article page; empty if the page has no status report."""
    ld = {}
    for m in re.finditer(r'<script type="application/ld(?:&#x2B;|\+)json">(.*?)</script>', html, re.S):
        try:
            d = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        if isinstance(d, dict) and d.get("@type") == "NewsArticle":
            ld = d
            break
    if "Status Report" not in (ld.get("headline") or ""):
        return {}
    links = [[int(pid), unescape(text).strip()] for pid, text in LINK.findall(html)]
    return {"headline": ld.get("headline"), "body": ld.get("articleBody") or "", "links": links,
            "modified": ld.get("dateModified")}


def fetch(season: int, day: str) -> dict:
    """Fetch and save one day's article. A missing article is saved as {} so a past day is not asked for again."""
    try:
        page = _extract(get(url_for(day)).text)
    except httpx.HTTPStatusError:
        page = {}
    p = raw_path(season, day)
    p.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(p, "wt") as f:
        json.dump(page, f)
    return page


def _fold(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)).lower()


def _sentences(text: str) -> list[str]:
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)  # markdown links to plain text
    text = re.sub(r"[*_]", "", text)
    # Split at sentence ends, but not after "St." (St. Louis), "Jr.", "No. 1", initials or month abbreviations.
    parts = re.split(r"(?<!\bSt\.)(?<!\bJr\.)(?<!\bSr\.)(?<!\bNo\.)(?<!\bDr\.)(?<!\bMt\.)(?<!\b[A-Z]\.)(?<!\bJan\.)(?<!\bFeb\.)(?<!\bAug\.)"
                     r"(?<!\bSept\.)(?<!\bOct\.)(?<!\bNov\.)(?<!\bDec\.)(?<=[.!?])\s+(?=[A-Z\"“‘(])|(?<=[.!?][”\"’])\s+(?=[A-Z\"“‘(])|"
                     r"\s(?:\.\.\.|…)\s", text)
    return [p.strip() for p in parts if p.strip()]


def classify(sentence: str) -> str | None:
    for status, pattern in RULES:
        if re.search(pattern, sentence, re.I):
            return status
    return None


def parse(page: dict, day: str, known: dict[str, int] | None = None) -> list[dict]:
    """One row per player the article names with a recognisable status. `known` maps folded full names to player ids
    for players the article mentions without a link."""
    if not page.get("body"):
        return []
    names = {_fold(n): pid for pid, n in page.get("links", [])}
    for n, pid in (known or {}).items():
        names.setdefault(n, pid)
    out, seen = [], set()
    for block in re.split(r"##\s*", page["body"]):
        head, _, text = block.partition("\n")
        section = re.sub(r"[*_]", "", head).strip() if text else ""
        sents = _sentences(text or block)
        for i, s in enumerate(sents):
            fs = _fold(s)
            for n, pid in names.items():
                if pid in seen or not re.search(rf"(?<![a-z]){re.escape(n)}(?![a-z])", fs):
                    continue
                # Read the sentence that names him; if it says nothing about availability, the next one about him may.
                last = n.split()[-1]
                follow = [t for t in sents[i + 1:i + 3] if re.search(rf"(?<![a-z]){re.escape(last)}(?![a-z])", _fold(t))]
                status, used = None, s
                for cand in [s, *follow]:
                    status = classify(_strip_others(cand, n, names))
                    if status:
                        used = cand
                        break
                if not status:
                    continue
                seen.add(pid)
                own = _strip_others(used, n, names)
                own = own[max(_fold(own).find(n), 0):]  # his own injury in "X (lower body) and Y (upper body)"
                inj = INJURY.search(own) or INJURY.search(used) or INJURY.search(s)
                tl = TIMELINE.search(used)
                out.append({"date": day, "player_id": pid, "name": next((t for p, t in page.get("links", []) if p == pid), n.title()),
                            "section": section, "status": status,
                            "injury": (inj.group(1) or inj.group(2)).lower().replace("-", " ") if inj else None,
                            "timeline": tl.group(1).lower() if tl else None, "note": used[:240], "url": url_for(day)})
    return out


def _strip_others(sentence: str, name: str, names: dict[str, int]) -> str:
    """Drop the clause about another player in a sentence that names two ("X will move into the lineup for Y, who is
    out"), so his status is read from his own part. Shared sentences ("X and Y are each day to day") are kept whole."""
    fs = _fold(sentence)
    me = fs.find(name)
    others = [fs.find(o) for o in names if o != name and o in fs]
    later = [o for o in others if o > me]
    start = 0
    if any(o < me for o in others):
        before = fs[max(o for o in others if o < me):me]
        if not re.search(r"(?:,|\band\b|&)\s*$", re.sub(r"\([^)]*\)", "", before)):
            start = me  # another player's clause comes first ("..., with Y promoted"): read from his name on
    if not later:
        return sentence[start:]
    cut = min(later)
    between = fs[me + len(name):cut]
    if re.fullmatch(r"\s*(?:\([^)]*\)\s*)?(?:,|and|&)\s*", between):
        return sentence[start:]  # a list of players sharing one status
    return sentence[start:cut]


def known_names(season: int) -> dict[str, int]:
    """Folded full names of every player seen this season and last, for unlinked mentions. A name shared by two
    players is left out rather than guessed."""
    seen: dict[str, set[int]] = {}
    for s in (season - 1, season):
        for f in ("players.parquet", "rosters.parquet", "game_rosters.parquet"):
            t = TABLES / str(s) / f
            if not t.exists():
                continue
            df = pl.read_parquet(t, columns=["player_id", "first", "last"]).drop_nulls().unique()
            for pid, first, last in df.iter_rows():
                seen.setdefault(_fold(f"{first} {last}"), set()).add(pid)
    return {n: next(iter(p)) for n, p in seen.items() if len(p) == 1}


def season_days(season: int, day: str) -> list[str]:
    games = pl.read_parquet(TABLES / str(season) / "games.parquet").filter(pl.col("game_type").is_in([REGULAR, PLAYOFFS]))
    start = Date.fromisoformat(games["date"].min()) - timedelta(days=3)  # opening-night news comes a few days early
    end = Date.fromisoformat(day) if season == CURRENT_SEASON else Date.fromisoformat(games["date"].max())
    return [(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]


def update(season: int, day: str | None = None) -> dict:
    """Fetch any missing days (and today again), then re-read every saved article into the table."""
    day = day or today()
    fetched = 0
    if not OFFLINE:
        for d in season_days(season, day):
            if raw_path(season, d).exists() and d < day:
                continue
            try:
                fetch(season, d)
                fetched += 1
            except RuntimeError:
                pass  # tried again next run
    return {"fetched": fetched, **build(season)}


def build(season: int) -> dict:
    """Read every saved article for the season into status_reports.parquet. Safe offline."""
    folder = RAW / "status" / str(season)
    known = known_names(season)
    rows = []
    for f in sorted(folder.glob("*.json.gz")) if folder.exists() else []:
        with gzip.open(f, "rt") as fh:
            rows += parse(json.load(fh), f.name[:10], known)
    pl.DataFrame(rows, schema=SCHEMA).sort("date", "player_id").write_parquet(path(season))
    return {"reports": len({r["date"] for r in rows}), "mentions": len(rows)}


def load(season: int) -> pl.DataFrame:
    return pl.read_parquet(path(season)) if path(season).exists() else pl.DataFrame(schema=SCHEMA)
