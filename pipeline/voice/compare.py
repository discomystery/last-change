"""Fact guard, side-by-side report and pattern counts for the write-and-compare loop (see pipeline/voice/__init__.py)."""
import html
import json
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median

from pipeline.export import textcheck

NUM_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
             "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
             "eighteen": 18, "nineteen": 19, "twenty": 20, "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
             "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10}
# capitalized words any story may use without the packet naming them
COMMON = {"The", "A", "An", "And", "But", "It", "He", "His", "They", "Their", "That", "This", "Then", "With", "After", "Before",
          "In", "On", "At", "By", "For", "Of", "From", "To", "Both", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight",
          "Nine", "Ten", "First", "Second", "Third", "Overtime", "OT", "NHL", "Not", "No", "Only", "So", "Still", "When", "Who",
          "What", "Why", "Out", "Back", "Up", "Down", "Every", "Each", "Just", "Even", "All", "Neither", "Nobody", "None",
          "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun",
          "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec", "October", "November",
          "Replay", "Run", "Watch", "Head", "Goalie", "Game", "Tonight", "Tomorrow", "Instead", "Meanwhile", "Since", "Until",
          "Last", "Next", "Twice", "Once", "Over", "Under", "Through", "Down", "Yes", "If", "Is", "Are", "Was", "Were", "Had",
          "Has", "Have", "Did", "Do", "Don’t", "Can", "Could", "Would", "Should", "Will", "We", "Our", "You", "Your", "There",
          "Here", "Late", "Early", "Midway", "Another", "Neither", "Either", "Most", "More", "Less", "Few", "Many", "Some",
          "Also", "Now", "Almost", "Nearly", "About", "Around", "Like", "As", "Than", "Where", "How", "Which", "While", "Though",
          "Although", "Because", "Ice", "Power", "Penalty", "Empty", "Shootout", "Final", "Western", "Eastern", "Conference",
          "Expect", "Otherwise", "Look", "Keep", "Make", "Watch", "Better", "Worse", "Plenty", "Nothing", "Everything"}


def fold(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)).replace("’", "'")


def _corpus(packet) -> str:
    out = []

    def walk(x):
        if isinstance(x, dict):
            for k, v in x.items():
                out.append(str(k))
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
        elif x is not None:
            out.append(str(x))
    walk(packet)
    return fold(" ".join(out))


def _numbers(text: str) -> set[str]:
    nums = set(re.findall(r"\d+(?:\.\d+)?", text))
    for n in list(nums):  # 0.714 -> 71, "8.6" stays; percentages may be written either way
        if n.startswith("0.") and len(n) > 3:
            nums.add(str(round(float(n) * 100)))
            nums.add(str(round(float(n) * 100, 1)))
    return nums


def guard(draft: str, packet: dict) -> list[dict]:
    """Numbers and names in a draft that the packet does not contain. Each is either fixed in the draft or, when the
    packet backs it in a form the guard can't see (a count it adds up, a difference of two scores), marked with an
    `<!-- ok: ... -->` comment saying how."""
    corpus = _corpus(packet)
    nums = _numbers(corpus) | {str(i) for i in range(0, 4)}  # zero to three are counts any writer may make
    ok = set(re.findall(r"<!--\s*ok:\s*([^>]*?)\s*-->", draft))
    body = re.sub(r"<!--.*?-->", "", draft, flags=re.S)
    out = []
    # one line at a time (a headline is its own sentence), item tags ("- Slump:") left out
    lines = [re.sub(r"^#+\s*|^-\s*(?:[A-Z][\w ]{0,20}:\s)?", "", l) for l in body.splitlines()]
    for s in (x for l in lines for x in textcheck.sentences(l)):
        for n in re.findall(r"(?<![\w.])\d+(?:\.\d+)?", s):
            if n not in nums and not any(n in o for o in ok):
                out.append({"what": "number", "value": n, "sentence": s})
        for w in re.findall(r"\b[a-z]+\b", s.lower()):
            v = NUM_WORDS.get(w)
            if v and v > 3 and str(v) not in nums and not any(w in o for o in ok):
                out.append({"what": "number word", "value": w, "sentence": s})
        words = re.findall(r"[A-Z][\w’'\-.]*[\w]", s)
        for i, w in enumerate(words):
            base = re.sub(r"’s$|'s$|’$", "", w)
            if base in COMMON or fold(base) in corpus or any(base in o for o in ok):
                continue
            out.append({"what": "name", "value": base, "sentence": s})
    return out


def parse_draft(text: str) -> dict:
    """`# Headline`, paragraphs, then `- Tag: text` items. Comments (`<!-- -->`) are dropped."""
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    head, paras, items = "", [], []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = [l.strip() for l in block.splitlines() if l.strip()]
        if not lines:
            continue
        if lines[0].startswith("# "):
            head = lines[0][2:].strip()
            lines = lines[1:]
        if lines and all(l.startswith("- ") for l in lines):
            for l in lines:
                tag, _, t = l[2:].partition(": ")
                items.append({"tag": tag, "text": t} if t else {"tag": "", "text": tag})
        elif lines:
            paras.append(" ".join(lines))
    return {"head": head, "paras": paras, "items": items}


def measures(story: dict) -> dict:
    """Automatic measures of one story, the same for both writers: length, sentence shape, repeats."""
    prose = [s for p in story.get("paras") or [] for s in textcheck.sentences(p)]
    lens = [len(s.split()) for s in prose] or [0]
    rep = Counter(ph for s in prose for ph in textcheck._phrases(s, 3) if textcheck._telling(ph))
    bigram = Counter()
    for s in prose:  # verb phrases a template leans on: "struck again", "put the X up"
        ws = re.sub(r"[^\w’\s]", "", textcheck.skeleton(s)).split()
        bigram.update({" ".join(ws[i:i + 2]) for i in range(len(ws) - 1)})
    stock = [p for p, k in bigram.items() if k > 2 and all(w not in textcheck.FILLER | {"x", "#", "the", "a"} for w in p.split())]
    same_open = sum(a.split()[:2] == b.split()[:2] for a, b in zip(prose, prose[1:]))
    nums = sum(len(textcheck.NUMBER.findall(s)) for s in prose)
    return {"words": sum(lens), "sentences": len(prose), "median_len": median(lens), "longest": max(lens),
            "numbers_per_sentence": round(nums / max(len(prose), 1), 2),
            "repeated_phrases": sorted(p for p, k in rep.items() if k > 1)[:8], "stock_bigrams": sorted(stock)[:6],
            "same_openers_in_a_row": same_open, "items": len(story.get("items") or []),
            "house_rule_slips": [f["why"] for f in textcheck.check_story(story) if f["level"] == "error"]}


# ---------------------------------------------------------------- findings and patterns

CATEGORIES = ("lead", "order", "emphasis", "omission", "overstatement", "repetition", "phrasing", "tone", "structure", "accuracy")
PATTERN_GAMES = 3  # a difference seen in this many games (or a quarter of a run's games) is a pattern


def load_findings(root: Path) -> list[dict]:
    f = root / "findings.jsonl"
    return [json.loads(l) for l in f.read_text().splitlines() if l.strip()] if f.exists() else []


def patterns(root: Path) -> list[dict]:
    """Findings grouped by key across every run: how many games, which runs, the status in patterns.json."""
    status = json.loads((root / "patterns.json").read_text()) if (root / "patterns.json").exists() else {}
    by = defaultdict(list)
    for f in load_findings(root):
        by[f["key"]].append(f)
    runs = Counter(f["run"] for f in load_findings(root))
    run_games = defaultdict(set)
    for f in load_findings(root):
        run_games[f["run"]].add(f["game_id"])
    out = []
    for key, fs in by.items():
        games = {f["game_id"] for f in fs}
        per_run = Counter(f["run"] for f in fs)
        share = max(len({f["game_id"] for f in fs if f["run"] == r}) / max(len(run_games[r]), 1) for r in per_run)
        st = status.get(key, {})
        out.append({"key": key, "category": fs[0]["category"], "page": sorted({f["page"] for f in fs}), "games": len(games),
                    "runs": sorted(per_run), "pattern": len(games) >= PATTERN_GAMES or share >= 0.25,
                    "status": st.get("status", "open"), "fix": st.get("fix"), "note": st.get("note") or fs[0]["note"],
                    # a fixed pattern seen again in a run after the fix is a regression
                    "regressed": st.get("status") == "fixed" and any(r > (st.get("since") or "") for r in per_run)})
    return sorted(out, key=lambda x: (not x["pattern"], -x["games"], x["key"]))


# ---------------------------------------------------------------- report

def _story_html(st: dict) -> str:
    h = f"<h3>{html.escape(st.get('head') or '')}</h3>" + "".join(f"<p>{html.escape(p)}</p>" for p in st.get("paras") or [])
    if st.get("items"):
        h += "<ul>" + "".join(f"<li><b>{html.escape(i.get('tag') or '')}</b> {html.escape(i.get('text') or '')}</li>" for i in st["items"]) + "</ul>"
    for t, o in (st.get("outlook") or {}).items():
        h += f"<p class=o><b>Outlook ({html.escape(t)}): {html.escape(o.get('head') or '')}.</b> {html.escape(o.get('body') or '')}</p>"
    for c in st.get("calls") or []:
        h += f"<p class=o><b>Call: {html.escape(c.get('head') or '')}.</b> {html.escape(c.get('body') or '')}</p>"
    return h


def report(run_dir: Path, root: Path) -> dict:
    """report.md and report.html for one run: per game, the two versions side by side, the automatic measures and the
    logged differences; then the run's totals."""
    run = run_dir.name
    finds = [f for f in load_findings(root) if f["run"] == run]
    games, md, rows = [], [f"# Voice comparison, {run}\n"], []
    tot = defaultdict(list)
    for pk_f in sorted(run_dir.glob("*.packet.json")):
        gid = int(pk_f.name.split(".")[0])
        pk = json.loads(pk_f.read_text())
        gen = json.loads((run_dir / f"{gid}.generator.json").read_text())
        d_f = run_dir / f"{gid}.claude.md"
        if not d_f.exists():
            continue
        mine = parse_draft(d_f.read_text())
        aft_f = run_dir / f"{gid}.after.json"
        aft = json.loads(aft_f.read_text()) if aft_f.exists() else None
        flags = guard(d_f.read_text(), pk)
        mg, mc = measures(gen), measures(mine)
        for k in ("words", "sentences", "median_len", "numbers_per_sentence", "same_openers_in_a_row"):
            tot["gen_" + k].append(mg[k])
            tot["claude_" + k].append(mc[k])
        tot["gen_repeats"].append(len(mg["repeated_phrases"]) + len(mg["stock_bigrams"]))
        tot["claude_repeats"].append(len(mc["repeated_phrases"]) + len(mc["stock_bigrams"]))
        title = f"{pk['away']['abbr']} at {pk['home']['abbr']}, {pk.get('date') or (pk.get('start_utc') or '')[:10]} ({pk['kind']})"
        if pk["kind"] == "recap":
            f_ = pk["final"]
            title = f"{pk['away']['abbr']} {f_[pk['away']['abbr']]} at {pk['home']['abbr']} {f_[pk['home']['abbr']]}" + \
                    ("" if f_["ended"] == "regulation" else f" ({f_['ended']})") + f", {pk['date']}"
        mine_f = [f for f in finds if f["game_id"] == gid]
        games.append({"gid": gid, "title": title, "gen": gen, "mine": mine, "after": aft, "mg": mg, "mc": mc, "flags": flags, "finds": mine_f})
        md += [f"\n## {title} ({gid})\n", "### Generator\n", f"**{gen.get('head')}**\n", *[p + "\n" for p in gen.get("paras") or []],
               *[f"- {i.get('tag')}: {i.get('text')}" for i in gen.get("items") or []],
               "\n### Claude, from the facts only\n", f"**{mine['head']}**\n", *[p + "\n" for p in mine["paras"]],
               *[f"- {i['tag']}: {i['text']}" for i in mine["items"]],
               *(["\n### Generator after the changes\n", f"**{aft.get('head')}**\n", *[p + "\n" for p in aft.get("paras") or []]] if aft else []),
               "\n### Measures (generator / Claude)\n",
               *[f"- {k}: {mg[k]} / {mc[k]}" for k in mg],
               f"\n### Fact guard: {'clean' if not flags else str(len(flags)) + ' unbacked'}\n",
               *[f"- {x['what']} `{x['value']}`: {x['sentence']}" for x in flags],
               "\n### Differences\n", *[f"- [{f['category']}] `{f['key']}`: {f['note']}" for f in mine_f]]
    pats = [p for p in patterns(root) if run in p["runs"]]
    md += ["\n## Patterns in this run\n", *[f"- {'PATTERN' if p['pattern'] else 'one-off'} `{p['key']}` ({p['games']} games, "
                                          f"{p['status']}{', fix ' + p['fix'] if p['fix'] else ''}): {p['note']}" for p in pats]]
    avg = {k: round(sum(v) / len(v), 2) for k, v in tot.items() if v}
    md += ["\n## Totals (averages per game)\n", *[f"- {k}: {v}" for k, v in sorted(avg.items())]]
    (run_dir / "report.md").write_text("\n".join(md) + "\n")
    (run_dir / "report.html").write_text(_html(run, games, pats, avg))
    return {"games": len(games), "unbacked": sum(len(g["flags"]) for g in games), "findings": len(finds), "patterns": len(pats)}


def _html(run: str, games: list[dict], pats: list[dict], avg: dict) -> str:
    css = ("body{font:16px/1.5 Georgia,serif;max-width:1200px;margin:2em auto;padding:0 16px;color:#1b2a3a;background:#fff}"
           ".g{border-top:1px solid #ccd;margin-top:2em}.cols{display:grid;grid-template-columns:1fr 1fr;gap:24px}"
           "@media(max-width:760px){.cols{grid-template-columns:1fr}}.cols>div{background:#f4f6f9;padding:4px 16px;border-radius:10px}"
           "h3{font-size:18px}.o{font-size:14px;color:#445}small,li.f{font:14px/1.4 system-ui,sans-serif}.k{color:#678}")
    out = [f"<!doctype html><meta charset=utf-8><title>Voice comparison {run}</title><style>{css}</style><h1>Voice comparison, {run}</h1>"]
    for g in games:
        out.append(f"<div class=g><h2>{html.escape(g['title'])}</h2><div class=cols><div><small class=k>GENERATOR</small>"
                   f"{_story_html(g['gen'])}</div><div><small class=k>CLAUDE, FROM THE FACTS ONLY</small>{_story_html(g['mine'])}</div></div>")
        if g.get("after"):
            out.append(f"<details><summary><small>GENERATOR AFTER THE CHANGES</small></summary><div class=cols><div>{_story_html(g['after'])}</div></div></details>")
        out.append("<ul>" + "".join(f"<li class=f><b>{html.escape(f['category'])}</b> {html.escape(f['note'])}</li>" for f in g["finds"]) + "</ul>")
        if g["flags"]:
            out.append("<p><small>Fact guard: " + "; ".join(html.escape(f"{x['what']} {x['value']}") for x in g["flags"]) + "</small></p>")
        out.append("</div>")
    out.append("<h2>Patterns</h2><ul>" + "".join(f"<li class=f><b>{'Pattern' if p['pattern'] else 'One-off'}</b> ({p['games']} games) "
                                                  f"{html.escape(p['note'])}</li>" for p in pats) + "</ul>")
    return "".join(out)


# ---------------------------------------------------------------- rewrite mode (Sora, 2026-10-30, round two)
# A draft written from the facts alone came out too far from the site's story to learn sentence-building from (Sora:
# shorter, reordered, other facts). In rewrite mode the draft starts from the site's own story and keeps every fact and
# the game order; only how sentences are built and joined changes. Each site sentence is then paired with what it became,
# and those pairs are the material for generator changes.

def to_markdown(story: dict) -> str:
    """The site's story in the draft format, the starting point of a rewrite."""
    out = [f"# {story.get('head') or ''}", ""]
    for p in story.get("paras") or []:
        out += [p, ""]
    out += [f"- {i['tag']}: {i['text']}" if i.get("tag") else f"- {i['text']}" for i in story.get("items") or []]
    return "\n".join(out).strip() + "\n"


def facts(text: str) -> set[str]:
    """Numbers (number words as digits, ordinals included) and capitalized names, folded: what a rewrite must keep."""
    out = set(re.findall(r"(?<![\w.])\d+(?:[.:]\d+)?(?:-\d+){0,2}", text))
    out |= {str(v) for w, v in NUM_WORDS.items() if v > 1 and re.search(rf"\b{w}\b", text.lower())}
    for w in re.findall(r"[A-Z][\w’'\-.]*[\w]", text):
        base = re.sub(r"’s$|'s$|’$", "", w)
        if base not in COMMON:
            out.add(fold(base))
    return out


def _scores(text: str) -> list[str]:
    return re.findall(r"(?<![\d-])\d{1,2}-\d{1,2}(?![\d-])", text)


def fidelity(rewrite: str, base: dict, packet: dict) -> dict:
    """What a rewrite dropped from the site's story, what it added that the packet doesn't back, and whether the
    running scores still come in the same order. `<!-- drop: X -->` records a deliberate cut (a fact said twice)."""
    r = parse_draft(rewrite)
    dropped_ok = set(re.findall(r"<!--\s*drop:\s*([^>]*?)\s*-->", rewrite))
    whole = lambda s: " ".join([s.get("head") or ""] + (s.get("paras") or []) + [i.get("text", "") for i in s.get("items") or []])
    have, had = facts(whole(r)), facts(whole(base))
    # digits and number words count as the same fact
    def present(f):
        if f in have:
            return True
        word = next((w for w, v in NUM_WORDS.items() if str(v) == f), None)
        return bool(word and word in whole(r).lower())
    dropped = sorted(f for f in had if not present(f) and not any(f in d for d in dropped_ok))
    a, b = _scores(" ".join(base.get("paras") or [])), _scores(" ".join(r["paras"]))
    seq = lambda xs, other: list(dict.fromkeys(x for x in xs if x in other))
    order_ok = seq(a, b) == seq(b, a)
    # anything the site's own story says is backed too (the pilot's packets predate some fields, e.g. fights)
    return {"dropped": dropped, "added": guard(rewrite, {"packet": packet, "site": base}), "order_kept": order_ok}


def _words(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z’']+", fold(s).lower()) if len(w) > 2}


def align(base: dict, rewrite: dict) -> list[dict]:
    """Pair each sentence of the site's story with the rewrite sentence that took over its facts (most shared facts,
    then words). Groups come out in the rewrite's order: `kept` (same sentence), `reworded`, `merged` (several site
    sentences into one), `split` (one site sentence told across several), `cut` (site sentence with nothing carried
    over) and `new` (a rewrite sentence that carries no site sentence, e.g. a joining clause)."""
    def sents(story):
        out = []
        for pi, p in enumerate(story.get("paras") or []):
            out += [(pi, s) for s in textcheck.sentences(p)]
        out += [(-1, f"{i.get('tag')}: {i.get('text')}") for i in story.get("items") or []]
        return out
    B, R = sents(base), sents(rewrite)
    if base.get("head") or rewrite.get("head"):
        B.insert(0, (-2, base.get("head") or ""))
        R.insert(0, (-2, rewrite.get("head") or ""))

    def score(b, r):
        fb, fr = facts(b), facts(r)
        wb, wr = _words(b), _words(r)
        return 3 * len(fb & fr) / max(len(fb), 1) + len(wb & wr) / max(len(wb | wr), 1)
    to = {}
    for bi, (bp, b) in enumerate(B):
        cands = [(score(b, r), -abs(ri - bi), ri) for ri, (rp, r) in enumerate(R) if (rp == -2) == (bp == -2)]
        best = max(cands, default=(0, 0, None))
        to[bi] = best[2] if best[0] >= 0.35 else None
    # a site sentence whose facts spread over two rewrite sentences: count it once, at its best match, and mark the split
    spread = defaultdict(set)
    for bi, (bp, b) in enumerate(B):
        for ri, (rp, r) in enumerate(R):
            if (rp == -2) == (bp == -2) and len(facts(b) & facts(r)) >= 2 and ri != to[bi]:
                spread[bi].add(ri)
    groups, seen_b = [], set()
    for ri, (rp, r) in enumerate(R):
        bs = [bi for bi in sorted(to) if to[bi] == ri]
        if not bs:
            groups.append({"op": "new", "site": [], "rewrite": [r], "part": rp})
            continue
        seen_b |= set(bs)
        site = [B[bi][1] for bi in bs]
        op = "kept" if site == [r] else "merged" if len(bs) > 1 else "split" if spread.get(bs[0]) else "reworded"
        groups.append({"op": op, "site": site, "rewrite": [r], "part": rp})
    for bi, (bp, b) in enumerate(B):
        if to[bi] is None:
            groups.append({"op": "cut", "site": [b], "rewrite": [], "part": bp})
    return groups
