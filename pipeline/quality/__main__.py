"""python -m pipeline.quality [--alarm] [--season YYYY]: run the self-checks and save the report. With --alarm, print
only the problems (nothing when all is well), for the nightly workflow to turn into an issue."""
import argparse

from pipeline.config import CURRENT_SEASON
from pipeline.quality import run

p = argparse.ArgumentParser()
p.add_argument("--alarm", action="store_true")
p.add_argument("--season", type=int, default=CURRENT_SEASON)
a = p.parse_args()
report = run.run(a.season)
run.save(report)
print(run.alarm(report) if a.alarm else run.summary(report))
