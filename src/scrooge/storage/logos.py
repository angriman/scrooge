"""The logo of an instrument: what is accepted, and which holdings still have none.

The images are the user's own, added by hand (`scrooge logo`, the holdings page): nothing here
or in the app fetches one. An SVG may hold a script, so it is only ever served sandboxed.
"""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Iterable
from datetime import datetime

from scrooge.storage import db, store

LIMIT = 262144  # bytes: more is no logo (the same bound the table checks)
_PNG = b"\x89PNG\r\n\x1a\n"


class LogoError(ValueError):
    """An image that is no logo, or a symbol that has no instrument."""


def read(image: bytes) -> store.Logo:
    """`image` as a logo: a PNG by its signature, an SVG as UTF-8 text with an `<svg` element."""
    if not image:
        msg = "the file is empty"
        raise LogoError(msg)
    if len(image) > LIMIT:
        msg = f"the file is larger than {LIMIT // 1024} KiB"
        raise LogoError(msg)
    if image.startswith(_PNG):
        media_type = "image/png"
    else:
        try:
            text = image.decode("utf-8")
        except UnicodeDecodeError:
            text = ""
        if "<svg" not in text:
            msg = "the file is neither a PNG nor an SVG"
            raise LogoError(msg)
        media_type = "image/svg+xml"
    return store.Logo(media_type, image, hashlib.sha256(image).hexdigest())


def add(
    conn: sqlite3.Connection, symbol: str, image: bytes, source: str, now: datetime
) -> store.Logo:
    """Keep `image` as the logo of the instrument `symbol`, in place of any it had."""
    logo = read(image)
    if symbol not in {instrument.symbol for instrument in store.instruments(conn)}:
        msg = f"{symbol}: no instrument has this symbol"
        raise LogoError(msg)
    store.set_logo(conn, symbol, logo, source, db.timestamp(now))
    return logo


def remove(conn: sqlite3.Connection, symbol: str) -> None:
    if not store.remove_logo(conn, symbol):
        msg = f"{symbol}: no logo to remove"
        raise LogoError(msg)


def missing(conn: sqlite3.Connection, held: Iterable[str]) -> list[str]:
    """Of the symbols `held`, once each in the order met, those without a logo."""
    have = store.logo_digests(conn)
    return [symbol for symbol in dict.fromkeys(held) if symbol not in have]
