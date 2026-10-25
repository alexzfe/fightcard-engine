import json

import pytest
from fightcard.ufcstats.client import Client, Halt, HttpError, HttpResponse, TransportError

URL = "http://ufcstats.com/statistics/events/upcoming"
DAY = 86400


class FakeClock:
    def __init__(self, start: float = 1_000_000.0):
        self.now = start
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class FakeTransport:
    """Replays scripted responses and records every request."""

    def __init__(self, clock: FakeClock, responses: list[HttpResponse]):
        self.clock = clock
        self.responses = list(responses)
        self.calls: list[dict] = []

    def _next(self, **call) -> HttpResponse:
        call["at"] = self.clock()
        self.calls.append(call)
        nxt = self.responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

    def get(self, url, cookies):
        return self._next(method="GET", url=url, cookies=dict(cookies))

    def post(self, url, data, cookies, referer):
        return self._next(
            method="POST", url=url, data=dict(data), cookies=dict(cookies), referer=referer
        )


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def cookie_file(tmp_path):
    return tmp_path / "cache" / "fmc.json"


def no_browser(url):
    pytest.fail("browser fallback not expected")


def make_client(clock, cookie_file, responses, browser=no_browser):
    transport = FakeTransport(clock, responses)
    client = Client(
        transport=transport,
        cookie_file=cookie_file,
        clock=clock,
        sleep=clock.sleep,
        browser=browser,
    )
    return client, transport


def ok(text="<html>ok</html>", cookies=None):
    return HttpResponse(status=200, text=text, cookies=cookies or {})


def test_plain_page_is_returned_with_one_request(clock, cookie_file):
    client, transport = make_client(clock, cookie_file, [ok("hello")])
    assert client.get(URL) == "hello"
    assert [c["method"] for c in transport.calls] == ["GET"]


def test_valid_cached_cookie_is_sent(clock, cookie_file):
    cookie_file.parent.mkdir(parents=True)
    cookie_file.write_text(json.dumps({"value": "abc", "obtained_at": clock() - 6 * DAY}))
    client, transport = make_client(clock, cookie_file, [ok()])
    client.get(URL)
    assert transport.calls[0]["cookies"] == {"_fmc": "abc"}


def test_cookie_older_than_seven_days_is_ignored(clock, cookie_file):
    cookie_file.parent.mkdir(parents=True)
    cookie_file.write_text(json.dumps({"value": "abc", "obtained_at": clock() - 7 * DAY - 1}))
    client, transport = make_client(clock, cookie_file, [ok()])
    client.get(URL)
    assert transport.calls[0]["cookies"] == {}


def test_corrupt_cookie_file_is_ignored(clock, cookie_file):
    cookie_file.parent.mkdir(parents=True)
    cookie_file.write_text("not json")
    client, transport = make_client(clock, cookie_file, [ok()])
    client.get(URL)
    assert transport.calls[0]["cookies"] == {}


def test_challenge_is_solved_posted_and_cookie_cached(clock, cookie_file, fixture_html):
    challenge = fixture_html("challenge.html")
    client, transport = make_client(
        clock, cookie_file, [HttpResponse(200, challenge), ok("", {"_fmc": "fresh"}), ok("real")]
    )

    assert client.get(URL) == "real"

    _, post, get2 = transport.calls
    assert post["method"] == "POST"
    assert post["url"] == "http://ufcstats.com/__c"
    assert post["data"]["nonce"] == "efecdc18178fd36b"
    assert post["referer"] == URL
    assert int(post["data"]["n"]) >= 0
    assert get2["cookies"] == {"_fmc": "fresh"}
    saved = json.loads(cookie_file.read_text())
    assert saved["value"] == "fresh"
    assert saved["obtained_at"] == post["at"]


def test_cached_cookie_is_reused_by_a_new_client(clock, cookie_file, fixture_html):
    challenge = fixture_html("challenge.html")
    first, _ = make_client(
        clock, cookie_file, [HttpResponse(200, challenge), ok("", {"_fmc": "fresh"}), ok()]
    )
    first.get(URL)
    second, transport = make_client(clock, cookie_file, [ok()])
    second.get(URL)
    assert transport.calls[0]["cookies"] == {"_fmc": "fresh"}


def test_requests_are_spaced_at_least_three_seconds(clock, cookie_file, fixture_html):
    challenge = fixture_html("challenge.html")
    client, transport = make_client(
        clock, cookie_file, [HttpResponse(200, challenge), ok("", {"_fmc": "x"}), ok(), ok()]
    )
    client.get(URL)
    client.get(URL)
    times = [c["at"] for c in transport.calls]
    assert all(b - a >= 3.0 for a, b in zip(times, times[1:]))


def test_first_request_does_not_wait(clock, cookie_file):
    client, _ = make_client(clock, cookie_file, [ok()])
    client.get(URL)
    assert clock.sleeps == []


@pytest.mark.parametrize("status", [403, 429, 500, 503])
def test_backs_off_120s_then_retries(clock, cookie_file, status):
    client, transport = make_client(clock, cookie_file, [HttpResponse(status, ""), ok("after")])
    assert client.get(URL) == "after"
    assert 120 in clock.sleeps
    assert transport.calls[1]["at"] - transport.calls[0]["at"] >= 120


def test_halts_after_two_bad_statuses_in_a_row(clock, cookie_file):
    client, _ = make_client(clock, cookie_file, [HttpResponse(429, ""), HttpResponse(503, "")])
    with pytest.raises(Halt):
        client.get(URL)


def test_bad_status_counter_resets_after_success(clock, cookie_file):
    client, _ = make_client(
        clock, cookie_file, [HttpResponse(429, ""), ok(), HttpResponse(429, ""), ok("second")]
    )
    client.get(URL)
    assert client.get(URL) == "second"


def test_other_http_errors_raise_without_retry(clock, cookie_file):
    client, transport = make_client(clock, cookie_file, [HttpResponse(404, "nope")])
    with pytest.raises(HttpError) as err:
        client.get(URL)
    assert err.value.status == 404
    assert len(transport.calls) == 1


def test_falls_back_to_browser_after_two_failed_solves(clock, cookie_file, fixture_html):
    challenge = HttpResponse(200, fixture_html("challenge.html"))
    browser_calls = []

    def browser(url):
        browser_calls.append(url)
        return "from-browser"

    client, transport = make_client(
        clock,
        cookie_file,
        [challenge, ok("", {"_fmc": "a"}), challenge, ok("", {}), challenge, ok("real")],
        browser=browser,
    )
    assert client.get(URL) == "real"
    assert browser_calls == [URL]
    assert transport.calls[-1]["cookies"] == {"_fmc": "from-browser"}
    assert json.loads(cookie_file.read_text())["value"] == "from-browser"


def test_halts_when_browser_cannot_get_cookie(clock, cookie_file, fixture_html):
    challenge = HttpResponse(200, fixture_html("challenge.html"))
    client, _ = make_client(
        clock, cookie_file, [challenge, ok(), challenge, ok(), challenge], browser=lambda url: None
    )
    with pytest.raises(Halt):
        client.get(URL)


def test_halts_when_challenge_persists_after_browser_cookie(clock, cookie_file, fixture_html):
    challenge = HttpResponse(200, fixture_html("challenge.html"))
    client, _ = make_client(
        clock,
        cookie_file,
        [challenge, ok(), challenge, ok(), challenge, challenge],
        browser=lambda url: "b",
    )
    with pytest.raises(Halt):
        client.get(URL)


def test_unparseable_challenge_goes_straight_to_browser(clock, cookie_file):
    weird = HttpResponse(200, '<p>Checking your browser…</p><script>"/__c"</script>')
    client, transport = make_client(clock, cookie_file, [weird, ok("real")], browser=lambda url: "b")
    assert client.get(URL) == "real"
    assert [c["method"] for c in transport.calls] == ["GET", "GET"]


def test_request_count_is_tracked(clock, cookie_file):
    client, _ = make_client(clock, cookie_file, [ok(), ok()])
    client.get(URL)
    client.get(URL)
    assert client.requests == 2


# --- default adapters (scrapling), with scrapling itself faked -----------------

class FakeScraplingResponse:
    def __init__(self, status=200, html="<html/>", cookies=None):
        self.status = status
        self.html_content = html
        self.cookies = cookies if cookies is not None else {}


def test_scrapling_transport_get(monkeypatch):
    from fightcard.ufcstats import client as mod

    calls = []

    class FakeFetcher:
        @staticmethod
        def get(url, **kw):
            calls.append((url, kw))
            return FakeScraplingResponse(200, "page", ({"name": "_fmc", "value": "v"}, {"bad": 1}))

    monkeypatch.setattr(mod, "Fetcher", FakeFetcher)
    assert mod.ScraplingTransport().get(URL, {"_fmc": "x"}) == HttpResponse(200, "page", {"_fmc": "v"})
    assert calls[0][1]["impersonate"] == "chrome"
    assert calls[0][1]["cookies"] == {"_fmc": "x"}


class FakeCurlResponse:
    status_code = 204
    text = ""
    cookies = {"_fmc": "posted"}


def fake_curl_session(calls, raises=None):
    class FakeSession:
        def __init__(self, **kw):
            calls.append(("init", kw))

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def post(self, url, **kw):
            calls.append(("post", url, kw))
            if raises:
                raise raises
            return FakeCurlResponse()

    return FakeSession


def test_challenge_post_is_http1_form_with_origin_and_referer(monkeypatch):
    from fightcard.ufcstats import client as mod

    calls = []
    monkeypatch.setattr(mod, "CurlSession", fake_curl_session(calls))
    resp = mod.ScraplingTransport().post(
        "http://ufcstats.com/__c", {"nonce": "a+b/c", "n": "17"}, {"_fmc": "old"}, referer=URL
    )

    assert resp == HttpResponse(204, "", {"_fmc": "posted"})
    (_, init), (_, url, kw) = calls
    assert init["impersonate"] == "chrome"
    assert url == "http://ufcstats.com/__c"
    assert kw["data"] == "nonce=a%2Bb%2Fc&n=17"
    assert kw["http_version"] == "v1"
    assert kw["headers"] == {
        "Content-Type": "application/x-www-form-urlencoded",
        "Origin": "http://ufcstats.com",
        "Referer": URL,
    }
    assert kw["cookies"] == {"_fmc": "old"}


def test_transport_wraps_curl_errors(monkeypatch):
    from curl_cffi.requests.exceptions import RequestException

    from fightcard.ufcstats import client as mod

    class BrokenFetcher:
        @staticmethod
        def get(url, **kw):
            raise RequestException("curl: (16) framing")

    monkeypatch.setattr(mod, "Fetcher", BrokenFetcher)
    monkeypatch.setattr(mod, "CurlSession", fake_curl_session([], RequestException("curl: (16)")))
    transport = mod.ScraplingTransport()
    with pytest.raises(TransportError):
        transport.get(URL, {})
    with pytest.raises(TransportError):
        transport.post("http://ufcstats.com/__c", {"nonce": "a", "n": "1"}, {}, referer=URL)


def test_stealthy_cookie_returns_fmc(monkeypatch):
    from fightcard.ufcstats import client as mod

    class FakeStealthy:
        @staticmethod
        def fetch(url, **kw):
            assert kw["headless"] is True
            return FakeScraplingResponse(cookies=[{"name": "_fmc", "value": "browser"}])

    monkeypatch.setattr(mod, "StealthyFetcher", FakeStealthy)
    assert mod.stealthy_cookie(URL) == "browser"


def test_default_cookie_file_honours_xdg_cache_home(monkeypatch, tmp_path):
    from fightcard.ufcstats import client as mod

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    assert mod.default_cookie_file() == tmp_path / "fightcard" / "ufcstats_cookie.json"


# --- transport failures ---------------------------------------------------------


def test_204_from_challenge_post_counts_as_success(clock, cookie_file, fixture_html):
    challenge = HttpResponse(200, fixture_html("challenge.html"))
    client, _ = make_client(
        clock, cookie_file, [challenge, HttpResponse(204, "", {"_fmc": "c"}), ok("real")]
    )
    assert client.get(URL) == "real"
    assert json.loads(cookie_file.read_text())["value"] == "c"


def test_transport_error_on_challenge_post_falls_back_to_browser(clock, cookie_file, fixture_html):
    challenge = HttpResponse(200, fixture_html("challenge.html"))
    client, transport = make_client(
        clock,
        cookie_file,
        [challenge, TransportError("curl: (16)"), ok("real")],
        browser=lambda url: "from-browser",
    )
    assert client.get(URL) == "real"
    assert [c["method"] for c in transport.calls] == ["GET", "POST", "GET"]
    assert transport.calls[-1]["cookies"] == {"_fmc": "from-browser"}


def test_transport_error_on_get_backs_off_then_retries(clock, cookie_file):
    client, transport = make_client(clock, cookie_file, [TransportError("reset"), ok("after")])
    assert client.get(URL) == "after"
    assert 120 in clock.sleeps


def test_two_transport_errors_in_a_row_halt(clock, cookie_file):
    client, _ = make_client(clock, cookie_file, [TransportError("a"), TransportError("b")])
    with pytest.raises(Halt):
        client.get(URL)


def test_transport_error_and_bad_status_share_the_streak(clock, cookie_file):
    client, _ = make_client(clock, cookie_file, [HttpResponse(503, ""), TransportError("b")])
    with pytest.raises(Halt):
        client.get(URL)


def test_browser_crash_halts(clock, cookie_file):
    weird = HttpResponse(200, '<p>Checking your browser…</p><script>"/__c"</script>')

    def browser(url):
        raise RuntimeError("patchright missing")

    client, _ = make_client(clock, cookie_file, [weird], browser=browser)
    with pytest.raises(Halt):
        client.get(URL)
