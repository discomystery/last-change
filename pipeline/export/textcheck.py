"""Checks on the generated text, run on finished pages (natural-text plan, stage 2).

Two kinds of findings:
- "error": a definite slip a reader would notice ("1 seconds", "a 8-3 rout", "21th", a banned word). Tests fail on these.
- "style": something that reads as generated (the same phrase twice in one story, two sentences in a row opening the
  same way, a stock line used in more than one game the same night). Counted and reported, not failed, so the
  numbers can be tracked while the wording work goes on.

`python -m pipeline.export.textcheck` prints the counts for the post-game and preview stories now in site data.
The house style sheet these rules come from is the Claude Doc "House style for generated text".
"""
import json
import re
import sys
from collections import Counter, defaultdict

from pipeline.export import grammar

SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9“\"])")
NAME = re.compile(r"\b[A-Z][\w’'.\-]*(?:\s+(?:(?:de|van|von|del|la)\s+)?[A-Z][\w’'.\-]*)*")
NUMBER = re.compile(r"\d[\d,:.%\-]*")

# (pattern, what is wrong). Case-insensitive unless the pattern says otherwise.
ERRORS = [
    (r"\b1 seconds\b", "\"1 seconds\""),
    (r"\b0 seconds\b", "\"0 seconds\" (say \"less than a second\")"),
    (r"\ba (?:Oilers|Islanders|Avalanche|Isles|Avs|Oil|Ottawa|Edmonton|Anaheim)\b", "\"a\" before a vowel"),
    (r"[A-Z][\w’.\-]+ and [A-Z][\w’.\-]+(?: [A-Z][\w’.\-]+)? and [A-Z]", "list joined with \"and ... and\""),
    (r"\bdeserv", "\"deserve\" (MoneyPuck's word; never on the site)"),
    (r"\bmiracle\b", "\"miracle\" lean"),
    (r"\b(?:odds|betting|bettor|parlay|pick 'em|pick ’em|money on|the spread)\b", "betting language"),
    # our own guess that a player was hurt (a league or club report quoted after the game, "placed on injured
    # reserve with a lower-body injury", is allowed: export/timeline.injury_news)
    (r"\b(?:is|was|were|got|gets|been|being|looked|appeared) (?:hurt|injured)\b|\bleft (?:with|because of|due to) an?\b[^.]*\binjury\b",
     "guesses that a player was hurt (say why only when a league or club report did)"),
    (r"\b\d+\.\d+ minutes\b", "ice time as decimal minutes (write m:ss)"),
    (r"\b\d{1,2}:\d{2} ?(?:ET|EST|EDT|PT|CT|MT)\b", "time zone beside a time"),
    (r" ,|\s{2,}|\.\.(?!\.)", "stray space or punctuation"),
    (r"[a-z]'s\b", "straight apostrophe (use ’)"),
    (r"\beven\b[^.]*\beven\b", "\"even\" twice in one sentence"),
]
ERRORS = [(re.compile(p, re.I if not any(c.isupper() for c in p.replace("\\b", "")) else 0), why) for p, why in ERRORS]
OPENS_CONTRAST = re.compile(r"(?:Even though|Although|Though|While)\b")  # the house rule of 2026-10-15
CONTRAST = re.compile(r"\b(?:even though|although|though|but|while|whereas)\b", re.I)


def sentences(text: str) -> list[str]:
    return [s.strip() for s in SENT.split(text or "") if s.strip()]


def skeleton(s: str) -> str:
    """A sentence with names and numbers taken out, so the same template reads the same across games."""
    return NUMBER.sub("#", NAME.sub("X", s)).lower()


def story_texts(story: dict | None) -> list[str]:
    """Every piece of text a reader sees in a story block, in page order."""
    if not story:
        return []
    out = [story.get("head") or ""] + list(story.get("paras") or [])
    out += [i.get("text") or "" for i in story.get("items") or []]
    lean = story.get("lean")
    if isinstance(lean, dict):
        out += list(lean.values())
    return [t for t in out if t]


ORD = re.compile(r"\b(\d[\d,]*)(st|nd|rd|th)\b")
ART = re.compile(r"\b([Aa]n?) (\d[\d,]*\S*)")


def check_text(text: str) -> list[dict]:
    found = []
    for s in sentences(text) or [text]:
        for rx, why in ERRORS:
            m = rx.search(s)
            if m:
                found.append({"level": "error", "why": why, "text": s})
        for m in ORD.finditer(s):
            if grammar.ordinal(int(m.group(1).replace(",", ""))) != f"{int(m.group(1).replace(',', '')):,}{m.group(2)}":
                found.append({"level": "error", "why": "wrong ordinal suffix", "text": s})
        for m in ART.finditer(s):
            if grammar.article(m.group(2)).split()[0] != m.group(1).lower():
                found.append({"level": "error", "why": f"\"{m.group(1)}\" before \"{m.group(2)}\"", "text": s})
        if OPENS_CONTRAST.match(s) and re.search(r"\band\b.*, and\b", s):
            found.append({"level": "error", "why": "a contrast sentence carrying an \"and ..., and\" chain (list the reasons)",
                          "text": s})
        if len(CONTRAST.findall(s)) > 1 and not re.search(r"\bbut\b.*\bwhile\b.*\b(?:on|in) the\b", s):
            found.append({"level": "style", "why": "more than one contrast word in a sentence", "text": s})
    return found


def _phrases(s: str, n: int = 4) -> set[str]:
    words = re.sub(r"[^\w’\s-]", "", skeleton(s)).split()
    return {" ".join(words[i:i + n]) for i in range(len(words) - n + 1)} - {""}


# words that carry no wording choice: a four-word run made only of these says nothing about a template
FILLER = {"x", "#", "the", "a", "an", "on", "in", "it", "to", "for", "and", "of", "at", "with", "power", "play", "period",
          "first", "second", "third", "early", "late", "midway", "through", "later", "left", "make", "made", "up", "his"}


def _telling(ph: str) -> bool:
    return any(w not in FILLER for w in ph.split())


def check_story(story: dict | None) -> list[dict]:
    """Errors in each sentence, plus style findings across the story: a phrase twice, two openers alike in a row."""
    texts = story_texts(story)
    found = [f for t in texts for f in check_text(t)]
    prose = [s for p in (story or {}).get("paras") or [] for s in sentences(p)]
    seen: Counter = Counter()
    for s in prose:
        for ph in _phrases(s):
            if _telling(ph):
                seen[ph] += 1
    for ph, k in seen.items():
        if k > 1:
            found.append({"level": "style", "why": "same phrase twice in one story", "text": ph})
    for a, b in zip(prose, prose[1:]):
        if a.split()[:2] == b.split()[:2]:
            found.append({"level": "style", "why": "two sentences in a row open the same way", "text": f"{a} / {b}"})
    return found


def slate_repeats(stories_by_game: dict, dates: dict) -> list[dict]:
    """Stock sentences used in more than one game on the same date (a reader opening several pages sees them)."""
    by_day: dict = defaultdict(lambda: defaultdict(set))
    for gid, story in stories_by_game.items():
        for p in (story or {}).get("paras") or []:
            for s in sentences(p):
                sk = skeleton(s)
                if len(sk.split()) >= 5:
                    by_day[dates.get(gid)][sk].add(gid)
    return [{"level": "style", "why": f"same sentence in {len(g)} games on {day}", "text": sk}
            for day, sks in by_day.items() for sk, g in sks.items() if len(g) > 1]


def report(folder) -> dict:
    """Counts for every story in a recaps/ or previews/ folder of site data."""
    from pathlib import Path
    stories, dates, findings = {}, {}, []
    for p in sorted(Path(folder).glob("2*.json")):
        r = json.loads(p.read_text())
        st = r.get("story")
        if not st:
            continue
        stories[r["game_id"]], dates[r["game_id"]] = st, r.get("date") or (r.get("start") or "")[:10]
        findings += [{**f, "game": r["game_id"]} for f in check_story(st)]
    findings += slate_repeats(stories, dates)
    prose = [s for st in stories.values() for p in st.get("paras") or [] for s in sentences(p)]
    lengths = sorted(len(s.split()) for s in prose) or [0]
    openers = sum(bool(re.match(r"(?:The [A-Z]|[A-Z][\w’.\-]+ [A-Z])", s)) for s in prose)
    stock = Counter(skeleton(s) for s in prose).most_common(1)
    return {"stories": len(stories), "sentences": len(prose),
            "errors": [f for f in findings if f["level"] == "error"],
            "style": Counter(f["why"].split(" in ")[0] if f["why"].startswith("same sentence") else f["why"]
                             for f in findings if f["level"] == "style"),
            "median_words": lengths[len(lengths) // 2], "p10_words": lengths[len(lengths) // 10],
            "p90_words": lengths[9 * len(lengths) // 10],
            "name_openers": round(openers / max(len(prose), 1), 2),
            "top_stock": (stock[0][1], stock[0][0]) if stock else None}


def main(argv: list[str]) -> int:
    from pipeline.config import SITE_DATA
    bad = 0
    for kind in ("recaps", "previews"):
        r = report(SITE_DATA / kind)
        print(f"\n{kind}: {r['stories']} stories, {r['sentences']} sentences, words per sentence "
              f"{r['p10_words']}/{r['median_words']}/{r['p90_words']} (10th/median/90th), "
              f"{int(100 * r['name_openers'])}% open with a name")
        if r["top_stock"]:
            print(f"  most used sentence ({r['top_stock'][0]}x): {r['top_stock'][1]}")
        for why, k in r["style"].most_common():
            print(f"  style: {k:4d}  {why}")
        for f in r["errors"]:
            print(f"  ERROR {f.get('game')}: {f['why']}: {f['text']}")
        bad += len(r["errors"])
    return 1 if bad and "--strict" in argv else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
