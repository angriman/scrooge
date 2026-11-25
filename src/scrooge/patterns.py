"""Patterns of rules run on the committed rows away from the server, for the settings page.

A regular expression can take longer than any page should wait (`(a+)+$` on a long text), and
Python's `re` cannot be stopped while it matches: in the server's own process one such pattern
would hold every other request. So the patterns of the settings page run in a process of their
own, which hands back what it has found as it goes, and is killed when its time is up. What
is guaranteed: no pattern runs on the rows in the server's process, and the server waits at
most the time given, then has what came back before it.

The process is started by a fork server, which forks it from a clean process that has already
imported this module: it starts in a few milliseconds, and nothing of the server's threads or
open database goes with it.
"""

from __future__ import annotations

import multiprocessing
import multiprocessing.queues
import queue
import re
import time
from collections.abc import Callable, Sequence
from datetime import date
from decimal import Decimal

from scrooge.classification import Rule
from scrooge.models import Kind, Transaction

# What a rule reads of a row: its account, whether money came in, description, counterparty.
Text = tuple[str, bool, str, str]
# A rule as it is counted: pattern, account, direction, and whose rows (None: everyone's).
Spec = tuple[str, str, str, str | None]
Put = Callable[[object], None]

_DONE = "done"
_BATCH = 500  # texts decided before what was found is handed back
_CONTEXT = multiprocessing.get_context("forkserver")
_CONTEXT.set_forkserver_preload([__name__])


def _read(text: Text) -> Transaction:
    account, money_in, description, counterparty = text
    return Transaction(
        date=date.min,
        account=account,
        amount=Decimal(1 if money_in else -1),
        currency="",
        description=description,
        kind=Kind.EXPENSE,
        source_file="",
        source_ref="",
        counterparty=counterparty,
    )


def matching(match: str, direction: str, texts: Sequence[Text], put: Put) -> None:
    """Hand back, a batch at a time, how many of `texts` (in their order) are decided so far
    and which of them the pattern `match` matches in `direction`: `(decided, [indexes])`."""
    rule = Rule(re.compile(match, re.IGNORECASE), "", direction=direction)
    found: list[int] = []
    for n, text in enumerate(texts):
        if rule.matches(_read(text)):
            found.append(n)
        if (n + 1) % _BATCH == 0:
            put((n + 1, found))
            found = []
    put((len(texts), found))


def counting(specs: Sequence[Spec], texts: Sequence[tuple[str, Text, int]], put: Put) -> None:
    """Hand back, rule by rule, `(index, rows)`: how many rows of `texts` (owner, text, how
    many rows read so) each of `specs` matches on its own; None for one that is no pattern."""
    for n, (match, account, direction, owner) in enumerate(specs):
        try:
            pattern = re.compile(match, re.IGNORECASE)
        except re.error:
            put((n, None))
            continue
        rule = Rule(pattern, "", None, account, direction)
        put(
            (
                n,
                sum(
                    count
                    for whose, text, count in texts
                    if owner in (None, whose) and rule.matches(_read(text))
                ),
            )
        )


def _child(
    work: Callable[..., None], args: tuple[object, ...], out: multiprocessing.queues.Queue[object]
) -> None:
    try:
        work(*args, out.put)
    finally:
        out.put(_DONE)


def bounded(
    work: Callable[..., None], args: tuple[object, ...], seconds: float
) -> tuple[list[object], bool]:
    """Run `work(*args, put)` in a process of its own for at most `seconds`: what it put, in
    order, and whether it finished. When time is up the process is killed."""
    out: multiprocessing.queues.Queue[object] = _CONTEXT.Queue()
    process = _CONTEXT.Process(target=_child, args=(work, args, out), daemon=True)
    process.start()
    deadline = time.monotonic() + seconds
    got: list[object] = []
    try:
        while True:
            try:
                item = out.get(timeout=max(deadline - time.monotonic(), 0.001))
            except queue.Empty:
                return got, False
            if item == _DONE:
                process.join(1)
                return got, process.exitcode == 0
            got.append(item)
    finally:
        if process.is_alive():
            process.kill()
        process.join()
