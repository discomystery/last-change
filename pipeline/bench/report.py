"""Markdown for the bench and the text samples, alone or as a before-and-after comparison (pull request comments)."""
import difflib

KINDS = {"edge": "Widest gap (edge)", "control": "Who controls the chances", "clash": "Strength into weakness (clash)",
         "duel": "Strength vs. strength (duel)", "pace": "Pace", "physical": "Hitting", "rest": "Rested against tired",
         "changed": "Changed team", "matchup": "Line matchup", "history": "Goalie history"}
MAX_CHARS = 60000  # GitHub comments stop at 65,536


def name(lab: str) -> str:
    """A trait key in words: "dShots" is "shots from defensemen", "quality offense" is "chance quality, attack wins"."""
    from pipeline.export import previews
    words = {"fast": "fast game", "slow": "slow game", "rest": "rested team", "control": "chance share", "physical": "physicality"}
    key, _, lean = lab.partition(" ")
    base = words.get(key) if not lean and key in words else previews.EDGE.get(key, (key,))[0]
    return f"{base}, {'attack wins' if lean == 'offense' else 'defense wins'}" if lean else base


def _pct(x: dict | None) -> str:
    return "–" if not x or x.get("rate") is None else f"{100 * x['rate']:.0f}%"


def _n(x: dict | None) -> int:
    return (x or {}).get("graded", 0)


def _over(x: dict | None, c: dict | None) -> str:
    if not x or not c or x.get("rate") is None or c.get("rate") is None:
        return "–"
    d = 100 * (x["rate"] - c["rate"])
    return f"{d:+.0f}"


def calls_table(r: dict) -> list[str]:
    seasons = [s for s in r["seasons"]]
    out = [f"Every preview call the current rules would make, replayed over {sum(v['games'] for v in r['seasons'].values()):,} games "
           f"({', '.join(_season(s) for s in seasons)}), with the morning's numbers only. Partly counts as half a hold. "
           "**By chance** is the same check with the reason taken away (any team, any game).", "",
           "| Call | Calls | Held | By chance | Over chance | " + " | ".join(_season(s) for s in seasons) + " | Live this season |",
           "|---|---:|---:|---:|---:|" + "---:|" * len(seasons) + "---:|"]
    for k in sorted(r["published"], key=lambda k: -_n(r["published"][k]["all"]["all"])):
        labs = r["published"][k]
        a, c = labs["all"]["all"], r["chance"].get(k, {}).get("all", {}).get("all")
        live = (r.get("live") or {}).get(k)
        out.append(f"| **{KINDS.get(k, k)}** | {_n(a):,} | {_pct(a)} | {_pct(c)} | {_over(a, c)} | "
                   + " | ".join(_pct(labs["all"].get(s)) for s in seasons) + f" | {_pct(live)} ({_n(live)}) |")
        for lab in sorted((x for x in labs if x != "all"), key=lambda x: -_n(labs[x]["all"])):
            if len(labs) <= 2:
                break
            x, cx = labs[lab]["all"], r["chance"].get(k, {}).get(lab, {}).get("all")
            out.append(f"| &nbsp;&nbsp;{name(lab)} | {_n(x):,} | {_pct(x)} | {_pct(cx)} | {_over(x, cx)} | "
                       + " | ".join(_pct(labs[lab].get(s)) for s in seasons) + " | |")
    for k in ("matchup", "history"):
        live = (r.get("live") or {}).get(k)
        if live:
            out.append("| " + " | ".join([f"**{KINDS[k]}** (not replayed)", *[""] * (4 + len(seasons)), f"{_pct(live)} ({_n(live)})"]) + " |")
    f = r.get("fidelity")
    if f:
        out += ["", f"Team styles rebuilt for {f['day']} matched the site's saved ones on {f['matched']} of {f['of']} team-traits."]
    return out


def _season(s: str) -> str:
    s = int(s)
    return f"{s}-{str(s + 1)[-2:]}"


def calls_compare(base: dict, head: dict) -> list[str]:
    """Kinds and traits whose replayed record moved, before and after."""
    rows = []
    for k in sorted(set(base["published"]) | set(head["published"])):
        labs = set(base["published"].get(k, {})) | set(head["published"].get(k, {}))
        for lab in sorted(labs, key=lambda x: (x != "all", x)):
            b = base["published"].get(k, {}).get(lab, {}).get("all")
            h = head["published"].get(k, {}).get(lab, {}).get("all")
            if _n(b) == _n(h) and (b or {}).get("rate") == (h or {}).get("rate"):
                continue
            c = head["chance"].get(k, {}).get(lab, {}).get("all")
            row = f"**{KINDS.get(k, k)}**" if lab == "all" else f"{KINDS.get(k, k)}: {name(lab)}"
            rows.append(f"| {row} | {_n(b):,} → {_n(h):,} | {_pct(b)} → {_pct(h)} | {_pct(c)} |")
    if not rows:
        return ["No call's replayed record changed."]
    hb, hh = _total(base), _total(head)
    return [f"All calls: {hb[0]:,} → {hh[0]:,} graded, held {hb[1]} → {hh[1]}.", "",
            "| Call | Calls | Held | By chance |", "|---|---:|---:|---:|", *rows]


def _total(r: dict) -> tuple[int, str]:
    held = graded = 0.0
    for k, labs in r["published"].items():
        a = labs["all"]["all"]
        graded += a["graded"]
        held += a["held"] + 0.5 * a["partly"]
    return int(graded), (f"{100 * held / graded:.0f}%" if graded else "–")


def samples_compare(base: dict, head: dict) -> tuple[list[str], int]:
    """Changed sentences, game by game and page by page."""
    out, changed = [], 0
    for gid, h in head["games"].items():
        b = base["games"].get(gid, {"pages": {}})
        parts = []
        for page in dict.fromkeys([*b["pages"], *h["pages"]]):
            old = [t for _, t in b["pages"].get(page, [])]
            new = [t for _, t in h["pages"].get(page, [])]
            if old == new:
                continue
            lines = []
            for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=old, b=new, autojunk=False).get_opcodes():
                if op == "equal":
                    continue
                for t in old[i1:i2]:
                    lines.append(f"- {_q(t)}")
                for t in new[j1:j2]:
                    lines.append(f"+ {_q(t)}")
            changed += 1
            parts.append(f"**{page}**\n\n```diff\n" + "\n".join(lines) + "\n```")
        if parts:
            out.append(f"<details><summary>{h['game']}, {h['date']} ({len(parts)} {'page' if len(parts) == 1 else 'pages'})</summary>\n\n"
                       + "\n\n".join(parts) + "\n\n</details>")
    return out, changed


def _q(t: str) -> str:
    return " ".join(t.split())


def pr_comment(calls_base: dict | None, calls_head: dict | None, samples_base: dict | None, samples_head: dict | None) -> str:
    out = ["## Bench: preview calls and page text", ""]
    if calls_base and calls_head:
        out += ["### Preview calls, replayed over past seasons", "", *calls_compare(calls_base, calls_head), ""]
        out += ["<details><summary>Every call with this pull request</summary>", "", *calls_table(calls_head), "", "</details>", ""]
    if samples_base and samples_head:
        diffs, n = samples_compare(samples_base, samples_head)
        out += [f"### Text on {len(samples_head['games'])} sample games", ""]
        out += [f"Wording changed on {n} {'page' if n == 1 else 'pages'}." if n else "No sentence changed on the sample pages.", ""]
        out += diffs
    body = "\n".join(out)
    if len(body) > MAX_CHARS:
        body = body[:MAX_CHARS].rsplit("\n<details>", 1)[0] + "\n\n…the rest is in the workflow run's bench-report file."
    return body + "\n\n---\n_Generated by [Claude Code](https://claude.ai/code)_\n"
