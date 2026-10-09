"""Polite HTTP: one shared client, rate-limited, with retries and backoff."""
import time

import httpx

from pipeline.config import REQUESTS_PER_SECOND, USER_AGENT

_client = httpx.Client(headers={"User-Agent": USER_AGENT}, follow_redirects=True, timeout=30)
_last = 0.0


def get(url: str, *, tries: int = 5) -> httpx.Response:
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
