"""Accounts as their institutions keep them: a broker's currencies are one account there, not
one each.

A statement that keeps cash in several currencies is read into one account per currency, named
by the account and the currency's code: IBKR's become `IBKR CHF`, `IBKR EUR` and `IBKR USD`
(`IBKR 2 USD` for a second account, which `account_labels` names), the pockets of one Revolut
export `Revolut EUR` and so on. Transactions, income and rules keep them apart, as they are
booked. The accounts page lists such accounts as one, and how far the statements go is judged
of that one, wherever it is (the day a person's statements are all in, the accounts behind,
the month the first page recaps): those of one person and one source whose names are one stem
and a currency's code (three capitals) and that the same statement files cover, file to file,
are one, under the stem. Two accounts of one bank with one statement each (`Migros EUR`,
`Migros USD`, an IBAN each) share no file, so they stay apart; an account alone keeps its name,
and so do accounts whose stem is the name of another account of the person.

The one account holds what its parts hold together, and its statements are theirs: a day is
covered when a statement of any of them covers it, since one statement covers them all, so a
currency the broker stops listing does not leave it behind.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Mapping, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from scrooge import holds, months

_CODE = re.compile(r"(.+) [A-Z]{3}")  # an account named by its stem and a currency's code
_CENT = Decimal("0.01")


@dataclass(frozen=True, slots=True)
class Row:
    """One account of the accounts page: one the books keep, or a broker's currencies together."""

    line: months.Coverage  # how far it is committed and staged, named as the page shows it
    parts: tuple[str, ...]  # the accounts the books keep it as, by name; itself alone when one
    spans: tuple[months.Span, ...]  # the runs of days its statements cover, staged ones first
    statements: tuple[months.Statement, ...]  # newest first, each once
    held: holds.Holds | None  # what it holds now; None when its balance is not known


def lines(conn: sqlite3.Connection, owner: str | None = None) -> tuple[months.Coverage, ...]:
    """`months.coverage` with a broker's currencies as one account: what every page that says
    how far the statements go, or which account is behind, judges."""
    found = months.coverage(conn, owner)
    return tuple(line for line, _ in grouped(found, months.statement_ids(conn, owner)))


def rows(
    conn: sqlite3.Connection,
    owner: str | None = None,
    held: Mapping[tuple[str, str], holds.Holds] | None = None,
) -> tuple[Row, ...]:
    """The accounts of `owner`, or of everyone, as the accounts page lists them, with what they
    hold by `held` (`holds.of`; nothing known without it)."""
    tracks = {(t.owner, t.account, t.source): t.spans for t in months.timeline(conn, owner)}
    return arranged(
        months.coverage(conn, owner), tracks, months.by_account(conn, owner), held or {}
    )


def arranged(
    lines: Sequence[months.Coverage],
    tracks: Mapping[tuple[str, str, str], Sequence[months.Span]],
    files: Mapping[tuple[str, str], Sequence[months.Statement]],
    held: Mapping[tuple[str, str], holds.Holds],
) -> tuple[Row, ...]:
    """The rows of `lines`, by person and name: `tracks` are their runs of days by person,
    account and source (`months.timeline`), `files` their statements by person and account
    (`months.by_account`) and `held` what they hold (`holds.of`)."""
    ids = {
        (line.owner, line.account, line.source): {
            s.id for s in files.get((line.owner, line.account), ()) if s.source == line.source
        }
        for line in lines
    }
    return tuple(_row(line, parts, tracks, files, held) for line, parts in grouped(lines, ids))


def grouped(
    lines: Sequence[months.Coverage], ids: Mapping[tuple[str, str, str], AbstractSet[int]]
) -> list[tuple[months.Coverage, tuple[months.Coverage, ...]]]:
    """Each account of `lines` as it is listed, and the accounts it is together, by person and
    name; `ids` are the statement files of each by person, account and source
    (`months.statement_ids`). A stem that is itself the name of an account of the person is
    not one: such accounts stay apart, so that no two are listed under one name."""
    names = {(line.owner, line.account) for line in lines}
    # By stem (None: not named by a currency), the accounts together and their statements.
    groups: list[tuple[str | None, list[months.Coverage], set[int]]] = []
    for line in lines:
        coded = _CODE.fullmatch(line.account)
        stem = coded[1] if coded and (line.owner, coded[1]) not in names else None
        covered = set(ids.get((line.owner, line.account, line.source), ()))
        # The groups it shares a statement with become one, with it.
        meets = [
            group
            for group in groups
            if stem is not None
            and group[0] == stem
            and group[1][0].owner == line.owner
            and group[1][0].source == line.source
            and group[2] & covered
        ]
        gone = {id(met) for met in meets}
        groups = [group for group in groups if id(group) not in gone]
        parts = [line, *(part for met in meets for part in met[1])]
        groups.append((stem, parts, covered.union(*(met[2] for met in meets))))
    found = [
        (_together(stem, parts), tuple(sorted(parts, key=lambda part: part.account)))
        for stem, parts, _ in groups
    ]
    return sorted(found, key=lambda each: (each[0].owner, each[0].account, each[0].source))


def _together(stem: str | None, parts: Sequence[months.Coverage]) -> months.Coverage:
    """The line of accounts together: committed as far as the furthest, staged beyond that,
    closed when every one of them is, on the last of their days."""
    first = parts[0]
    if stem is None or len(parts) == 1:
        return first
    committed = max((part.committed for part in parts if part.committed), default=None)
    staged = max((part.staged for part in parts if part.staged), default=None)
    ahead = staged if staged and (not committed or staged > committed) else None
    days = [part.closed for part in parts if part.closed]
    closed = max(days) if len(days) == len(parts) else None
    return months.Coverage(first.owner, stem, first.source, committed, ahead, closed)


def _row(
    line: months.Coverage,
    parts: Sequence[months.Coverage],
    tracks: Mapping[tuple[str, str, str], Sequence[months.Span]],
    files: Mapping[tuple[str, str], Sequence[months.Statement]],
    held: Mapping[tuple[str, str], holds.Holds],
) -> Row:
    names = tuple(part.account for part in parts)
    spans = [span for name in names for span in tracks.get((line.owner, name, line.source), ())]
    statements = {
        s.id: s
        for name in names
        for s in files.get((line.owner, name), ())
        if s.source == line.source
    }
    return Row(
        line,
        names,
        tuple(
            span
            for committed in (False, True)
            for span in months.joined(
                [(span.first, span.last) for span in spans if span.committed == committed],
                committed,
            )
        ),
        tuple(sorted(statements.values(), key=lambda s: (s.last, s.first, s.id), reverse=True)),
        combined([held.get((line.owner, name)) for name in names]),
    )


def combined(found: Sequence[holds.Holds | None]) -> holds.Holds | None:
    """What accounts hold together: their balances added up, their cash by kind and currency,
    their securities, and their month-ends day by day (one that held nothing yet on a day adds
    nothing to it). None unless every one of them is known: part of it is not all of it."""
    parts = [part for part in found if part is not None]
    if not parts or len(parts) < len(found):
        return None
    if len(parts) == 1:
        return parts[0]
    cash: dict[tuple[str, str], tuple[Decimal, Decimal]] = {}
    for each in (c for part in parts for c in part.cash):
        amount, converted = cash.get((each.asset, each.currency), (Decimal(0), Decimal(0)))
        cash[each.asset, each.currency] = amount + each.amount, converted + each.converted
    securities = [part.securities for part in parts if part.securities is not None]
    ends: dict[date, Decimal] = {}
    for end in (m for part in parts for m in part.months):
        ends[end.day] = ends.get(end.day, Decimal(0)) + end.balance
    exact = sum((part.balance if part.exact is None else part.exact for part in parts), Decimal(0))
    return holds.Holds(
        exact.quantize(_CENT),
        tuple(
            sorted(
                (holds.Cash(asset, code, *cash[asset, code]) for asset, code in cash),
                key=lambda each: (-each.converted, each.currency, each.asset),
            )
        ),
        sum(securities, Decimal(0)) if securities else None,
        tuple(holds.MonthEnd(day, ends[day]) for day in sorted(ends)),
        exact=exact,
    )


def total(rows: Sequence[Row]) -> Decimal | None:
    """What the accounts of `rows` whose balance is known hold together, rounded once to the cent;
    None when none is known."""
    known = [row.held for row in rows if row.held is not None]
    if not known:
        return None
    return sum(
        (held.balance if held.exact is None else held.exact for held in known), Decimal(0)
    ).quantize(_CENT)
