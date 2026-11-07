"""The logos kept in the database, one per symbol."""

import hashlib
import sqlite3

import pytest

from finanze.storage import store

SVG = b'<svg xmlns="http://www.w3.org/2000/svg"/>'
AT = "2026-11-07T12:00:00+00:00"


def _logo(image: bytes, media_type: str = "image/svg+xml") -> store.Logo:
    return store.Logo(media_type, image, hashlib.sha256(image).hexdigest())


def test_a_logo_is_kept_replaced_and_removed(conn: sqlite3.Connection) -> None:
    assert store.logo(conn, "WIDG") is None
    store.set_logo(conn, "WIDG", _logo(SVG), "widg.svg", AT)
    assert store.logo(conn, "WIDG") == _logo(SVG)
    newer = _logo(b"\x89PNG\r\n\x1a\nrest", "image/png")
    store.set_logo(conn, "WIDG", newer, "widg.png", AT)
    assert store.logo(conn, "WIDG") == newer
    assert store.logo_digests(conn) == {"WIDG": newer.digest}
    assert store.remove_logo(conn, "WIDG")
    assert not store.remove_logo(conn, "WIDG")
    assert store.logo_digests(conn) == {}


@pytest.mark.parametrize(
    ("media_type", "image"),
    [("image/gif", SVG), ("image/svg+xml", b""), ("image/svg+xml", b"x" * 262145)],
)
def test_only_an_svg_or_png_of_a_sensible_size_is_kept(
    conn: sqlite3.Connection, media_type: str, image: bytes
) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        store.set_logo(conn, "WIDG", _logo(image, media_type), "", AT)
