"""Every sentence a reader sees on preview and post-game pages, through the house rules in export/textcheck.py.

Stories were already checked; this also reads the calls, surprises, reveals and lineup notes, by walking each page's
JSON for the fields that hold reader-facing words. Post-game pages are rewritten every full update, so all of them
count. Previews are frozen at puck drop (until a rules change rebuilds them), so slips on previews of games more than
FROZEN_DAYS old are only counted (`frozen`): no code change can fix them before the next rebuild."""
import json
from datetime import datetime, timedelta, timezone

from pipeline.config import SITE_DATA
from pipeline.export import textcheck

WORDS = {"head", "body", "text", "plain", "title", "happened", "call", "see", "gist", "kicker", "paras", "lean", "note"}
MAX_LISTED = 25
FROZEN_DAYS = 2


def texts(node, key: str | None = None):
    """Reader-facing strings in a page's JSON (a sentence or more, under one of the WORDS keys)."""
    if isinstance(node, dict):
        for k, v in node.items():
            yield from texts(v, k)
    elif isinstance(node, list):
        for v in node:
            yield from texts(v, key)
    elif isinstance(node, str) and key in WORDS and len(node.split()) >= 4:
        yield node


def run(season: int) -> dict:
    problems, style, pages, frozen = [], {}, 0, 0
    old = (datetime.now(timezone.utc) - timedelta(days=FROZEN_DAYS)).isoformat()
    for kind in ("previews", "recaps"):
        for p in sorted((SITE_DATA / kind).glob("2*.json")):
            page = json.loads(p.read_text())
            pages += 1
            seen = set()
            if kind == "previews" and (page.get("start") or "").replace("Z", "+00:00") < old:
                frozen += sum(f["level"] == "error" for t in texts(page) for f in textcheck.check_text(t))
                continue
            for t in texts(page):
                for f in textcheck.check_text(t):
                    if f["level"] == "error" and (f["why"], f["text"]) not in seen:
                        seen.add((f["why"], f["text"]))
                        problems.append({"page": f"{kind[:-1]} {page.get('game_id')}", "what": f["why"], "text": f["text"]})
        r = textcheck.report(SITE_DATA / kind)
        style[kind] = {"stories": r["stories"], "median_words": r["median_words"], "name_openers": r["name_openers"],
                       **{why: k for why, k in r["style"].most_common()}}
    return {"level": "problem" if problems else "ok", "pages": pages, "problems": problems[:MAX_LISTED],
            "count": len(problems), "frozen": frozen, "style": style}
