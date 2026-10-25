"""Injectable, per-source rate-limited HTTP layer (tests pass a fake transport and clock)."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

USER_AGENT = "fightcard/0.1 (+https://ufc.xlobax.com; non-commercial fan site)"


@dataclass(frozen=True)
class Response:
    status: int  # 0 = transport error
    body: bytes

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    def json(self) -> Any:
        return json.loads(self.body)


Transport = Callable[[str, dict | None], Response]


def urllib_transport(url: str, params: dict | None = None, timeout: float = 15.0) -> Response:
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return Response(resp.status, resp.read())
    except urllib.error.HTTPError as e:
        return Response(e.code, b"")


def source_of(url: str) -> str:
    """Rate-limit bucket: the registrable domain, so all *.espn.com/*.espncdn.com share one."""
    host = urllib.parse.urlsplit(url).hostname or ""
    if "espn" in host:
        return "espn"
    return ".".join(host.split(".")[-2:])


class HttpClient:
    def __init__(
        self,
        transport: Transport = urllib_transport,
        min_interval: float = 1.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.transport = transport
        self.min_interval = min_interval
        self._clock = clock
        self._sleep = sleep
        self._last: dict[str, float] = {}

    def get(self, url: str, params: dict | None = None) -> Response:
        source = source_of(url)
        last = self._last.get(source)
        if last is not None:
            wait = self.min_interval - (self._clock() - last)
            if wait > 0:
                self._sleep(wait)
        try:
            return self.transport(url, params)
        except Exception:  # noqa: BLE001 - any network failure is just a miss
            return Response(0, b"")
        finally:
            self._last[source] = self._clock()
