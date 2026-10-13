"""Build the playoff test site from the replay's site data, as a folder of pages that link to each other by relative
paths (so it can be published as one private page with its files, away from the live site).

    uv run --project pipeline python -m pipeline.playoffs.build_site [--season 2025]

Copies site/ into data/playoffs/{season}/build (never touching site/public/data), drops the pages the playoff data
can't fill (team, player and league pages need the full season's player files), builds, then rewrites every
root-relative link to a relative one and points links to pages that aren't in the build nowhere.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DROP = ["team", "player", "players", "players.astro", "teams.astro", "league.astro", "data"]


def build(season: int) -> Path:
    base = ROOT / "data" / "playoffs" / str(season)
    src, data, work = ROOT / "site", base / "site", base / "build"
    if work.exists():
        shutil.rmtree(work)
    shutil.copytree(src, work, ignore=shutil.ignore_patterns("node_modules", "dist", ".astro"))
    (work / "node_modules").symlink_to(src / "node_modules")
    shutil.rmtree(work / "public" / "data")
    shutil.copytree(data, work / "public" / "data")
    stub = work / "public" / "data" / "players" / "index.json"
    stub.parent.mkdir(exist_ok=True)
    if not stub.exists():
        stub.write_text("[]")
    for name in DROP:
        p = work / "src" / "pages" / name
        if p.is_dir():
            shutil.rmtree(p)
        elif p.exists():
            p.unlink()
    (work / "public" / "CNAME").unlink(missing_ok=True)
    subprocess.run(["npm", "run", "build"], cwd=work, check=True, env={**os.environ, "SITE_BASE": "/"})
    dist = work / "dist"
    relative(dist)
    return dist


ATTR = re.compile(r'(\s(?:href|src|data-src|data-base|action|poster)=")(/[^"]*)"')


# Links the pages build in the browser end in a folder ("recap/2025030111/"); the page host wants the file itself.
FOLDERS = ("<script>document.addEventListener('click',function(e){var a=e.target.closest&&e.target.closest('a[href]');"
           "if(!a)return;var h=a.getAttribute('href');if(!h||/^[a-z]+:/i.test(h)||h.charAt(0)==='#')return;"
           "var m=h.match(/^([^#]*\\/)(#.*)?$/);if(!m)return;e.preventDefault();"
           "location.href=new URL(m[1]+'index.html'+(m[2]||''),location.href).href;},true);</script>")


def relative(dist: Path) -> None:
    pages = {p.relative_to(dist).as_posix() for p in dist.rglob("index.html")}
    files = {p.relative_to(dist).as_posix() for p in dist.rglob("*") if p.is_file()}
    for f in dist.rglob("*.html"):
        depth = len(f.relative_to(dist).parts) - 1
        up = "../" * depth or "./"

        def fix(m: re.Match) -> str:
            attr, url = m.group(1), m.group(2)
            if url.startswith("//"):
                return m.group(0)
            path, _, frag = url.partition("#")
            path = path.lstrip("/")
            if attr.strip().startswith("data-base"):
                return f'{attr}{up}"'
            if path == "" or path.endswith("/"):
                target = path + "index.html"
                if target not in pages:
                    return f'{attr}#"' if attr.strip().startswith("href") else m.group(0)
                path = target
            elif path not in files and attr.strip().startswith("href"):
                return f'{attr}#"'
            return f'{attr}{up}{path}{"#" + frag if frag else ""}"'

        f.write_text(ATTR.sub(fix, f.read_text()).replace("</body>", FOLDERS + "</body>", 1))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--season", type=int, default=2025)
    a = p.parse_args()
    d = build(a.season)
    print(json.dumps({"dist": str(d), "files": sum(1 for x in d.rglob("*") if x.is_file())}))
