# Spare Line Change: live feed

The small site behind Spare Line Change's live game pages (sparelinechange.com/live/{game id}/). While NHL games are on,
`live.yml` follows them in shifts and every two minutes writes one small file per live game (shots with their chances,
penalties, power plays, the score and the clock) from the NHL's play-by-play, then `deploy.yml` publishes the files here.

Everything else lives in the main repository, discomystery/last-change: the code (`pipeline/live/`), and the master copy
of these files (`pipeline/live/site/`). Change them there and copy them over.

Game data: NHL. Chances are calculated with Spare Line Change's own model. Not affiliated with the NHL.
