"""The price source, against recorded answers: no test ever connects (D9)."""

import json
import urllib.error
import urllib.request
from datetime import UTC, date, datetime
from decimal import Decimal
from email.message import Message
from io import BytesIO
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from finanze import prices
from finanze.prices import History, PriceError, Split, Yahoo, significant

FIXTURES = Path(__file__).parent / "fixtures" / "yahoo"
CHART = (FIXTURES / "chart.json").read_bytes()
RATE = (FIXTURES / "rate.json").read_bytes()
SEARCH = (FIXTURES / "search.json").read_bytes()
TODAY = date(2025, 10, 1)
D = Decimal


class Recorded:
    """Stands for the network: hands out one answer and keeps the addresses asked for."""

    def __init__(self, answer: bytes) -> None:
        self.answer = answer
        self.asked: list[str] = []

    def __call__(self, url: str) -> bytes:
        self.asked.append(url)
        return self.answer


def yahoo(answer: bytes) -> tuple[Yahoo, Recorded]:
    recorded = Recorded(answer)
    return Yahoo(recorded, lambda: TODAY), recorded


def test_closes_are_the_prices_of_their_day_before_and_after_a_split() -> None:
    source, _ = yahoo(CHART)
    history = source.history("WIDG", date(2025, 9, 1), date(2025, 9, 30))
    # The answer adjusts the two closes before the 4-for-1 split of 10 September to a quarter;
    # the day without a close is absent.
    assert history == History(
        "USD",
        (
            (date(2025, 9, 8), D("200.51")),
            (date(2025, 9, 9), D("202.16")),
            (date(2025, 9, 10), D("51.34")),
            (date(2025, 9, 12), D("52.6")),
        ),
        (Split(date(2025, 9, 10), 4, 1),),
    )


def test_history_is_asked_up_to_today_and_cut_to_the_days_wanted() -> None:
    source, recorded = yahoo(CHART)
    history = source.history("WIDG", date(2025, 9, 8), date(2025, 9, 9))
    # The split is after the last day wanted: only an answer reaching today lists it.
    assert [day for day, _ in history.closes] == [date(2025, 9, 8), date(2025, 9, 9)]
    assert history.closes[0][1] == D("200.51")
    assert history.splits == (Split(date(2025, 9, 10), 4, 1),)
    [url] = recorded.asked
    asked = parse_qs(urlsplit(url).query)
    assert urlsplit(url).path == "/v8/finance/chart/WIDG"
    assert asked == {
        "period1": ["1757289600"],  # 8 September 2025, 00:00 UTC
        "period2": ["1759363200"],  # the end of today, 1 October
        "interval": ["1d"],
        "events": ["split"],
    }


def test_a_day_after_today_is_asked_for_as_it_is() -> None:
    source, recorded = yahoo(CHART)
    source.history("WIDG", date(2025, 9, 8), date(2025, 10, 5))
    assert parse_qs(urlsplit(recorded.asked[0]).query)["period2"] == ["1759708800"]


def test_a_rate_is_dated_by_the_day_of_its_exchange() -> None:
    source, recorded = yahoo(RATE)
    history = source.history(prices.rate_symbol("EUR", "CHF"), date(2025, 9, 8), date(2025, 9, 30))
    # Yahoo names a session by its opening: 23:00 UTC of the evening before, in London.
    assert history == History(
        "CHF",
        (
            (date(2025, 9, 8), D("0.94008")),
            (date(2025, 9, 9), D("0.94198")),
            (date(2025, 9, 10), D("0.9385")),
        ),
    )
    assert "/chart/EURCHF%3DX?" in recorded.asked[0]


def test_a_session_is_dated_in_its_exchange_s_time_of_that_day_not_of_today() -> None:
    # London opens at midnight: 00:00 UTC in winter, 23:00 UTC of the evening before in summer.
    # Asked in winter, the answer's own offset is zero, which would date summer a day early.
    winter = int(datetime(2025, 1, 15, 0, tzinfo=UTC).timestamp())
    summer = int(datetime(2025, 7, 14, 23, tzinfo=UTC).timestamp())
    meta = '{"currency":"CHF","gmtoffset":0,"exchangeTimezoneName":"%s"}'
    answer = (
        '{"chart":{"result":[{"meta":%s,"timestamp":[%d,%d],'
        '"indicators":{"quote":[{"close":[0.94,0.93]}]}}],"error":null}}'
    )
    london = (answer % (meta % "Europe/London", winter, summer)).encode()
    history = yahoo(london)[0].history("EURCHF=X", date(2025, 1, 1), date(2025, 9, 30))
    assert [day for day, _ in history.closes] == [date(2025, 1, 15), date(2025, 7, 15)]
    nowhere = (answer % (meta % "Mars/Olympus", winter, summer)).encode()
    with pytest.raises(PriceError, match="not in the expected form"):
        yahoo(nowhere)[0].history("EURCHF=X", date(2025, 1, 1), date(2025, 9, 30))


def test_a_range_that_ends_before_it_starts_is_refused() -> None:
    source, recorded = yahoo(CHART)
    with pytest.raises(PriceError, match="2025-09-01 is before 2025-09-02"):
        source.history("WIDG", date(2025, 9, 2), date(2025, 9, 1))
    assert recorded.asked == []


def test_an_unknown_symbol_is_an_error_in_the_source_s_words() -> None:
    answer = b'{"chart":{"result":null,"error":{"code":"Not Found","description":"No data found"}}}'
    with pytest.raises(PriceError, match="NOPE: No data found"):
        yahoo(answer)[0].history("NOPE", date(2025, 9, 1), date(2025, 9, 2))


@pytest.mark.parametrize(
    "answer",
    [
        b"<html>try again later</html>",
        b"{}",
        b'{"chart":{"result":[],"error":null}}',
        b'{"chart":{"result":[{"meta":{"currency":"USD","gmtoffset":0},"timestamp":[1,2],'
        b'"indicators":{"quote":[{"close":[1.5]}]}}],"error":null}}',  # a close is missing
        b'{"chart":{"result":[{"meta":{"currency":"USD","gmtoffset":0},"timestamp":["x"],'
        b'"indicators":{"quote":[{"close":[1.5]}]}}],"error":null}}',
        b'{"chart":{"result":[{"meta":{"currency":"USD","gmtoffset":0},"timestamp":[1],'
        b'"indicators":{"quote":[{"close":["x"]}]}}],"error":null}}',
    ],
)
def test_an_answer_in_another_form_is_an_error(answer: bytes) -> None:
    with pytest.raises(PriceError, match="WIDG: the price source"):
        yahoo(answer)[0].history("WIDG", date(2025, 9, 1), date(2025, 9, 2))


def test_an_answer_without_any_day_is_an_empty_history() -> None:
    answer = (
        b'{"chart":{"result":[{"meta":{"currency":"USD","gmtoffset":0},'
        b'"indicators":{"quote":[{}]}}],"error":null}}'
    )
    history = yahoo(answer)[0].history("WIDG", date(2025, 9, 1), date(2025, 9, 2))
    assert history == History("USD", ())


@pytest.mark.parametrize(("numerator", "denominator"), [("1.5", "1"), ("0", "1"), ("3", "-1")])
def test_a_split_that_is_not_in_whole_shares_is_refused(numerator: str, denominator: str) -> None:
    chart = json.loads(CHART)
    split = chart["chart"]["result"][0]["events"]["splits"]["1757511000"]
    split["numerator"], split["denominator"] = float(numerator), float(denominator)
    with pytest.raises(PriceError, match="WIDG: cannot store a split"):
        yahoo(json.dumps(chart).encode())[0].history("WIDG", date(2025, 9, 1), date(2025, 9, 30))


def test_a_reverse_split_is_undone_too() -> None:
    chart = json.loads(CHART)
    split = chart["chart"]["result"][0]["events"]["splits"]["1757511000"]
    split["numerator"], split["denominator"] = 1.0, 10.0
    history = yahoo(json.dumps(chart).encode())[0].history(
        "WIDG", date(2025, 9, 8), date(2025, 9, 8)
    )
    assert history.closes == ((date(2025, 9, 8), D("5.01275")),)
    assert history.splits == (Split(date(2025, 9, 10), 1, 10),)


def test_an_isin_is_found_under_the_first_symbol_listed() -> None:
    source, recorded = yahoo(SEARCH)
    assert source.find("CH0000000001") == "0P0000WIDG.SW"
    assert parse_qs(urlsplit(recorded.asked[0]).query)["q"] == ["CH0000000001"]
    assert urlsplit(recorded.asked[0]).path == "/v1/finance/search"


def test_an_isin_the_source_does_not_know_is_not_found() -> None:
    assert yahoo(b'{"quotes":[]}')[0].find("CH0000000001") is None


@pytest.mark.parametrize("answer", [b"[]", b'{"quotes":[{}]}', b"not json"])
def test_a_search_answer_in_another_form_is_an_error(answer: bytes) -> None:
    with pytest.raises(PriceError, match="CH0000000001: the price source"):
        yahoo(answer)[0].find("CH0000000001")


def test_closes_keep_the_digits_a_single_precision_float_can_be_trusted_for() -> None:
    assert str(significant(D("1396.1800537109375"))) == "1396.18"
    assert str(significant(D("0.9400799870491028"))) == "0.94008"
    assert str(significant(D("0.0000123456789"))) == "0.00001234568"
    assert str(significant(D("123456789.5"))) == "123456800"
    assert str(significant(D(200))) == "200"
    assert significant(D(0)) == 0


# ---------------------------------------------------------------- the connection itself


class Answer(BytesIO):
    def __enter__(self) -> "Answer":
        return self


def test_the_default_fetch_asks_over_https_and_reads_the_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked: list[tuple[str, float]] = []

    def urlopen(request: urllib.request.Request, timeout: float) -> Answer:
        assert request.get_header("User-agent")
        asked.append((request.full_url, timeout))
        return Answer(CHART)

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    history = Yahoo(today=lambda: TODAY).history("WIDG", date(2025, 9, 8), date(2025, 9, 8))
    assert history.closes == ((date(2025, 9, 8), D("200.51")),)
    [(url, timeout)] = asked
    assert url.startswith("https://query1.finance.yahoo.com/v8/finance/chart/WIDG?")
    assert timeout > 0


def test_a_refusal_is_read_like_an_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    body = b'{"chart":{"result":null,"error":{"code":"Not Found","description":"No data found"}}}'

    def urlopen(request: urllib.request.Request, timeout: float) -> Answer:
        raise urllib.error.HTTPError(request.full_url, 404, "Not Found", Message(), BytesIO(body))

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    with pytest.raises(PriceError, match="NOPE: No data found"):
        Yahoo().history("NOPE", date(2025, 9, 1), date(2025, 9, 2))


def test_no_connection_is_an_error_of_its_own(monkeypatch: pytest.MonkeyPatch) -> None:
    def urlopen(request: urllib.request.Request, timeout: float) -> Answer:
        raise urllib.error.URLError("no route to host")

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    with pytest.raises(PriceError, match="no answer from the price source"):
        Yahoo().find("CH0000000001")
