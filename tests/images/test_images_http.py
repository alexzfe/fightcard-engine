import pytest

from fightcard.images.http import HttpClient, Response


def test_response_json_and_ok():
    r = Response(200, b'{"a": 1}')
    assert r.ok and r.json() == {"a": 1}
    assert not Response(404, b"").ok


def test_first_request_does_not_sleep(transport, clock):
    client = HttpClient(transport, min_interval=1.0, clock=clock.time, sleep=clock.sleep)
    client.get("https://site.api.espn.com/x")
    assert clock.sleeps == []


def test_requests_to_same_source_are_spaced(transport, clock):
    client = HttpClient(transport, min_interval=1.0, clock=clock.time, sleep=clock.sleep)
    client.get("https://site.api.espn.com/a")
    clock.now += 0.25
    client.get("https://site.api.espn.com/b")
    assert clock.sleeps == [pytest.approx(0.75)]


def test_sources_are_rate_limited_independently(transport, clock):
    client = HttpClient(transport, min_interval=1.0, clock=clock.time, sleep=clock.sleep)
    client.get("https://site.api.espn.com/a")
    client.get("https://en.wikipedia.org/w/api.php")
    assert clock.sleeps == []


def test_espn_hosts_share_one_bucket(transport, clock):
    # site.api / site.web.api / a.espncdn are all ESPN: one source.
    client = HttpClient(transport, min_interval=1.0, clock=clock.time, sleep=clock.sleep)
    client.get("https://site.web.api.espn.com/apis/common/v3/search")
    client.get("https://a.espncdn.com/i/headshots/mma/players/full/1.png")
    assert clock.sleeps == [pytest.approx(1.0)]


def test_no_sleep_when_interval_already_elapsed(transport, clock):
    client = HttpClient(transport, min_interval=1.0, clock=clock.time, sleep=clock.sleep)
    client.get("https://en.wikipedia.org/a")
    clock.now += 5
    client.get("https://en.wikipedia.org/b")
    assert clock.sleeps == []


def test_transport_errors_become_status_zero(transport, clock):
    transport.add("https://en.wikipedia.org", OSError("boom"))
    client = HttpClient(transport, min_interval=1.0, clock=clock.time, sleep=clock.sleep)
    r = client.get("https://en.wikipedia.org/w/api.php")
    assert r.status == 0 and not r.ok


def test_params_are_passed_through(transport, clock):
    client = HttpClient(transport, min_interval=1.0, clock=clock.time, sleep=clock.sleep)
    client.get("https://en.wikipedia.org/w/api.php", {"titles": "Petr Yan"})
    assert transport.calls == [("https://en.wikipedia.org/w/api.php", {"titles": "Petr Yan"})]
