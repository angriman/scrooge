"""`scrooge backup` as a service: the copy, its name and what is kept."""

from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from scrooge import backups
from scrooge.storage import db

NOW = datetime(2026, 10, 6, 3, 0, tzinfo=UTC)


@pytest.fixture
def database(tmp_path: Path) -> Path:
    path = tmp_path / "scrooge.db"
    db.create(path, NOW)
    conn = db.connect(path)
    conn.execute("INSERT INTO owners (name) VALUES ('Lukas')")
    conn.close()
    return path


def at(day: date, hour: int = 3) -> datetime:
    return datetime(day.year, day.month, day.day, hour, tzinfo=UTC)


def test_a_backup_is_a_whole_copy_named_by_its_moment_in_utc(database: Path) -> None:
    assert backups.found(database) == {}
    made = backups.take(database, NOW.astimezone(timezone(timedelta(hours=2))))
    assert made.path == database.parent / "backups" / "scrooge-backup-20261006T030000Z.db"
    assert (made.removed, made.damage) == ((), ())
    with db.reading(made.path) as copy:
        assert copy.execute("SELECT name FROM owners").fetchall()[0][0] == "Lukas"
        assert copy.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    assert [path.name for path in made.path.parent.iterdir()] == [made.path.name]
    assert backups.found(database) == {NOW: made.path}


def test_two_backups_in_one_second_are_refused(database: Path) -> None:
    backups.take(database, NOW)
    with pytest.raises(backups.BackupError, match="a backup of that second already"):
        backups.take(database, NOW)


def test_a_backup_that_fails_leaves_no_file(database: Path) -> None:
    database.write_text("not a database, only text long enough to look like a header" * 20)
    with pytest.raises(backups.BackupError, match=r"cannot be read \(file is not a database\)"):
        backups.take(database, NOW)
    assert list((database.parent / "backups").iterdir()) == []


def test_only_its_own_files_are_ever_removed(database: Path) -> None:
    folder = database.parent / "backups"
    folder.mkdir()
    others = [
        "scrooge-20250101T000000.db",  # a copy a commit kept
        "scrooge-backup-20250101T000000Z.db.part",
        "scrooge-backup-20251399T000000Z.db",  # no real moment
        "other-backup-20250101T000000Z.db",  # of another database
        "notes.txt",
    ]
    for other in others:
        (folder / other).write_text("x")
    (folder / "scrooge-backup-20250102T000000Z.db").mkdir()  # a folder, not a backup
    first = at(date(2024, 1, 1))
    for day in range(60):
        backups.take(database, first + timedelta(days=day * 7))  # a year and more, weekly
    remaining = {path.name for path in folder.iterdir()}
    assert set(others) <= remaining
    assert "scrooge-backup-20250102T000000Z.db" in remaining
    assert len(backups.found(database)) == len(backups.keep(backups.found(database)))


def test_a_damaged_copy_is_kept_and_nothing_is_pruned(database: Path) -> None:
    folder = database.parent / "backups"
    folder.mkdir()
    older = [folder / backups.name(database, NOW - timedelta(days=day)) for day in range(1, 40)]
    for path in older:
        path.write_text("x")
    conn = db.connect(database)
    conn.execute("PRAGMA foreign_keys = OFF")
    orphan = "INSERT INTO accounts (owner_id, name, kind) VALUES (9, 'X', 'broker_securities')"
    conn.execute(orphan)
    conn.close()
    made = backups.take(database, NOW)
    assert made.damage == ("a row of accounts refers to a missing row of owners",)
    assert made.removed == ()
    assert all(path.exists() for path in [*older, made.path])
    database.unlink()  # once put right, the next one prunes
    db.create(database, NOW)
    made = backups.take(database, NOW + timedelta(days=1))
    assert made.damage == ()
    assert len(made.removed) == len(older) + 2 - len(backups.found(database))  # with both new


def test_the_retention_keeps_dailies_weeklies_and_monthlies() -> None:
    # Twice a day for 400 days, up to Wednesday 2026-10-07.
    last = date(2026, 10, 7)
    moments = [at(last - timedelta(days=day), hour) for day in range(400) for hour in (3, 15)]
    kept = sorted(backups.keep(moments))
    days = [at(last - timedelta(days=day), 15) for day in range(7)]  # the newest of each day
    sundays = [at(date(2026, 10, 4) - timedelta(weeks=week), 15) for week in range(7)]
    month_ends = [at(date(2026, 9, 30), 15)]
    for month in range(8, 0, -1):
        month_ends.append(at(date(2026, month + 1, 1) - timedelta(days=1), 15))
    month_ends += [at(date(2025, 12, 31), 15), at(date(2025, 11, 30), 15)]
    assert kept == sorted({*days, *sundays, *month_ends})


def test_days_without_a_backup_are_not_counted() -> None:
    # Ten backups, a month apart: no day, week or month among them is empty of others.
    moments = [at(date(2025, month, 1)) for month in range(1, 11)]
    assert backups.keep(moments) == set(moments)
    moments = [at(date(2024 + year // 12, year % 12 + 1, 1)) for year in range(15)]
    assert backups.keep(moments) == set(moments[-12:])


moments_strategy = st.lists(
    st.datetimes(
        min_value=datetime(2020, 1, 1),  # noqa: DTZ001  # hypothesis bounds are naive; timezones=UTC
        max_value=datetime(2030, 1, 1),  # noqa: DTZ001  # as above
        timezones=st.just(UTC),
    ),
    max_size=200,
)


@given(moments_strategy)
def test_the_retention_is_bounded_keeps_the_newest_and_changes_nothing_run_again(
    moments: list[datetime],
) -> None:
    kept = backups.keep(moments)
    assert kept <= set(moments)
    assert len(kept) <= backups.DAILY + backups.WEEKLY + backups.MONTHLY
    if moments:
        assert max(moments) in kept
    assert backups.keep(kept) == kept
    assert backups.keep(reversed(moments)) == kept  # the order they are listed in is no matter
