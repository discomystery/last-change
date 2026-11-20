"""Polite HTTP: one shared client, rate-limited, with retries and backoff."""
import time

import httpx

from pipeline.config import OFFLINE, REQUESTS_PER_SECOND, USER_AGENT

_client = httpx.Client(headers={"User-Agent": USER_AGENT}, follow_redirects=True, timeout=30)
_last = 0.0


def get(url: str, *, tries: int = 5) -> httpx.Response:
    if OFFLINE:
        raise RuntimeError(f"offline run tried to fetch {url}")  # every caller should have used its saved copy
    global _last
    for attempt in range(tries):
        wait = _last + 1 / REQUESTS_PER_SECOND - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last = time.monotonic()
        try:
            res = _client.get(url)
            if res.status_code == 200:
                return res
            if res.status_code == 404:
                res.raise_for_status()
        except httpx.HTTPStatusError:
            raise
        except httpx.HTTPError:
            pass
        time.sleep(2**attempt)
    raise RuntimeError(f"gave up on {url}")


STATS_PAGE = 100  # the NHL stats API (api.nhle.com/stats) returns at most 100 rows a request, whatever `limit` says


def stats_rows(url: str, params: dict, key: str) -> list[dict]:
    """Every row of an NHL stats API report, a page of 100 at a time. Since November 2026 the API ignores `limit=-1`
    and stops at 100 rows (this season's skater report had 784), so pages are walked with `start`, sorted on the
    report's own sort with `key` (playerId, teamId...) added last so the pages never overlap."""
    from urllib.parse import urlencode
    import json

    sort = json.loads(params["sort"]) if "sort" in params else []
    if not any(s.get("property") == key for s in sort):
        sort.append({"property": key, "direction": "ASC"})
    rows: list[dict] = []
    while True:
        q = {**params, "sort": json.dumps(sort), "start": len(rows), "limit": STATS_PAGE}
        body = get(f"{url}?{urlencode(q)}").json()
        page = body.get("data")
        if page is None:
            raise ValueError(f"no data in {url}")
        rows += page
        total = body.get("total")
        if not page or len(page) < STATS_PAGE or (total is not None and len(rows) >= total):
            return rows
