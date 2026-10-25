from images_helpers import load_fixture

from fightcard.images.http import HttpClient, Response
from fightcard.images.sources import (
    ESPN_SCOREBOARD_URL,
    ESPN_SEARCH_URL,
    WIKIPEDIA_API_URL,
    ImageFinder,
    headshot_url,
    parse_espn_search,
    parse_scoreboard,
    parse_wikipedia,
)

SCOREBOARD = load_fixture("espn/scoreboard.json")
SEARCH = load_fixture("espn/search_islam_makhachev.json")
WIKI_HIT = load_fixture("wikipedia/hit_islam_makhachev.json")
WIKI_MISS = load_fixture("wikipedia/miss_noah_gugnon.json")


def test_headshot_url_pattern():
    assert headshot_url("3949584") == "https://a.espncdn.com/i/headshots/mma/players/full/3949584.png"


def test_parse_scoreboard_returns_every_athlete_on_the_card():
    athletes = parse_scoreboard(SCOREBOARD)
    assert len(athletes) == 28
    assert ("Alexander Volkanovski", "3949584") in athletes
    assert ("Lone'er Kavanagh", "5147737") in athletes


def test_parse_scoreboard_tolerates_garbage():
    assert parse_scoreboard({}) == []
    assert parse_scoreboard({"events": [{"competitions": [{"competitors": [{}]}]}]}) == []


def test_parse_espn_search_matches_mma_player():
    assert parse_espn_search(SEARCH, "Islam Makhachev") == "3332412"


def test_parse_espn_search_rejects_non_matching_name():
    assert parse_espn_search(SEARCH, "Islam Dulatov") is None


def test_parse_espn_search_ignores_other_sports():
    data = {"items": [{"id": "9", "displayName": "Islam Makhachev", "sport": "soccer"}]}
    assert parse_espn_search(data, "Islam Makhachev") is None


def test_parse_wikipedia_hit_returns_image_url():
    url = parse_wikipedia(WIKI_HIT, "Islam Makhachev")
    assert url.startswith("https://upload.wikimedia.org/")


def test_parse_wikipedia_miss_returns_none():
    assert parse_wikipedia(WIKI_MISS, "Noah Gugnon") is None


def test_parse_wikipedia_prefers_thumbnail():
    data = {"query": {"pages": {"1": {"title": "Petr Yan",
            "thumbnail": {"source": "https://upload.wikimedia.org/thumb.jpg"},
            "original": {"source": "https://upload.wikimedia.org/orig.jpg"}}}}}
    assert parse_wikipedia(data, "Petr Yan") == "https://upload.wikimedia.org/thumb.jpg"


def test_parse_wikipedia_rejects_page_with_different_title():
    # A redirect to some other article must not give us someone else's photo.
    data = {"query": {"pages": {"1": {"title": "Magomedov",
            "original": {"source": "https://upload.wikimedia.org/x.jpg"}}}}}
    assert parse_wikipedia(data, "Abus Magomedov") is None


def test_parse_wikipedia_accepts_disambiguated_title():
    data = {"query": {"pages": {"1": {"title": "Daniel Santos (fighter)",
            "original": {"source": "https://upload.wikimedia.org/x.jpg"}}}}}
    assert parse_wikipedia(data, "Daniel Santos") == "https://upload.wikimedia.org/x.jpg"


def _finder(transport, clock):
    return ImageFinder(HttpClient(transport, min_interval=1.0, clock=clock.time, sleep=clock.sleep))


def test_finder_uses_scoreboard_before_search(transport, clock):
    transport.add_json(ESPN_SCOREBOARD_URL, SCOREBOARD)
    finder = _finder(transport, clock)
    # candidates() is lazy: the stage stops pulling once a download succeeds.
    assert next(finder.candidates("Alexander Volkanovski")) == headshot_url("3949584")
    assert next(finder.candidates("Petr Yan")) == headshot_url("4293517")
    # Scoreboard fetched once, search never called.
    assert [u for u, _ in transport.calls] == [ESPN_SCOREBOARD_URL]


def test_finder_falls_back_to_search_then_wikipedia(transport, clock):
    transport.add_json(ESPN_SCOREBOARD_URL, SCOREBOARD)
    transport.add_json(ESPN_SEARCH_URL, SEARCH, query="Islam Makhachev")
    transport.add_json(WIKIPEDIA_API_URL, WIKI_HIT, query="Islam Makhachev")
    finder = _finder(transport, clock)
    urls = list(finder.candidates("Islam Makhachev"))
    assert urls[0] == headshot_url("3332412")
    assert urls[1].startswith("https://upload.wikimedia.org/")


def test_finder_returns_empty_on_total_miss(transport, clock):
    transport.add_json(ESPN_SCOREBOARD_URL, SCOREBOARD)
    transport.add_json(ESPN_SEARCH_URL, {"items": []})
    transport.add_json(WIKIPEDIA_API_URL, WIKI_MISS)
    assert list(_finder(transport, clock).candidates("Nobody Atall")) == []


def test_finder_survives_source_errors(transport, clock):
    transport.add(ESPN_SCOREBOARD_URL, Response(500, b""))
    transport.add(ESPN_SEARCH_URL, Response(200, b"not json"))
    transport.add(WIKIPEDIA_API_URL, OSError("down"))
    assert list(_finder(transport, clock).candidates("Petr Yan")) == []


def test_finder_uses_nickname_for_scoreboard_match(transport, clock):
    board = {"events": [{"competitions": [{"competitors": [
        {"id": "77", "athlete": {"displayName": "Shara Magomedov"}}]}]}]}
    transport.add_json(ESPN_SCOREBOARD_URL, board)
    finder = _finder(transport, clock)
    assert next(finder.candidates("Sharabutdin Magomedov", nickname="Shara Bullet")) == headshot_url("77")


def test_finder_skips_search_when_scoreboard_matched(transport, clock):
    transport.add_json(ESPN_SCOREBOARD_URL, SCOREBOARD)
    transport.add_json(WIKIPEDIA_API_URL, WIKI_MISS)
    assert list(_finder(transport, clock).candidates("Petr Yan")) == [headshot_url("4293517")]
    assert not any(u == ESPN_SEARCH_URL for u, _ in transport.calls)
