import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest

from scrooge.storage import db

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    connection = db.connect(":memory:")
    db.migrate(connection, NOW)
    yield connection
    connection.close()
