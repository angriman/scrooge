"""The `finanze` command: every command works on the database, see `finanze import --help`."""

from __future__ import annotations

import sys
from collections.abc import Callable, Sequence
from typing import TextIO

from finanze import dbcli


def main(
    argv: Sequence[str] | None = None,
    out: TextIO = sys.stdout,
    ask: Callable[[str], str] = input,
) -> int:
    """Run a database command; without one, or with one that is unknown, print the usage."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    return dbcli.main(arguments, out, ask)
