"""The accounts of the accounts page as their institutions keep them: a broker's currencies are
one account there, not one each.

A statement that keeps cash in several currencies is read into one account per currency, named
by the account and the currency's code: IBKR's become `IBKR CHF`, `IBKR EUR` and `IBKR USD`
(`IBKR 2 USD` for a second account, which `account_labels` names), Revolut's `Revolut EUR` and
so on. Transactions, income and rules keep them apart, as they are booked. The accounts page
shows such accounts as one: those of one person and one source whose names are one stem and a
currency's code (three capitals) and that the same statement files cover, file to file, are
listed under the stem. Two accounts of one bank with one statement each (`Migros EUR`,
`Migros USD`, an IBAN each) share no file, so they stay apart; an account alone keeps its name.

The one account holds what its parts hold together, and its statements are theirs: a day is
covered when a statement of any of them covers it, since one statement covers them all, so a
currency the broker stops listing does not leave it behind.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from scrooge import holds, months

_CODE = re.compile(r"(.+) [A-Z]{3}")  # an account named by its stem and a currency's code


@dataclass(frozen=True, slots=True)
class Row:
    """One account of the accounts page: one the books keep, or a broker's currencies together."""

    line: months.Coverage  # how far it is committed and staged, named as the page shows it
    parts: tuple[str, ...]  # the accounts the books keep it as, by name; itself alone when one
    spans: tuple[months.Span, ...]  # the runs of days its statements cover, staged ones first
    statements: tuple[months.Statement, ...]  # newest first, each once
    held: holds.Holds | None  # what it holds now; None when its balance is not known


def rows(
    lines: Sequence[months.Coverage],
    tracks: Mapping[tuple[str, str, str], Sequence[months.Span]],
    files: Mapping[tuple[str, str], Sequence[months.Statement]],
    held: Mapping[tuple[str, str], holds.Holds],
) -> tuple[Row, ...]:
    """The accounts of `lines` as the page lists them, by person and name: `tracks` are their
    runs of days by person, account and source (`months.timeline`), `files` their statements by
    person and account (`months.by_account`) and `held` what they hold (`holds.of`)."""

    def ids(line: months.Coverage) -> set[int]:
        listed = files.get((line.owner, line.account), ())
        return {s.id for s in listed if s.source == line.source}

    # By stem (None: not named by a currency), the accounts together and their statements.
    groups: list[tuple[str | None, list[months.Coverage], set[int]]] = []
    for line in lines:
        coded = _CODE.fullmatch(line.account)
        stem = coded[1] if coded else None
        covered = ids(line)
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
    found = [_row(stem or "", parts, tracks, files, held) for stem, parts, _ in groups]
    return tuple(sorted(found, key=lambda row: (row.line.owner, row.line.account, row.line.source)))


def _row(
    stem: str,
    parts: Sequence[months.Coverage],
    tracks: Mapping[tuple[str, str, str], Sequence[months.Span]],
    files: Mapping[tuple[str, str], Sequence[months.Statement]],
    held: Mapping[tuple[str, str], holds.Holds],
) -> Row:
    first = parts[0]
    if len(parts) == 1:
        line = first
    else:
        committed = max((part.committed for part in parts if part.committed), default=None)
        staged = max((part.staged for part in parts if part.staged), default=None)
        ahead = staged if staged and (not committed or staged > committed) else None
        line = months.Coverage(first.owner, stem, first.source, committed, ahead)
    names = tuple(sorted(part.account for part in parts))
    spans = [span for name in names for span in tracks.get((first.owner, name, first.source), ())]
    statements = {
        s.id: s
        for name in names
        for s in files.get((first.owner, name), ())
        if s.source == first.source
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
        combined([held[first.owner, name] for name in names if (first.owner, name) in held]),
    )


def combined(parts: Sequence[holds.Holds]) -> holds.Holds | None:
    """What accounts hold together: their balances added up, their cash by kind and currency,
    their securities, and their month-ends day by day (one that held nothing yet on a day adds
    nothing to it). None when none of them is known."""
    if len(parts) < 2:
        return parts[0] if parts else None
    cash: dict[tuple[str, str], tuple[Decimal, Decimal]] = {}
    for each in (c for part in parts for c in part.cash):
        amount, converted = cash.get((each.asset, each.currency), (Decimal(0), Decimal(0)))
        cash[each.asset, each.currency] = amount + each.amount, converted + each.converted
    securities = [part.securities for part in parts if part.securities is not None]
    ends: dict[date, Decimal] = {}
    for end in (m for part in parts for m in part.months):
        ends[end.day] = ends.get(end.day, Decimal(0)) + end.balance
    return holds.Holds(
        sum((part.balance for part in parts), Decimal(0)),
        tuple(
            sorted(
                (holds.Cash(asset, code, *cash[asset, code]) for asset, code in cash),
                key=lambda each: (-each.converted, each.currency, each.asset),
            )
        ),
        sum(securities, Decimal(0)) if securities else None,
        tuple(holds.MonthEnd(day, ends[day]) for day in sorted(ends)),
    )
