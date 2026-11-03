"""One live game as a small file, in the same shape as the post-game flow file (export/flow.py), so the site's flow
chart draws it unchanged, plus a `live` block (clock, score, shots on goal, when it was made).

Built from the play-by-play alone. The NHL fills the shift chart in late, so during a game there is no on-ice list:
skater counts and empty nets come from the play-by-play situation code, shots are rated with the live chance model
(xg_model.saved_live: the site's model without the three shift-time features), and the file has no shifts, lines
or Swing. Once a game is final, its post-game page takes over with the full numbers.
"""
from datetime import datetime, timezone

import polars as pl

from pipeline.build import games, shot_features
from pipeline.build.stints import game_seconds
from pipeline.export import flow
from pipeline.ingest import names
from pipeline.metrics import xg_model

UNBLOCKED = {"shot-on-goal", "missed-shot", "goal"}


def skaters_on(events: list[dict]) -> list[list[int]]:
    """[sec, away skaters, home skaters, away goalie in, home goalie in] each time the situation code changes (the same
    rows flow.skaters_on makes from the shifts)."""
    out = []
    for e in events:
        c = e["situation_code"] or ""
        if len(c) != 4 or not c.isdigit():
            continue
        v = [int(c[1]), int(c[2]), int(c[0]), int(c[3])]
        if not out or out[-1][1:] != v:
            out.append([int(e["sec"]), *v])
    return out


def pulled(events: list[dict], end: int) -> list[list[int]]:
    """[side (1 home), from, to] for each stretch of 10+ seconds a side's net was empty for an extra attacker, from the
    situation codes. Not a delayed penalty, where the goalie comes off only until the other side touches the puck (the
    post-game flow file reads pulled goalies from the goalie's shifts, which leave those out too)."""
    out, since, delayed = [], [None, None], False
    for e in events:
        c = e["situation_code"] or ""
        if e["type"] == "delayed-penalty":
            delayed = True
        elif e["type"] in ("penalty", "stoppage", "faceoff", "goal", "period-end"):
            delayed = False
        if len(c) != 4 or not c.isdigit():
            continue
        for h in (0, 1):
            empty = c[3 if h else 0] == "0" and not delayed
            if empty and since[h] is None:
                since[h] = int(e["sec"])
            elif not empty and since[h] is not None:
                out.append([h, since[h], int(e["sec"])])
                since[h] = None
    out += [[h, since[h], end] for h in (0, 1) if since[h] is not None]
    return sorted(p for p in out if p[2] - p[1] >= 10)


def now_sec(pbp: dict) -> int:
    """Game time reached, in seconds from the opening faceoff (the clock counts down, so period length minus what is left)."""
    pd = pbp.get("periodDescriptor") or {}
    period = pd.get("number") or 1
    if pd.get("periodType") == "SO":
        period = 4
    left = (pbp.get("clock") or {}).get("timeRemaining") or "00:00"
    length = 300 if pd.get("periodType") == "OT" and pbp.get("gameType") == 2 else 1200
    m, s = (int(x) for x in left.split(":"))
    return game_seconds(period, "00:00") + max(0, length - 60 * m - s)


def game(pbp: dict, model: dict) -> dict:
    gid = pbp["id"]
    events, _ = games.pbp_events(pbp, [], live=True)
    xg = {}
    feats = shot_features.rows(events, {})
    if feats:
        df = xg_model.with_derived(pl.DataFrame(feats, infer_schema_length=None))
        if df.height:
            xg = dict(zip(df["event_id"].to_list(), xg_model.predict(model, df).tolist()))
    clips = flow.clips(pbp)
    shots = []
    for e in events:
        if e["type"] not in UNBLOCKED or e["x_norm"] is None or e["is_home"] is None or e["event_id"] not in xg:
            continue
        c = e["situation_code"] or "1551"
        h = int(e["is_home"])
        goal = e["type"] == "goal"
        shots.append([int(e["sec"]), h, round(xg[e["event_id"]], 3), int(goal), round(e["x_norm"], 1), round(e["y_norm"], 1), e["p1"],
                      int(c[0] == "0") if h else int(c[3] == "0"), flow.strength(c, h), e["shot_type"],
                      e["p2"] if goal else None, e["p3"] if goal else None])
        if goal and clips.get(e["event_id"]):
            shots[-1].append(clips[e["event_id"]])
    blocks = [[int(e["sec"]), int(e["is_home"]), round(e["x_norm"], 1), round(e["y_norm"], 1), e["p2"], e["p1"]]
              for e in events if e["type"] == "blocked-shot" and e["reason"] != "teammate-blocked" and e["x_norm"] is not None
              and e["is_home"] is not None and e["p2"] is not None]
    pens = [[int(e["sec"]), int(bool(e["is_home"])), e["p1"], e["p2"], e["pen_min"], e["pen_type"], e["pen_desc"], e["p3"]]
            for e in events if e["type"] == "penalty" and e["is_home"] is not None]
    state = pbp.get("gameState")
    final = state in ("FINAL", "OFF")
    reached = max([now_sec(pbp)] + [int(e["sec"]) for e in events])
    length = max([int(e["sec"]) for e in events], default=0) if final else reached
    pp = flow.power_plays([(int(e["sec"]), e["situation_code"]) for e in events], length)
    roster = [{"player_id": r["playerId"], "number": r.get("sweaterNumber"),
               **dict(zip(("first", "last"), names.spell(r["playerId"], r["firstName"], r["lastName"])))} for r in pbp.get("rosterSpots", [])]
    from pipeline.export import wording
    short = wording.short_names(roster, key=None)
    ids = {s[i] for s in shots for i in (6, 10, 11)} | {b[i] for b in blocks for i in (4, 5)} | {p[i] for p in pens for i in (2, 3, 7)}
    so = flow.shootout(pbp) if (pbp.get("periodDescriptor") or {}).get("periodType") == "SO" else None
    ids |= {a[i] for a in (so or []) for i in (1, 2)}
    away, home = pbp["awayTeam"], pbp["homeTeam"]
    clock = pbp.get("clock") or {}
    pd = pbp.get("periodDescriptor") or {}
    return {
        "game_id": gid, "len": length, "shots": shots, "pp": pp, "players": [], "goalies": [], "blocks": blocks, "pens": pens,
        "str": skaters_on(events), "pulled": pulled(events, length), **({"so": so} if so else {}),
        "names": {str(pid): short[pid] for pid in ids if pid in short},
        "live": {"state": state, "period": pd.get("number"), "ptype": pd.get("periodType"), "left": clock.get("timeRemaining"),
                 "break": bool(clock.get("inIntermission")), "running": bool(clock.get("running")), "sec": reached,
                 "score": [away.get("score", 0), home.get("score", 0)], "sog": [away.get("sog", 0), home.get("sog", 0)],
                 "away": away.get("abbrev"), "home": home.get("abbrev"),
                 "made": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")},
    }
