"""Polite ufcstats HTTP client.

- One request at a time, at least `min_interval` seconds apart.
- 403/429/5xx: back off `backoff` seconds and retry; two in a row halts the run.
- Proof-of-work challenges are solved in Python (see pow.py). If that fails twice
  the `browser` callable (StealthyFetcher by default) obtains the `_fmc` cookie.
- `_fmc` is cached to `cookie_file` for 7 days so CI can reuse it across runs.

Transport, clock, sleep and browser are injectable so tests stay offline.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol
from urllib.parse import urljoin

from scrapling.fetchers import Fetcher, StealthyFetcher

from fightcard.ufcstats.pow import is_challenge, parse_challenge, solve

log = logging.getLogger(__name__)

COOKIE_NAME = "_fmc"
COOKIE_TTL = 7 * 86400
MAX_SOLVES = 2


class Halt(Exception):
    """ufcstats is blocking us; stop the run without writing anything."""


class HttpError(Exception):
    def __init__(self, url: str, status: int):
        super().__init__(f"HTTP {status} on {url}")
        self.url = url
        self.status = status


@dataclass
class HttpResponse:
    status: int
    text: str
    cookies: dict[str, str] = field(default_factory=dict)


class Transport(Protocol):
    def get(self, url: str, cookies: dict[str, str]) -> HttpResponse: ...

    def post(self, url: str, data: dict[str, str], cookies: dict[str, str]) -> HttpResponse: ...


def _cookie_dict(cookies) -> dict[str, str]:
    """Scrapling returns cookies as a dict, or as a sequence of {name, value} dicts."""
    if isinstance(cookies, dict):
        return {str(k): str(v) for k, v in cookies.items()}
    out = {}
    for c in cookies or ():
        if isinstance(c, dict) and "name" in c:
            out[c["name"]] = c.get("value", "")
    return out


def _to_response(resp) -> HttpResponse:
    return HttpResponse(status=resp.status, text=resp.html_content, cookies=_cookie_dict(resp.cookies))


class ScraplingTransport:
    """curl_cffi via scrapling Fetcher, impersonating Chrome."""

    def get(self, url: str, cookies: dict[str, str]) -> HttpResponse:
        return _to_response(Fetcher.get(url, cookies=cookies, impersonate="chrome", timeout=30))

    def post(self, url: str, data: dict[str, str], cookies: dict[str, str]) -> HttpResponse:
        return _to_response(
            Fetcher.post(url, data=data, cookies=cookies, impersonate="chrome", timeout=30)
        )


def stealthy_cookie(url: str) -> str | None:
    """Fallback: let a real (patchright) browser pass the challenge and return `_fmc`."""
    page = StealthyFetcher.fetch(url, headless=True, network_idle=True, wait=3000)
    return _cookie_dict(page.cookies).get(COOKIE_NAME)


def default_cookie_file() -> Path:
    cache = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(cache) / "fightcard" / "ufcstats_cookie.json"


class Client:
    def __init__(
        self,
        transport: Transport | None = None,
        cookie_file: Path | None = None,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        browser: Callable[[str], str | None] = stealthy_cookie,
        min_interval: float = 3.0,
        backoff: float = 120.0,
    ):
        self.transport = transport or ScraplingTransport()
        self.cookie_file = cookie_file or default_cookie_file()
        self.clock = clock
        self.sleep = sleep
        self.browser = browser
        self.min_interval = min_interval
        self.backoff = backoff
        self.requests = 0
        self._last_request: float | None = None
        self._bad_streak = 0
        self._cookie = self._load_cookie()

    # -- cookie cache ------------------------------------------------------

    def _load_cookie(self) -> str | None:
        try:
            saved = json.loads(self.cookie_file.read_text())
            if self.clock() - float(saved["obtained_at"]) <= COOKIE_TTL:
                return str(saved["value"])
        except (OSError, ValueError, KeyError, TypeError):
            pass
        return None

    def _store_cookie(self, value: str) -> None:
        self._cookie = value
        self.cookie_file.parent.mkdir(parents=True, exist_ok=True)
        self.cookie_file.write_text(json.dumps({"value": value, "obtained_at": self.clock()}))

    def _cookies(self) -> dict[str, str]:
        return {COOKIE_NAME: self._cookie} if self._cookie else {}

    # -- requests ----------------------------------------------------------

    def _wait(self) -> None:
        if self._last_request is not None:
            remaining = self.min_interval - (self.clock() - self._last_request)
            if remaining > 0:
                self.sleep(remaining)
        self._last_request = self.clock()

    def _send(self, send: Callable[[], HttpResponse], url: str) -> HttpResponse:
        while True:
            self._wait()
            resp = send()
            self.requests += 1
            if resp.status in (403, 429) or resp.status >= 500:
                self._bad_streak += 1
                if self._bad_streak >= 2:
                    raise Halt(f"HTTP {resp.status} twice in a row ({url})")
                log.warning("HTTP %s on %s; backing off %ss", resp.status, url, self.backoff)
                self.sleep(self.backoff)
                continue
            self._bad_streak = 0
            return resp

    def _get(self, url: str) -> HttpResponse:
        return self._send(lambda: self.transport.get(url, self._cookies()), url)

    def _post_solution(self, url: str, html: str) -> bool:
        challenge = parse_challenge(html)
        n = solve(challenge)
        post_url = urljoin(url, challenge.path)
        data = {"nonce": challenge.nonce, "n": str(n)}
        resp = self._send(lambda: self.transport.post(post_url, data, self._cookies()), post_url)
        cookie = resp.cookies.get(COOKIE_NAME)
        if 200 <= resp.status < 300 and cookie:
            self._store_cookie(cookie)
            return True
        log.warning("challenge POST returned %s without %s", resp.status, COOKIE_NAME)
        return False

    def get(self, url: str) -> str:
        resp = self._get(url)
        for _ in range(MAX_SOLVES):
            if not is_challenge(resp.text):
                break
            try:
                self._post_solution(url, resp.text)
            except (ValueError, RuntimeError) as exc:
                log.warning("cannot solve challenge: %s", exc)
                break
            resp = self._get(url)

        if is_challenge(resp.text):
            log.warning("python solver failed; falling back to browser for %s", COOKIE_NAME)
            cookie = self.browser(url)
            if not cookie:
                raise Halt("browser could not obtain the challenge cookie")
            self._store_cookie(cookie)
            resp = self._get(url)
            if is_challenge(resp.text):
                raise Halt(f"challenge persists after browser cookie ({url})")

        if resp.status != 200:
            raise HttpError(url, resp.status)
        return resp.text
