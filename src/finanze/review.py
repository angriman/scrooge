"""Ask the user for the category of unmatched transactions, saving each answer as a rule."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TextIO

from finanze import categories, classification
from finanze.models import Transaction


def ask_categories(
    pending: Sequence[Transaction], rules_file: Path, out: TextIO, ask: Callable[[str], str]
) -> None:
    """Ask once per counterparty and direction; each answer becomes a rule in `rules_file`."""
    groups: dict[tuple[str, str], list[Transaction]] = {}
    unnamed = 0
    for txn in pending:
        name = (txn.counterparty or txn.description).strip()
        if not name:
            unnamed += 1  # a rule on an empty text would match every transaction
            continue
        groups.setdefault((name, classification.direction(txn)), []).append(txn)
    if unnamed:
        out.write(f"\n{unnamed} rows have no text to match: they need a rule written by hand.\n")
    menu = "  ".join(f"{n}={name}" for n, name in enumerate(categories.ORDER, start=1))
    out.write(f"\nCategories: {menu}\nEnter a number or name, nothing to skip, q to stop.\n")
    for (counterparty, flow), rows in groups.items():
        total = sum(txn.amount for txn in rows)
        out.write(f"\n{rows[0].account}: {rows[0].description}\n")
        answer = ask(f"  {counterparty} ({len(rows)} rows, {total}) -> ").strip()
        while answer and answer != "q" and _category(answer) is None:
            answer = ask(f"  unknown category {answer!r}, try again -> ").strip()
        if answer == "q":
            return
        chosen = _category(answer)
        if chosen:
            classification.append_rule(rules_file, counterparty, flow, chosen)
            out.write(f"  saved to {rules_file}: {counterparty} -> {chosen}\n")


def _category(answer: str) -> str | None:
    if answer.isdigit() and 1 <= int(answer) <= len(categories.ORDER):
        return categories.ORDER[int(answer) - 1]
    matches = [name for name in categories.ORDER if name.lower() == answer.lower()]
    return matches[0] if matches else None
