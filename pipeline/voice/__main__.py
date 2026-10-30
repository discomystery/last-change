import argparse
import json
import sys
from datetime import date
from pathlib import Path

import polars as pl

from pipeline.config import ROOT, SITE_DATA
from pipeline.voice import compare, packet

VOICE = ROOT / "voice"


def sample(n: int, n_prev: int, seed: str) -> list[tuple[int, str]]:
    """Varied finished games: one of each shape first (shootout, overtime, comeback, shutout, rout, one-goal game,
    hat trick, high-scoring, a quiet game), then the newest others; plus the next few previews. Stable for a seed."""
    import zlib
    rows = []
    for p in sorted((SITE_DATA / "recaps").glob("2*.json")):
        r = json.loads(p.read_text())
        if r.get("pending") or not r.get("story"):
            continue
        a, h = (r["score"][t] for t in (r["away"], r["home"]))
        head = (r["story"].get("head") or "").lower()
        rows.append({"gid": r["game_id"], "end": r.get("end"), "margin": abs(a - h), "total": a + h, "shutout": min(a, h) == 0,
                     "hat": "hat trick" in head, "comeback": any(w in head for w in ("comeback", "back from", "rally", "storm")),
                     "date": r["date"]})
    df = pl.DataFrame(rows).with_columns(pl.col("gid").map_elements(lambda g: zlib.crc32(f"{seed}{g}".encode()), return_dtype=pl.Int64).alias("k"))
    shapes = [pl.col("end") == "SO", pl.col("end") == "OT", pl.col("comeback"), pl.col("shutout"), pl.col("margin") >= 4,
              (pl.col("margin") == 1) & (pl.col("end") == "REG"), pl.col("hat"), pl.col("total") >= 9, pl.col("total") <= 3]
    picked: list[int] = []
    recent = df.filter(pl.col("date") >= sorted(df["date"])[max(len(df) - 90, 0)])  # the last couple of weeks of games
    for s in shapes:
        c = recent.filter(s & ~pl.col("gid").is_in(picked)).sort("k")
        if len(c) and len(picked) < n:
            picked.append(c["gid"][0])
    rest = recent.filter(~pl.col("gid").is_in(picked)).sort("k")["gid"].to_list()
    picked += rest[: max(n - len(picked), 0)]
    prev = sorted(int(p.name.split(".")[0]) for p in (SITE_DATA / "previews").glob("2*.json"))
    later = [g for g in prev if g > max(df["gid"])]
    return [(g, "recap") for g in picked] + [(g, "preview") for g in later[:: max(len(later) // max(n_prev, 1), 1)][:n_prev]]


def after(run_dir: Path) -> list[tuple[int, str]]:
    """Rewrite each game of a run with the generator as it is now, into `{gid}.after.json`, to see a change's effect
    beside the old text and the draft. Nothing in site data is changed."""
    from datetime import datetime
    from pipeline.export import previews, stories
    places = {t["abbr"]: t["place"] for t in json.loads((SITE_DATA / "teams.json").read_text())}
    sched = json.loads((SITE_DATA / "schedule.json").read_text())["games"]
    site, done = None, []
    for pk in sorted(run_dir.glob("*.packet.json")):
        gid, kind = int(pk.name.split(".")[0]), json.loads(pk.read_text())["kind"]
        if kind == "recap":
            st = stories.recap_story(json.loads((SITE_DATA / "recaps" / f"{gid}.json").read_text()), places)
            out = {"head": st["head"], "paras": st["paras"], "items": st["items"]}
        else:
            g = next((x for x in sched if x["id"] == gid), None)
            if g is None:
                continue
            site = site or previews.site_files(SITE_DATA)
            now = datetime.fromisoformat(json.loads((SITE_DATA / "previews" / f"{gid}.json").read_text())["snapshot_at"])
            snap = previews.snapshot(g, site, places, sched, now, previews._chances([g]).get(gid), previews._likely([g]).get(gid) or {},
                                     methods=previews.METHODS)
            st = snap.get("story") or {}
            out = {"head": st.get("head"), "paras": st.get("paras") or [], "items": st.get("items") or [], "outlook": snap.get("outlook"),
                   "calls": [{"head": c.get("head"), "body": c.get("body")} for c in snap["claims"]]}
        (run_dir / f"{gid}.after.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
        done.append((gid, kind))
    return done


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="python -m pipeline.voice")
    ap.add_argument("cmd", choices=["sample", "packet", "check", "after", "report", "patterns"])
    ap.add_argument("--run", default=date.today().isoformat())
    ap.add_argument("--n", type=int, default=12)
    ap.add_argument("--previews", type=int, default=3)
    ap.add_argument("--games", default="", help="comma-separated game ids for `packet` (recaps; add :p for a preview)")
    a = ap.parse_args(argv)
    run_dir = VOICE / "runs" / a.run
    if a.cmd in ("sample", "packet"):
        games = sample(a.n, a.previews, a.run) if a.cmd == "sample" else \
            [(int(g.split(":")[0]), "preview" if g.endswith(":p") else "recap") for g in a.games.split(",") if g]
        for gid, kind in games:
            packet.write(run_dir, gid, kind)
            print(f"{kind:8s} {gid}  -> {run_dir / f'{gid}.packet.json'}")
        return 0
    if a.cmd == "check":
        bad = 0
        for d in sorted(run_dir.glob("*.claude.md")):
            gid = d.name.split(".")[0]
            flags = compare.guard(d.read_text(), json.loads((run_dir / f"{gid}.packet.json").read_text()))
            bad += len(flags)
            print(f"{gid}: {'clean' if not flags else ''}")
            for f in flags:
                print(f"   {f['what']:11s} {f['value']!r:18s} {f['sentence']}")
        return 1 if bad else 0
    if a.cmd == "after":
        for gid, kind in after(run_dir):
            print(f"{kind:8s} {gid}  -> {run_dir / f'{gid}.after.json'}")
        return 0
    if a.cmd == "report":
        print(compare.report(run_dir, VOICE))
        return 0
    for p in compare.patterns(VOICE):
        print(f"{'PATTERN' if p['pattern'] else 'one-off':8s} {p['games']:3d} games  {p['status']:8s} {p['key']}: {p['note']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
