import pytest

from fightcard.models import Record, Result
from fightcard.ufcstats.parsers import (
    EventStub,
    extract_id,
    parse_event_detail,
    parse_event_list,
    parse_fighter,
    parse_height,
    parse_record,
    slugify,
)

# --- helpers --------------------------------------------------------------


def test_extract_id_from_detail_urls():
    assert extract_id("http://ufcstats.com/event-details/0a66de3a797cf73a") == "0a66de3a797cf73a"
    assert extract_id("http://ufcstats.com/fighter-details/b6452706b373eea1/") == "b6452706b373eea1"
    assert extract_id("") is None


def test_slugify_event_names():
    assert slugify("UFC Fight Night: Moicano vs. Nolan") == "ufc-fight-night-moicano-vs-nolan"
    assert slugify("UFC 334: Gane vs. Hokit") == "ufc-334-gane-vs-hokit"
    assert slugify("UFC Fight Night: Rosas Jr. vs. Barcelos") == "ufc-fight-night-rosas-jr-vs-barcelos"


@pytest.mark.parametrize(
    "text,expected",
    [
        ("21-7-1", Record(w=21, l=7, d=1)),
        ("Record: 21-7-1 (1 NC)", Record(w=21, l=7, d=1, nc=1)),
        ("10-2-0 (2 NC)", Record(w=10, l=2, d=0, nc=2)),
        ("garbage", Record()),
    ],
)
def test_parse_record(text, expected):
    assert parse_record(text) == expected


@pytest.mark.parametrize(
    "text,expected", [("5' 11\"", 71.0), ("6' 0\"", 72.0), ("--", None), ("", None)]
)
def test_parse_height(text, expected):
    assert parse_height(text) == expected


# --- event lists ----------------------------------------------------------


def test_parse_upcoming_list(fixture_html):
    events = parse_event_list(fixture_html("upcoming.html"))
    assert len(events) == 7
    assert events[0] == EventStub(
        id="0a66de3a797cf73a",
        name="UFC Fight Night: Moicano vs. Nolan",
        date="2026-10-31",
        location="Las Vegas, Nevada, USA",
        url="http://ufcstats.com/event-details/0a66de3a797cf73a",
    )
    assert events[-1].date == "2027-02-06"
    assert [e.date for e in events] == sorted(e.date for e in events)


def test_parse_completed_list_includes_next_row_and_past_events(fixture_html):
    events = parse_event_list(fixture_html("completed.html"))
    # The first row is the next upcoming event (flagged with next.png); the stage filters by date.
    assert events[0].id == "55e94a9b525dcf45"
    assert events[1] == EventStub(
        id="7f98d9d5a10fa25c",
        name="UFC Fight Night: Allen vs. Duncan",
        date="2026-10-10",
        location="Las Vegas, Nevada, USA",
        url="http://ufcstats.com/event-details/7f98d9d5a10fa25c",
    )
    assert len(events) == 11


def test_parse_event_list_without_table_is_empty():
    assert parse_event_list("<html><body>nothing</body></html>") == []


# --- event detail ---------------------------------------------------------


def test_parse_upcoming_event_detail(fixture_html):
    parsed = parse_event_detail(fixture_html("event_detail_upcoming.html"), "0a66de3a797cf73a")
    event = parsed.event
    assert event.id == "0a66de3a797cf73a"
    assert event.name == "UFC Fight Night: Moicano vs. Nolan"
    assert event.slug == "ufc-fight-night-moicano-vs-nolan"
    assert event.date == "2026-10-31"
    assert event.location == "Las Vegas, Nevada, USA"
    assert event.status == "upcoming"
    assert len(event.fights) == 13

    main = event.fights[0]
    assert main.id == "6d7244be5589953c"
    assert (main.red, main.blue) == ("b6452706b373eea1", "6c3b2525436cf5d4")
    assert main.weight_class == "Lightweight"
    assert main.title_fight is False
    assert main.result is None
    assert event.fights[3].weight_class == "Women's Bantamweight"

    assert parsed.fighters["b6452706b373eea1"] == "Renato Moicano"
    assert parsed.fighters["6c3b2525436cf5d4"] == "Tom Nolan"
    assert len(parsed.fighters) == 26


def test_parse_completed_event_detail_has_results(fixture_html):
    parsed = parse_event_detail(fixture_html("event_detail_completed.html"), "7f98d9d5a10fa25c")
    event = parsed.event
    assert event.status == "completed"
    assert event.date == "2026-10-10"
    assert len(event.fights) == 12

    main = event.fights[0]
    assert main.id == "7db1a3dac7e343e7"
    assert main.weight_class == "Middleweight"
    assert main.result == Result(winner=main.red, method="SUB", round=5, time="2:46")
    assert event.fights[1].result == Result(winner=event.fights[1].red, method="U-DEC", round=3, time="5:00")
    assert event.fights[3].result.method == "KO/TKO"


def test_draw_has_no_winner(fixture_html):
    event = parse_event_detail(fixture_html("event_detail_completed.html"), "x").event
    draw = event.fights[-1]
    assert draw.result == Result(winner=None, method="M-DEC", round=3, time="5:00")


ROW = """
<table class="b-fight-details__table"><tbody class="b-fight-details__table-body">
<tr class="b-fight-details__table-row" {data_link}>
 <td class="b-fight-details__table-col">{flag}</td>
 <td class="b-fight-details__table-col">
  <p class="b-fight-details__table-text"><a href="http://ufcstats.com/fighter-details/aaaaaaaaaaaaaaaa">A One</a></p>
  <p class="b-fight-details__table-text"><a href="http://ufcstats.com/fighter-details/bbbbbbbbbbbbbbbb">B Two</a></p>
 </td>
 <td class="b-fight-details__table-col"></td><td class="b-fight-details__table-col"></td>
 <td class="b-fight-details__table-col"></td><td class="b-fight-details__table-col"></td>
 <td class="b-fight-details__table-col"><p class="b-fight-details__table-text">
   Bantamweight <br/>{belt}</p></td>
 <td class="b-fight-details__table-col"><p class="b-fight-details__table-text">{method}</p>
   <p class="b-fight-details__table-text">{detail}</p></td>
 <td class="b-fight-details__table-col"><p class="b-fight-details__table-text">{round}</p></td>
 <td class="b-fight-details__table-col"><p class="b-fight-details__table-text">{time}</p></td>
</tr></tbody></table>
"""


def event_html(**kw):
    values = dict(data_link="", flag="", belt="", method="", detail="", round="", time="")
    values.update(kw)
    head = (
        '<h2 class="b-content__title"><span class="b-content__title-highlight">UFC 999: A vs. B'
        '</span></h2><ul class="b-list__box-list">'
        '<li class="b-list__box-list-item"><i>Date:</i> January 02, 2027</li>'
        '<li class="b-list__box-list-item"><i>Location:</i> </li></ul>'
    )
    return head + ROW.format(**values)


def test_title_fight_flag_from_belt_image():
    html = event_html(belt='<img src="http://x/belt.png"/>')
    fight = parse_event_detail(html, "e1").event.fights[0]
    assert fight.title_fight is True
    assert fight.weight_class == "Bantamweight"


def test_fight_without_data_link_gets_positional_id():
    fight = parse_event_detail(event_html(), "e1").event.fights[0]
    assert fight.id == "e1-1"


def test_empty_location_is_none():
    assert parse_event_detail(event_html(), "e1").event.location is None


def test_no_contest_has_no_winner():
    flag = '<p><a><i class="b-flag__inner"><i class="b-flag__text">nc</i></i></a></p>'
    html = event_html(flag=flag, method="Overturned", round="2", time="1:10")
    assert parse_event_detail(html, "e1").event.fights[0].result == Result(
        winner=None, method="Overturned", round=2, time="1:10"
    )


def test_loss_flag_means_second_fighter_won():
    flag = '<p><a><i class="b-flag__inner"><i class="b-flag__text">loss</i></i></a></p>'
    html = event_html(flag=flag, method="DQ", round="x", time="")
    result = parse_event_detail(html, "e1").event.fights[0].result
    assert result == Result(winner="bbbbbbbbbbbbbbbb", method="DQ", round=None, time=None)


def test_event_detail_without_fights():
    html = event_html().split("<table")[0]
    parsed = parse_event_detail(html, "e1")
    assert parsed.event.fights == []
    assert parsed.event.name == "UFC 999: A vs. B"
    assert parsed.event.date == "2027-01-02"


# --- fighter page ---------------------------------------------------------


def test_parse_fighter_page(fixture_html):
    fighter = parse_fighter(fixture_html("fighter.html"), "b6452706b373eea1")
    assert fighter.id == "b6452706b373eea1"
    assert fighter.name == "Renato Moicano"
    assert fighter.nickname is None
    assert fighter.record == Record(w=21, l=7, d=1, nc=0)
    assert fighter.height_in == 71.0
    assert fighter.reach_in == 72.0
    assert fighter.stance == "Orthodox"
    assert fighter.dob == "1989-05-21"
    assert fighter.image is None
    assert fighter.details_fetched_at is None


def test_parse_fighter_with_nickname_nc_and_missing_values():
    html = """
    <h2 class="b-content__title">
      <span class="b-content__title-highlight"> Jane Doe </span>
      <span class="b-content__title-record"> Record: 5-1-0 (1 NC) </span>
    </h2>
    <p class="b-content__Nickname"> The Hammer </p>
    <div class="b-list__info-box b-list__info-box_style_small-width"><ul class="b-list__box-list">
      <li class="b-list__box-list-item"><i class="b-list__box-item-title">Height:</i> --</li>
      <li class="b-list__box-list-item"><i class="b-list__box-item-title">Reach:</i> --</li>
      <li class="b-list__box-list-item"><i class="b-list__box-item-title">STANCE:</i> </li>
      <li class="b-list__box-list-item"><i class="b-list__box-item-title">DOB:</i> --</li>
    </ul></div>
    """
    fighter = parse_fighter(html, "f1")
    assert fighter.name == "Jane Doe"
    assert fighter.nickname == "The Hammer"
    assert fighter.record == Record(w=5, l=1, d=0, nc=1)
    assert (fighter.height_in, fighter.reach_in, fighter.stance, fighter.dob) == (None, None, None, None)
