"""python -m pipeline.bench calls|samples|choose|compare ... (see pipeline/bench/__init__.py)."""
import argparse
import json
import sys
from pathlib import Path


def main(argv: list[str]) -> None:
    if argv[:1] == ["samples-here"]:  # the child process of `samples`, with the site folder pointed at a copy
        from pipeline.bench import samples
        samples._rebuild_here(int(argv[1]), [int(x) for x in argv[2:]])
        return
    ap = argparse.ArgumentParser(prog="python -m pipeline.bench")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("calls", help="replay every preview call kind over past seasons")
    c.add_argument("--out", type=Path, required=True)
    c.add_argument("--seasons", type=int, nargs="*")
    c.add_argument("--progress", action="store_true")
    s = sub.add_parser("samples", help="rebuild the sample games' pages and save their text")
    s.add_argument("--out", type=Path, required=True)
    sub.add_parser("choose", help="pick a fresh set of sample games (writes pipeline/bench/sample_games.json)")
    m = sub.add_parser("compare", help="Markdown comparing two runs (any of them may be missing)")
    m.add_argument("--calls", type=Path, nargs=2, metavar=("BASE", "HEAD"))
    m.add_argument("--samples", type=Path, nargs=2, metavar=("BASE", "HEAD"))
    m.add_argument("--out", type=Path)
    t = sub.add_parser("table", help="Markdown table of one calls run")
    t.add_argument("file", type=Path)
    a = ap.parse_args(argv)
    if a.cmd == "calls":
        from pipeline.bench import calls
        r = calls.run(tuple(a.seasons) if a.seasons else calls.SEASONS, progress=a.progress)
        a.out.write_text(json.dumps(r, ensure_ascii=False))
        print(json.dumps(r["seasons"]))
    elif a.cmd == "samples":
        from pipeline.bench import samples
        r = samples.run()
        a.out.write_text(json.dumps(r, ensure_ascii=False))
        print(f"{len(r['games'])} games")
    elif a.cmd == "choose":
        from pipeline.bench import samples
        picked = samples.choose()
        samples.save_choice(picked)
        print(f"{len(picked)} games on {len({g['date'] for g in picked})} days")
    elif a.cmd == "table":
        from pipeline.bench import report
        print("\n".join(report.calls_table(json.loads(a.file.read_text()))))
    else:
        from pipeline.bench import report
        load = lambda p: json.loads(p.read_text()) if p and p.exists() else None
        cb, ch = (load(p) for p in (a.calls or (None, None)))
        sb, sh = (load(p) for p in (a.samples or (None, None)))
        body = report.pr_comment(cb, ch, sb, sh)
        (a.out.write_text(body) if a.out else print(body))


if __name__ == "__main__":
    main(sys.argv[1:])
