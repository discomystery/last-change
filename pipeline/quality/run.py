"""Run every self-check, save the day's report and say what needs attention (see pipeline/quality/__init__.py)."""
import json
from datetime import date, datetime, timezone

from pipeline.config import CURRENT_SEASON, RAW

KEEP = 60  # daily reports kept


def _checks():
    from pipeline.quality import hindsight, official, outside, text
    return [("text", text.run), ("hindsight", hindsight.run), ("official", official.run), ("outside", outside.run)]


def run(season: int = CURRENT_SEASON) -> dict:
    report = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "season": season, "checks": {}}
    for name, fn in _checks():
        try:
            report["checks"][name] = fn(season)
        except Exception as e:  # one broken check must not hide the others
            report["checks"][name] = {"level": "problem", "problems": [{"what": f"the {name} check itself failed: {e!r}"}]}
    report["problems"] = sum(len(c.get("problems") or []) for c in report["checks"].values() if c.get("level") == "problem")
    return report


def save(report: dict) -> None:
    folder = RAW / "quality"
    folder.mkdir(parents=True, exist_ok=True)
    body = json.dumps(report, indent=1, ensure_ascii=False)
    (folder / f"{date.today().isoformat()}.json").write_text(body)
    (folder / "latest.json").write_text(body)
    for old in sorted(folder.glob("20*.json"))[:-KEEP]:
        old.unlink()


TITLES = {"text": "Text on the pages", "hindsight": "Previews against what happened",
          "official": "Our totals against the NHL's official reports", "outside": "Our scores and standings against ESPN"}


def alarm(report: dict) -> str:
    """The problems as a short Markdown note for a GitHub issue; empty when there are none."""
    lines = []
    for name, c in report["checks"].items():
        if c.get("level") != "problem":
            continue
        lines.append(f"### {TITLES.get(name, name)}")
        for p in c.get("problems") or []:
            where = f"{p['page']}: " if p.get("page") else ""
            lines.append(f"- {where}{p['what']}" + (f" — “{p['text']}”" if p.get("text") else ""))
        if c.get("count", 0) > len(c.get("problems") or []):
            lines.append(f"- … {c['count'] - len(c['problems'])} more in data/raw/quality/latest.json on the data branch")
        if c.get("refetch"):
            lines.append(f"- Asked for {len(c['refetch'])} games to be fetched again on the next update: {', '.join(map(str, c['refetch']))}")
    if not lines:
        return ""
    return "The nightly self-check found problems on the site (pipeline/quality). Nothing was changed on the pages.\n\n" + "\n".join(lines)


def summary(report: dict) -> str:
    out = []
    for name, c in report["checks"].items():
        n = len(c.get("problems") or [])
        out.append(f"{name}: {c.get('level')}" + (f" ({c.get('count', n)} problems)" if n else ""))
    h = report["checks"].get("hindsight") or {}
    if h.get("starters"):
        s = h["starters"]["nhl"]
        if s["picks"]:
            out.append(f"  NHL.com starters right {s['right']} of {s['picks']} in the last 14 days")
    return "\n".join(out)
