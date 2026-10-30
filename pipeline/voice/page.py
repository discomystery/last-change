"""Side-by-side page of a round for Sora: post-game rewrites (the site before, the rewrite, the site after the round's
changes) and fuller previews (the site today, a fuller version from the same data), with the round's pattern table.
`python -m pipeline.voice.page out.html voice/runs/{run} [voice/runs/{run} ...]`, then publish it as an Artifact."""
import html
import re
import json
import sys
from collections import Counter
from pathlib import Path

from pipeline.export import textcheck
from pipeline.voice import compare

E = html.escape
STAT = {"fixed": ("Fixed", "s-fixed"), "partly": ("Partly fixed", "s-partly"), "kept": ("Kept as is", "s-kept"),
        "proposed": ("Needs your call", "s-prop"), "open": ("Next round", "s-open")}
CAT = {"lead": "Lead", "order": "Order", "omission": "Left out", "repetition": "Repetition", "phrasing": "Phrasing",
       "structure": "Structure", "emphasis": "Emphasis", "tone": "Tone", "overstatement": "Overstated", "accuracy": "Accuracy"}


BOX = re.compile(r"\s?\((\d+ G / \d+ A / \d+ P|\d+-\d+-\d+(?: / \.\d{3} SV%)?) in (\d+ G[PS])\)")
NUM = re.compile(r"(?<![\w\-.])(?!(?:19|20)\d\d\b)\.?\d[\d.]*%?(?![\w\-])")


RANK = re.compile(r"\s?\(((?:\d+(?:st|nd|rd|th) )?lowest|\d+(?:st|nd|rd|th))\)")
TAGS = re.compile(f"{BOX.pattern}|{RANK.pattern}")


def T(text, ranks=None):  # escaped text with stats in the sans, as the site sets them (Base.astro sansNumbers)
    out, i = [], 0
    sn = lambda s: NUM.sub(lambda n: f"<span class=sn>{n.group(0)}</span>", E(s))
    for m in TAGS.finditer(text):
        if m.group(1):
            pill = f" <span class=pill>{E(m.group(1))} <i>in {E(m.group(2))}</i></span>"
        elif ranks:  # a habit's league rank, its tooltip in reading order (story["ranks"])
            pill = f" <span class='pill rk' title='{E(ranks.pop(0))}'>{E(m.group(3))}</span>"
        else:
            continue
        out += [sn(text[i:m.start()]), pill]
        i = m.end()
    return "".join(out) + sn(text[i:])


def sents(paras):
    return [s.strip() for p in paras for s in textcheck.sentences(p)]


def story(st, before=None, calls=None):
    old = set(sents(before["paras"])) if before else None
    h = f"<h4>{E(st.get('head') or '')}</h4>"
    ranks = list(st.get("ranks") or [])
    for p in st.get("paras") or []:
        h += "<p>" + (T(p, ranks) if old is None else " ".join(T(s, ranks) if s in old else f"<mark>{T(s, ranks)}</mark>" for s in textcheck.sentences(p))) + "</p>"
    its = st.get("items") or []
    if its:
        h += "<ul>" + "".join(f"<li><b>{E(i.get('tag') or '')}</b> {E(i.get('text') or '')}</li>" for i in its) + "</ul>"
    if calls:
        h += "<div class=calls><div class=lab>Call cards further down the page</div>" + "".join(
            f"<div><b>{E(c.get('head') or '')}</b> {E(c.get('body') or '')}</div>" for c in calls) + "</div>"
    return h


def words(paras):
    return len(" ".join(paras).split())


def build(out: Path, runs: list[Path]) -> None:
    root = runs[0].parent.parent
    finds = compare.load_findings(root)
    pats = compare.patterns(root)
    games = []
    for run in runs:
        for pk in sorted(run.glob("*.packet.json")):
            gid = pk.name.split(".")[0]
            P = json.loads(pk.read_text())
            a, hm = P["away"]["abbr"], P["home"]["abbr"]
            if P["kind"] == "recap":
                if not (run / f"{gid}.rewrite.md").exists():
                    continue
                f = P["final"]
                end = {"shootout": " (shootout)", "OT": " (overtime)"}.get(f.get("ended"), "")
                base = json.loads((run / f"{gid}.base.json").read_text())
                mine = compare.parse_draft((run / f"{gid}.rewrite.md").read_text())
                aft = json.loads((run / f"{gid}.after.json").read_text())
                cols = [("The site before this round", story(base)), ("My rewrite: same facts, same order", story(mine, base)),
                        ("The site after this round's changes", story(aft, base))]
                fl = [x for x in finds if str(x["game_id"]) == gid and x["run"].endswith("rewrite")]
                games.append(("recap", P["date"], gid, f"{a} {f[a]} at {hm} {f[hm]}{end}", "Post-game", cols, fl, None))
            elif "teams" in P:  # a fuller preview packet (round two on); round one's previews were written from less
                gen = json.loads((run / f"{gid}.generator.json").read_text())
                mine = compare.parse_draft((run / f"{gid}.claude.md").read_text())
                af = run / f"{gid}.after.json"
                aft = json.loads(af.read_text()) if af.exists() else None
                cols = [("The site before", story(gen, calls=gen.get("calls"))), ("My fuller preview", story(mine))]
                if aft:
                    cols.append(("The site's generator now", story(aft, gen)))
                games.append(("preview", P["start_utc"][:10], gid, f"{a} at {hm}", "Preview", cols, [],
                              (words(gen["paras"]), words(mine["paras"]), words(aft["paras"]) if aft else 0)))
    games.sort(key=lambda g: (g[0] != "recap", g[1]))
    recs = [g for g in games if g[0] == "recap"]
    prevs = [g for g in games if g[0] == "preview"]
    pairs = [json.loads(l) for l in (root / "pairs.jsonl").read_text().splitlines() if l.strip()] if (root / "pairs.jsonl").exists() else []
    ops = Counter(p["op"] for p in pairs)
    first = {}
    for x in finds:
        first.setdefault(x["key"], x["note"])
    rows = ""
    for p in [p for p in pats if p["key"].startswith("rw-") or p["key"] in ("two-winners", "assist-milestone-wording")]:
        lab, cls = STAT.get(p["status"], (p["status"], "s-open"))
        rows += (f"<tr class='{'pat' if p['pattern'] else 'one'}'><td><span class='st {cls}'>{lab}</span></td><td class=n>{p['games']}</td>"
                 f"<td>{E(first.get(p['key'], ''))}</td><td class=did>{E(p['note'])}</td></tr>")
    cards = ""
    for kind_, when, gid, title, kind, cols, fl, _ in games:
        diffs = "".join(f"<li><span class=cat>{E(CAT.get(x['category'], x['category']))}</span> {E(x['note'])}</li>" for x in fl)
        body = "".join(f"<div class='col'><div class=lab>{E(l)}</div>{h}</div>" for l, h in cols)
        cards += (f"<section class=game id=g{gid}><header><span class=kind>{kind}</span><h3>{E(title)}</h3><span class=when>{E(when)}</span></header>"
                  f"<div class='cols n{len(cols)}'>{body}</div>"
                  + (f"<div class=diffs><div class=lab>What the rewrite changed</div><ul>{diffs}</ul></div>" if diffs else "") + "</section>")
    nav = "".join(f"<a href='#g{g[2]}'>{E(g[3])}</a>" for g in games)
    pw = [g[7] for g in prevs]
    changed = sum(v for k, v in ops.items() if k != "kept")
    pw_site = round(sum(x[0] for x in pw) / max(len(pw), 1))
    pw_mine = round(sum(x[1] for x in pw) / max(len(pw), 1))
    pw_now = round(sum(x[2] for x in pw) / max(len(pw), 1))
    page = f"""<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Voice Loop Pilot</title>
<link rel=preconnect href=https://fonts.googleapis.com><link rel=preconnect href=https://fonts.gstatic.com crossorigin>
<style>
:root{{--bg:#eef3f7;--card:#fff;--ink:#0f1c2b;--ink2:#3a4a5c;--ink3:#6f7e8e;--line:#d6e0e8;--soft:#f5f8fb;--mark:#fff1b8;--fix:#2f7d5b;--prop:#b25d12;--part:#6a6fb8}}
@media (prefers-color-scheme:dark){{:root:not([data-theme=light]){{--bg:#0e1823;--card:#152231;--ink:#e6edf3;--ink2:#b8c4cf;--ink3:#7c8a98;--line:#26384a;--soft:#1a2a3b;--mark:#5a4a12;--fix:#6cc49b;--prop:#f0a35e;--part:#a4a8ee}}}}
:root[data-theme=dark]{{--bg:#0e1823;--card:#152231;--ink:#e6edf3;--ink2:#b8c4cf;--ink3:#7c8a98;--line:#26384a;--soft:#1a2a3b;--mark:#5a4a12;--fix:#6cc49b;--prop:#f0a35e;--part:#a4a8ee}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--ink);font:17px/1.55 Literata,Georgia,serif}}
.wrap{{max-width:1360px;margin:0 auto;padding:32px 16px 80px}}
h1,h2,h3,h4{{font-family:Fraunces,Georgia,serif;font-variation-settings:"SOFT" 100;font-weight:600;margin:0}}
h1{{font-size:36px;line-height:1.15}} h2{{font-size:26px;margin:48px 0 8px}}
.sn{{font:600 .94em 'Hanken Grotesk',system-ui,sans-serif;color:var(--ink)}}
.pill{{display:inline-block;font:600 12px/1 'Hanken Grotesk',system-ui,sans-serif;padding:4px 8px;border-radius:999px;background:var(--line);color:var(--ink2);white-space:nowrap;vertical-align:1px}} .pill i{{font-style:normal;font-weight:500;color:var(--ink3)}} .pill.rk{{cursor:help;padding:3px 7px;font-size:11.5px}}
.lede{{color:var(--ink2);max-width:860px;margin:12px 0 0}}
.lab,.kind,.cat,.st,th,.when{{font:600 11px/1.3 'Hanken Grotesk',system-ui,sans-serif;letter-spacing:.07em;text-transform:uppercase;color:var(--ink3)}}
.tiles{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin:24px 0}}
.tile{{background:var(--card);border-radius:12px;padding:14px 16px}}
.tile b{{display:block;font:600 28px/1.1 Fraunces,serif;margin:4px 0}}
.tile span{{font:500 14px/1.3 'Hanken Grotesk',sans-serif;color:var(--ink2)}}
.steps{{counter-reset:s;list-style:none;padding:0;display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:12px}}
.steps li{{background:var(--card);border-radius:12px;padding:14px 16px;font-size:15px;color:var(--ink2)}}
.steps li::before{{counter-increment:s;content:counter(s);display:block;font:600 22px Fraunces,serif;color:var(--ink)}}
table{{width:100%;border-collapse:collapse;background:var(--card);border-radius:12px;overflow:hidden;font-size:15px}}
td,th{{padding:10px 12px;border-top:1px solid var(--line);vertical-align:top;text-align:left}}
td.n{{font:600 15px 'Hanken Grotesk',sans-serif;text-align:right;white-space:nowrap}}
tr.one td{{color:var(--ink3)}} td.did{{color:var(--ink2);font-size:14px}}
@media (max-width:760px){{thead{{display:none}} tr{{display:block;border-top:1px solid var(--line)}} td{{display:block;border:0;padding:6px 12px}} td.n{{display:none}} tr td:first-child{{padding-top:12px}} tr td:last-child{{padding-bottom:12px}}}}
.st{{display:inline-block;padding:3px 8px;border-radius:99px;white-space:nowrap;background:var(--soft)}}
.s-fixed{{color:var(--fix)}} .s-prop{{color:var(--prop)}} .s-partly{{color:var(--part)}}
.nav{{display:flex;flex-wrap:wrap;gap:6px;margin:12px 0 0}}
.nav a{{font:500 13px 'Hanken Grotesk',sans-serif;color:var(--ink2);background:var(--card);padding:5px 10px;border-radius:99px;text-decoration:none}}
.game{{background:var(--card);border-radius:16px;padding:20px;margin:20px 0}}
.game header{{display:flex;flex-wrap:wrap;align-items:baseline;gap:4px 12px;margin-bottom:14px}}
.game h3{{font-size:22px}}
.cols{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}} .cols.n2{{grid-template-columns:repeat(2,minmax(0,1fr))}}
main,.wrap,.game,.col{{min-width:0}} .col{{overflow-wrap:anywhere;background:var(--soft);border-radius:12px;padding:14px 16px;font-size:15.5px}}
.col h4{{font-size:18px;margin:6px 0 8px}} .col p{{margin:0 0 10px}}
.col ul{{padding-left:18px;margin:6px 0;font-size:14.5px}} .col li b{{font:600 12px 'Hanken Grotesk',sans-serif;text-transform:uppercase;letter-spacing:.05em}}
.calls{{border-top:1px solid var(--line);margin-top:10px;padding-top:8px;font-size:14px;color:var(--ink2)}} .calls div{{margin:4px 0}}
mark{{background:var(--mark);color:inherit;border-radius:3px;padding:0 2px}}
.same{{color:var(--ink3);font-style:italic}}
.diffs{{margin-top:14px}} .diffs ul{{padding-left:0;list-style:none;margin:6px 0 0}} .diffs li{{margin:6px 0;font-size:15px;color:var(--ink2)}}
.cat{{display:inline-block;min-width:92px;color:var(--ink)}}
@media (max-width:1000px){{.cols,.cols.n2{{grid-template-columns:1fr}}}}
@media (max-width:600px){{h1{{font-size:28px}}.game{{padding:14px}}td.n{{display:none}}}}
</style></head><body><div class=wrap>
<div class=lab>Spare Line Change · writing check, rounds two and three</div>
<h1>Closer rewrites, and fuller previews</h1>
<p class=lede>Round one's stories from scratch were too far from the site's to learn from. This time, for {len(recs)} post-game pages, I started from the site's own story and kept every fact and the order of the game, changing only how the sentences are built and joined. A check flags anything I dropped, added or moved. Each site sentence is paired with what it became, and the changes that kept coming up are now in the site's writer. For {len(prevs)} upcoming games, I wrote fuller previews from a much bigger set of facts (standings, team stats and ranks, style, top scorers and recent form, goalies, lines and power-play units), to show what the site's preview could hold. You picked that shape, so the site's own generator now writes it too: where they stand, how they play, who carries the scoring, who's in net, then the matchup to watch. Its new sentences are highlighted in the third column, built only from games before each preview. Latest round: the second paragraph now ends with what the two styles mean against each other, the last games are told by day (and move with the day you read the preview), the closing ideas each get their own lead-in, and the wording varies more from team to team.</p>
<div class=tiles>
<div class=tile><div class=lab>Post-game sentences reworked</div><b>{changed}</b><span>{ops.get('merged', 0)} joined, {ops.get('reworded', 0)} reworded, {ops.get('split', 0)} split, {ops.get('cut', 0)} cut</span></div>
<div class=tile><div class=lab>Changes now in the site</div><b>{sum(1 for p in pats if p['key'].startswith('rw-') and p['status'] == 'fixed')}</b><span>of {sum(1 for p in pats if p['key'].startswith('rw-'))} patterns from the rewrites</span></div>
<div class=tile><div class=lab>Preview words</div><b>{pw_site} → {pw_now}</b><span>story paragraphs, the site before vs. its generator now (my drafts: {pw_mine}; call cards come on top)</span></div>
</div>
<h2>What the rewrites kept changing</h2>
<table><thead><tr><th>Status</th><th class=n>Games</th><th>What was different</th><th>What I did</th></tr></thead><tbody>{rows}</tbody></table>
<h2>Game by game</h2>
<div class=nav>{nav}</div>
{cards}
</div></body></html>"""
    out.write_text(page)
    print(out, len(page))


if __name__ == "__main__":
    build(Path(sys.argv[1]), [Path(a) for a in sys.argv[2:]])
