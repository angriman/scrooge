import re
from dataclasses import replace
from datetime import date

from starlette.testclient import TestClient

from scrooge.classification import load_instruments
from tests.conftest import INSTRUMENTS_YAML
from tests.fakes import FakeMaturities
from tests.web.builders import CLICKED, MATURED, Site
from tests.web.test_app import text
from tests.web.test_import_page import result

BILL = "912797ZZ1"  # the bill the Tastytrade fixture buys
MATURES = date(2026, 12, 24)
LOOK_UP = f"Look up {BILL} on TreasuryDirect and import again"


def undated(site: Site, source: FakeMaturities | None) -> TestClient:
    """The app of `site`, with an instruments file that does not date the bill bought."""
    site.setup.instruments.write_text(
        INSTRUMENTS_YAML.replace(f"  {BILL}: {{maturity: 2026-12-24}}\n", "") + MATURED,
        encoding="utf-8",
    )
    site.client = site.app(imports=site.setup, maturities=source)
    return site.client


def held(page: str) -> str:
    found = re.search(r'name="held" value="([^"]+)"', page)
    assert found, "no button to look the bills up"
    return found.group(1)


def look_up(client: TestClient, key: str, token: str) -> str:
    return client.post("/import/maturities", data={"token": token, "held": key}).text


def test_an_import_stopped_on_a_bill_looks_it_up_and_imports_the_same_files(site: Site) -> None:
    source = FakeMaturities({BILL: MATURES})
    client = undated(site, source)
    stopped = site.send(site.uploads("tastytrade.csv"))
    assert stopped.status_code == 409
    assert f"Nothing was imported: {BILL}: no maturity date" in text(stopped.text)
    assert LOOK_UP in text(stopped.text)
    assert source.asked == []  # nothing is asked before the button is pressed
    assert site.count("statement_files") == 0

    response = client.post(
        "/import/maturities", data={"token": site.token, "held": held(stopped.text)}
    )
    assert response.status_code == 200
    assert source.asked == [BILL]
    assert f"{BILL} matures on 2026-12-24 (TreasuryDirect), written in instruments.yaml." in text(
        response.text
    )
    assert result(response.text) == [["tastytrade.csv", "Tastytrade transactions", "Staged."]]
    assert site.count("statement_files") == 1
    assert load_instruments(site.setup.instruments)[BILL].maturity == MATURES
    # The files are kept for one import only.
    again = look_up(client, held(stopped.text), site.token)
    assert "The files of that import are no longer kept: choose them again." in text(again)


def test_a_bill_the_source_does_not_know_is_left_to_type(site: Site) -> None:
    client = undated(site, FakeMaturities())
    key = held(site.send(site.uploads("tastytrade.csv")).text)
    response = client.post("/import/maturities", data={"token": site.token, "held": key})
    assert response.status_code == 409
    assert f"TreasuryDirect knows no bill {BILL}: add its maturity to instruments.yaml." in text(
        response.text
    )
    assert held(response.text) == key  # still kept, for another try
    assert BILL not in load_instruments(site.setup.instruments)
    assert site.count("statement_files") == 0


def test_a_source_that_cannot_be_reached_keeps_the_files_for_another_try(site: Site) -> None:
    source = FakeMaturities({BILL: MATURES}, down=True)
    client = undated(site, source)
    key = held(site.send(site.uploads("tastytrade.csv")).text)
    response = client.post("/import/maturities", data={"token": site.token, "held": key})
    assert response.status_code == 502
    assert "TreasuryDirect could not tell: no answer from the price source" in text(response.text)
    source.down = False
    assert "Staged." in look_up(client, key, site.token)


def test_a_maturity_the_file_cannot_take_is_said_and_nothing_is_imported(site: Site) -> None:
    client = undated(site, FakeMaturities({BILL: MATURES}))
    instruments = site.setup.instruments
    instruments.write_text(instruments.read_text(encoding="utf-8") + f"  {BILL}: {{tab: Bonds}}\n")
    key = held(site.send(site.uploads("tastytrade.csv")).text)
    response = client.post("/import/maturities", data={"token": site.token, "held": key})
    assert response.status_code == 409
    assert f"{BILL} listed without a maturity; add it there." in text(response.text)
    assert site.count("statement_files") == 0


def test_files_kept_too_long_or_never_kept_are_asked_again(site: Site) -> None:
    client = undated(site, FakeMaturities({BILL: MATURES}))
    first = held(site.send(site.uploads("tastytrade.csv")).text)
    kept = client.app.state.held  # type: ignore[attr-defined]  # Starlette's app behind the client
    kept[first] = replace(kept[first], until=CLICKED)  # the app's clock stands at CLICKED
    asked = look_up(client, first, site.token)
    assert "no longer kept: choose them again" in text(asked)
    assert "no longer kept" in text(look_up(client, "never-kept", site.token))
    # Another import that stops drops what has waited too long.
    second = held(site.send(site.uploads("tastytrade.csv")).text)
    assert first not in kept
    assert second in kept


def test_the_button_needs_the_pages_token_and_a_source(site: Site) -> None:
    client = undated(site, FakeMaturities({BILL: MATURES}))
    key = held(site.send(site.uploads("tastytrade.csv")).text)
    stale = client.post("/import/maturities", data={"token": "of-an-earlier-start", "held": key})
    assert stale.status_code == 403
    assert site.count("statement_files") == 0

    plain = undated(site, None)
    stopped = site.send(site.uploads("tastytrade.csv"))
    assert stopped.status_code == 409
    assert f"{BILL}: no maturity date; add" in text(stopped.text)
    assert LOOK_UP not in text(stopped.text)
    assert (
        plain.post("/import/maturities", data={"token": site.token, "held": key}).status_code == 404
    )


def test_a_copy_looks_nothing_up(site: Site) -> None:
    copy = site.app(imports=site.setup, maturities=FakeMaturities(), read_only=True)
    assert (
        copy.post("/import/maturities", data={"token": site.token, "held": "x"}).status_code == 403
    )


def test_an_import_stopped_for_another_reason_after_the_lookup_says_both(site: Site) -> None:
    client = undated(site, FakeMaturities({BILL: MATURES}))
    key = held(site.send(site.uploads("tastytrade.csv")).text)
    (site.folder / "categories.yaml").write_text("categories: 5", encoding="utf-8")
    response = client.post("/import/maturities", data={"token": site.token, "held": key})
    assert response.status_code == 409
    assert f"{BILL} matures on 2026-12-24" in text(response.text)
    assert "Nothing was imported: " in text(response.text)
