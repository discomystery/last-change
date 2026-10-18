"""The home page's game rows (Sora, 2026-10-18: league leaders broke when every result row failed)."""
import re
from pathlib import Path

from pipeline.export import home, stories


def test_story_helpers_exist():
    # home.py borrows private helpers from stories.py; a rename there emptied every result row once (#202).
    used = set(re.findall(r"\bstories\.(\w+)", Path(home.__file__).read_text()))
    assert used and not [n for n in used if not hasattr(stories, n)]


def test_blown_lead():
    g = lambda hs, as_: {"hs": hs, "as": as_}
    goals = [g(1, 0), g(2, 0), g(2, 1), g(2, 2), g(3, 2)]
    assert home._blown(goals, True) == "2-0"
    assert home._blown(goals[:3] + [g(3, 1)], True) is None
