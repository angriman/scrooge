"""What is accepted as a logo, and which holdings lack one."""

import sqlite3
from datetime import UTC, datetime

import pytest

from scrooge.storage import logos, store

NOW = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"/>'
PNG = b"\x89PNG\r\n\x1a\n" + b"data"


def test_a_png_is_known_by_its_signature_and_an_svg_by_its_element() -> None:
    assert logos.read(PNG).media_type == "image/png"
    svg = logos.read(SVG)
    assert svg.media_type == "image/svg+xml"
    assert len(svg.digest) == 64


@pytest.mark.parametrize(
    ("image", "why"),
    [
        (b"", "empty"),
        (b"x" * (logos.LIMIT + 1), "larger than 256 KiB"),
        (b"GIF89a", "neither a PNG nor an SVG"),
        (b"\xff\xfe<svg", "neither a PNG nor an SVG"),
    ],
)
def test_what_is_no_logo_is_refused_and_why(image: bytes, why: str) -> None:
    with pytest.raises(logos.LogoError, match=why):
        logos.read(image)


def test_a_logo_is_added_for_an_instrument_and_removed(conn: sqlite3.Connection) -> None:
    store.ensure_currency(conn, "USD")
    store.instrument_id(conn, "WIDG", "ETFs", "USD")
    logo = logos.add(conn, "WIDG", SVG, "widg.svg", NOW)
    assert store.logo(conn, "WIDG") == logo
    with pytest.raises(logos.LogoError, match="no instrument"):
        logos.add(conn, "GADG", SVG, "", NOW)
    logos.remove(conn, "WIDG")
    with pytest.raises(logos.LogoError, match="no logo to remove"):
        logos.remove(conn, "WIDG")


def test_the_holdings_without_a_logo_are_listed_once_each(conn: sqlite3.Connection) -> None:
    store.ensure_currency(conn, "USD")
    store.instrument_id(conn, "WIDG", "ETFs", "USD")
    logos.add(conn, "WIDG", SVG, "", NOW)
    assert logos.missing(conn, ["GADG", "WIDG", "GADG", "THNG"]) == ["GADG", "THNG"]
