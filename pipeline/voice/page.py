"""Side-by-side page of a run for Sora: the site's text, the draft, and the site after the run's fixes, with the
pattern table. `python -m pipeline.voice.page voice/runs/{run} out.html`, then publish it as an Artifact."""
import html, json, re, sys
from pathlib import Path
from statistics import mean
from pipeline.voice import compare
from pipeline.export import textcheck

run = Path(sys.argv[1]); out = Path(sys.argv[2]); root = run.parent.parent
E = html.escape
finds = [json.loads(l) for l in (root / "findings.jsonl").read_text().splitlines() if l.strip()]
pats = compare.patterns(root)
STAT = {"fixed": ("Fixed", "s-fixed"), "partly": ("Partly fixed", "s-partly"), "kept": ("Kept as is", "s-kept"),
        "proposed": ("Needs your call", "s-prop"), "open": ("Noted, one-off", "s-open")}
CAT = {"lead": "Lead", "order": "Order", "omission": "Left out", "repetition": "Repetition", "phrasing": "Phrasing",
       "structure": "Structure", "emphasis": "Emphasis", "tone": "Tone", "overstatement": "Overstated", "accuracy": "Accuracy"}

def sents(paras):
    return [s.strip() for p in paras for s in textcheck.sentences(p)]

def story(st, before=None, calls=None):
    old = set(sents(before["paras"])) if before else None
    h = f"<h4>{E(st.get('head') or '')}</h4>"
    for p in st.get("paras") or []:
        if old is None:
            h += f"<p>{E(p)}</p>"
        else:
            h += "<p>" + " ".join(f"<mark>{E(s)}</mark>" if s not in old else E(s) for s in textcheck.sentences(p)) + "</p>"
    its = st.get("items") or []
    if its:
        oldit = {(i.get('tag'), i.get('text')) for i in (before or {}).get("items") or []} if before else None
        h += "<ul>" + "".join(
            f"<li><b>{E(i.get('tag') or '')}</b> " + (f"<mark>{E(i.get('text') or '')}</mark>" if oldit is not None and (i.get('tag'), i.get('text')) not in oldit else E(i.get('text') or '')) + "</li>"
            for i in its) + "</ul>"
    if calls:
        h += "<div class=calls>" + "".join(f"<div><b>{E(c.get('head') or '')}</b> {E(c.get('body') or '')}</div>" for c in calls) + "</div>"
    return h

games = []
for pk in sorted(run.glob("*.packet.json")):
    gid = pk.name.split(".")[0]
    P = json.loads(pk.read_text())
    a, hm = P["away"]["abbr"], P["home"]["abbr"]
    if P["kind"] == "recap":
        f = P["final"]; end = {"shootout": " (shootout)", "OT": " (overtime)"}.get(f.get("ended"), "")
        title = f"{a} {f[a]} at {hm} {f[hm]}{end}"; when = P["date"]; kind = "Post-game"
    else:
        title = f"{a} at {hm}"; when = P["start_utc"][:10]; kind = "Preview"
    gen = json.loads((run / f"{gid}.generator.json").read_text())
    aft = json.loads((run / f"{gid}.after.json").read_text()) if (run / f"{gid}.after.json").exists() else None
    me = compare.parse_draft((run / f"{gid}.claude.md").read_text())
    changed = aft and (sents(aft["paras"]) != sents(gen["paras"]) or aft.get("head") != gen.get("head") or aft.get("items") != gen.get("items"))
    fl = [x for x in finds if str(x["game_id"]) == gid]
    games.append((P["kind"], gid, title, when, kind, gen, me, aft, changed, fl))
games.sort(key=lambda g: (g[0] != "recap", g[3]))

pat_rows = ""
first = {}
for x in finds:
    first.setdefault(x['key'], x['note'])
for p in pats:
    lab, cls = STAT.get(p["status"], (p["status"], "s-open"))
    pat_rows += (f"<tr class='{'pat' if p['pattern'] else 'one'}'><td><span class='st {cls}'>{lab}</span></td>"
                 f"<td class=n>{p['games']}</td><td>{E(first.get(p['key'], ''))}</td><td class=did>{E(re.sub(r'^Needs Sora: ', '', p['note']))}</td></tr>")

cards = ""
for kind_, gid, title, when, kind, gen, me, aft, changed, fl in games:
    diffs = "".join(f"<li><span class=cat>{E(CAT.get(x['category'], x['category']))}</span> {E(x['note'])}</li>" for x in fl)
    after_col = (story(aft, gen) if changed else "<p class=same>No change from these fixes.</p>") if aft else "<p class=same>Not regenerated.</p>"
    cards += f"""<section class=game id=g{gid}>
<header><span class=kind>{kind}</span><h3>{E(title)}</h3><span class=when>{E(when)}</span></header>
<div class=cols>
<div class='col gen'><div class=lab>The site today</div>{story(gen, calls=gen.get('calls'))}</div>
<div class='col me'><div class=lab>Written from the facts alone</div>{story(me)}</div>
<div class='col aft'><div class=lab>The site with this round's fixes</div>{after_col}</div>
</div>
{f"<div class=diffs><div class=lab>What was different</div><ul>{diffs}</ul></div>" if diffs else ""}
</section>"""

M = [(compare.measures(g[5]), compare.measures(g[6])) for g in games]
avg = lambda i, k: mean(m[i][k] for m in M)
rep = lambda i: mean(len(m[i]['repeated_phrases']) for m in M)
nav = "".join(f"<a href='#g{g[1]}'>{E(g[2])}</a>" for g in games)
n_rec = sum(g[0] == "recap" for g in games); n_prev = len(games) - n_rec
page = f"""<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Voice Loop Pilot</title>
<link rel=preconnect href=https://fonts.googleapis.com><link rel=preconnect href=https://fonts.gstatic.com crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght,SOFT@9..144,600,100&family=Literata:opsz,wght@7..72,400;7..72,600&family=Hanken+Grotesk:wght@500;600;700&display=swap" rel=stylesheet>
<style>
:root{{--bg:#eef3f7;--card:#fff;--ink:#0f1c2b;--ink2:#3a4a5c;--ink3:#6f7e8e;--line:#d6e0e8;--soft:#f5f8fb;--mark:#fff1b8;--fix:#2f7d5b;--prop:#b25d12;--part:#6a6fb8}}
@media (prefers-color-scheme:dark){{:root:not([data-theme=light]){{--bg:#0e1823;--card:#152231;--ink:#e6edf3;--ink2:#b8c4cf;--ink3:#7c8a98;--line:#26384a;--soft:#1a2a3b;--mark:#5a4a12;--fix:#6cc49b;--prop:#f0a35e;--part:#a4a8ee}}}}
:root[data-theme=dark]{{--bg:#0e1823;--card:#152231;--ink:#e6edf3;--ink2:#b8c4cf;--ink3:#7c8a98;--line:#26384a;--soft:#1a2a3b;--mark:#5a4a12;--fix:#6cc49b;--prop:#f0a35e;--part:#a4a8ee}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--ink);font:17px/1.55 Literata,Georgia,serif}}
.wrap{{max-width:1360px;margin:0 auto;padding:32px 16px 80px}}
h1,h2,h3,h4{{font-family:Fraunces,Georgia,serif;font-variation-settings:"SOFT" 100;font-weight:600;margin:0}}
h1{{font-size:36px;line-height:1.15}} h2{{font-size:26px;margin:48px 0 8px}}
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
.cols{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}}
main,.wrap,.game,.col{{min-width:0}} .col{{overflow-wrap:anywhere;background:var(--soft);border-radius:12px;padding:14px 16px;font-size:15.5px}}
.col h4{{font-size:18px;margin:6px 0 8px}} .col p{{margin:0 0 10px}}
.col ul{{padding-left:18px;margin:6px 0;font-size:14.5px}} .col li b{{font:600 12px 'Hanken Grotesk',sans-serif;text-transform:uppercase;letter-spacing:.05em}}
.calls{{border-top:1px solid var(--line);margin-top:10px;padding-top:8px;font-size:14px;color:var(--ink2)}} .calls div{{margin:4px 0}}
mark{{background:var(--mark);color:inherit;border-radius:3px;padding:0 2px}}
.same{{color:var(--ink3);font-style:italic}}
.diffs{{margin-top:14px}} .diffs ul{{padding-left:0;list-style:none;margin:6px 0 0}} .diffs li{{margin:6px 0;font-size:15px;color:var(--ink2)}}
.cat{{display:inline-block;min-width:92px;color:var(--ink)}}
@media (max-width:1000px){{.cols{{grid-template-columns:1fr}}}}
@media (max-width:600px){{h1{{font-size:28px}}.game{{padding:14px}}td.n{{display:none}}}}
</style></head><body><div class=wrap>
<div class=lab>Spare Line Change · writing check, first round</div>
<h1>The site's stories, beside ones written from the same facts</h1>
<p class=lede>For {n_rec} post-game pages and {n_prev} previews, I was handed only the facts the site's writer sees and wrote the story I'd want to read, without looking at the site's version. Then I lined them up, logged every difference, and changed the site's writer wherever a difference kept turning up. The third column is the site's story with those changes; highlighted sentences are new.</p>
<div class=tiles>
<div class=tile><div class=lab>Words per story</div><b>{avg(0, 'words'):.0f} → {avg(1, 'words'):.0f}</b><span>site vs. mine</span></div>
<div class=tile><div class=lab>Sentences per story</div><b>{avg(0, 'sentences'):.1f} → {avg(1, 'sentences'):.1f}</b><span>site vs. mine</span></div>
<div class=tile><div class=lab>Repeated phrases</div><b>{rep(0):.1f} → {rep(1):.1f}</b><span>per story, site vs. mine</span></div>
<div class=tile><div class=lab>Differences logged</div><b>{len(finds)}</b><span>{sum(p['pattern'] for p in pats)} patterns, the rest one-offs</span></div>
</div>
<h2>How the loop works</h2>
<ol class=steps>
<li>Pick a dozen varied games: a shootout, an overtime game, a comeback, a shutout, a rout, a one-goal game, plus a few previews.</li>
<li>Hand me each game's facts only, never the site's text.</li>
<li>I write the story. A guard flags any number or name in it that isn't in the facts, so I can't teach the site something untrue.</li>
<li>Line the two up and log each difference. Seen in three or more games, it's a pattern.</li>
<li>Fix the site's writer for each pattern (unless it goes against one of your decisions), rerun, and check nothing came back.</li>
</ol>
<h2>What kept coming up</h2>
<table><thead><tr><th>Status</th><th class=n>Games</th><th>What was different</th><th>What I did</th></tr></thead><tbody>{pat_rows}</tbody></table>
<h2>Game by game</h2>
<div class=nav>{nav}</div>
{cards}
</div></body></html>"""
out.write_text(page)
print(out, len(page))
