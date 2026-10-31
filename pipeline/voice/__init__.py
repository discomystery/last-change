"""Write-and-compare voice loop (Sora, 2026-10-30, thread "Build a write-and-compare voice tuning loop").

The generator writes every preview and post-game story from rules. To find where its voice falls short, a Claude
session is handed only the facts of a game (a "packet", the same data the generator reads, never its text), writes the
story it thinks is best, and the two are lined up. The differences (lead, order, emphasis, what was left out or
overstated, repetition, tone) are logged per game; a difference seen across several games is a pattern and becomes a
generator change, a one-off stays in the log.

    python -m pipeline.voice sample  [--n 12] [--previews 3] [--run DATE]   pick varied games, write their packets
    python -m pipeline.voice check   [--run DATE]                           fact guard on the drafts
    python -m pipeline.voice after   [--run DATE]                           the run's games with the generator as it is now
    python -m pipeline.voice report  [--run DATE]                           side-by-side + automatic measures
    python -m pipeline.voice patterns                                       findings across every run
    python -m pipeline.voice.page voice/runs/DATE out.html                  the side-by-side page for Sora

Runs live in `voice/runs/{date}/` at the repository root: `{gid}.packet.json` (facts), `{gid}.claude.md` (the
draft), `{gid}.generator.json` (what the site says, kept for the record), `report.md`. Findings go to
`voice/findings.jsonl`, one line per difference, and `voice/patterns.json` holds each pattern's status (open, fixed
and in which PR, or kept on purpose). The steps a session follows are in `voice/README.md`.

Guardrail: `check` lists every number and proper name in a draft that the packet does not contain. A draft with an
unbacked fact is fixed before it is compared, so the loop can never teach the generator to say something the data
does not show.

Related: `pipeline.quality` checks the published text against our data and outside sources (correctness), and
`export/textcheck.py` holds the house rules (definite slips and style counts). This loop is about voice, and its
automatic measures reuse textcheck's sentence splitting and skeletons.
"""
