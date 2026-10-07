"""Ask the user for the category of unmatched transactions, saving each answer as a rule."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TextIO

from finanze import categories, classification
from finanze.categories import Extra
from finanze.models import Transaction

_HELP = (
    "Enter a number or name, nothing to skip, q to stop.\n"
    "A new category: +Name, or +Group:Name to put an expense under a top-level category.\n"
)


def ask_categories(
    pending: Sequence[Transaction],
    rules_file: Path,
    categories_file: Path,
    out: TextIO,
    ask: Callable[[str], str],
) -> None:
    """Ask once per counterparty and direction; each answer becomes a rule in `rules_file`.

    An answer like `+Prestiti` adds the category to `categories_file` first.
    """
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
    extras = categories.load(categories_file)
    menu = "  ".join(f"{n}={name}" for n, name in enumerate(categories.names(extras), start=1))
    out.write(f"\nCategories: {menu}\n{_HELP}")
    for (counterparty, flow), rows in groups.items():
        total = sum(txn.amount for txn in rows)
        out.write(f"\n{rows[0].account}: {rows[0].description}\n")
        answer = ask(f"  {counterparty} ({len(rows)} rows, {total}) -> ").strip()
        while answer and answer != "q" and not _understood(answer, extras):
            answer = ask(f"  unknown category {answer!r}, try again -> ").strip()
        if answer == "q":
            return
        if not answer:
            continue
        chosen = _category(answer, extras)
        if chosen is None:
            new = _new(answer, flow, extras)
            categories.add(categories_file, new)
            extras = categories.load(categories_file)
            chosen = new.name
            number = categories.names(extras).index(chosen) + 1
            under = f" under {new.group}" if new.group else ""
            where = f"{new.side}{under}"
            out.write(f"  new category {number}={chosen} ({where}) in {categories_file}\n")
        classification.append_rule(rules_file, counterparty, flow, chosen)
        out.write(f"  saved to {rules_file}: {counterparty} -> {chosen}\n")


def _understood(answer: str, extras: tuple[Extra, ...]) -> bool:
    if answer.startswith("+"):
        return bool(answer[1:].rpartition(":")[2].strip())
    return _category(answer, extras) is not None


def _category(answer: str, extras: tuple[Extra, ...]) -> str | None:
    known = categories.names(extras)
    if answer.isdigit() and 1 <= int(answer) <= len(known):
        return known[int(answer) - 1]
    matches = [name for name in known if name.lower() == answer.lower()]
    return matches[0] if matches else None


def _new(answer: str, flow: str, extras: tuple[Extra, ...]) -> Extra:
    """`+Group:Name` for a row going `flow`: money received is Entrate, which have no groups."""
    group, _, name = (part.strip() for part in answer[1:].rpartition(":"))
    if flow == "in":
        return Extra(name, "entrate")
    group = group or name  # a category on its own is its own group, like Regali
    same = [known for known in categories.groups(extras) if known.lower() == group.lower()]
    return Extra(name, "uscite", same[0] if same else group)
