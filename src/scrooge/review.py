"""Ask the user for the category of unmatched transactions, saving each answer as a rule."""

from __future__ import annotations

import textwrap
import unicodedata
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TextIO

from scrooge import categories, classification
from scrooge.categories import Extra
from scrooge.classification import Patterns
from scrooge.models import Transaction

_HELP = (
    "Answer with a number, a name or a part of one (groc -> Groceries).\n"
    "  ?            list the categories again\n"
    "  +Name        a new category; +Group:Name puts an expense under a top-level category\n"
    "  (nothing)    skip these rows\n"
    "  q            stop\n"
)
_WIDTH = 100
_STOP = "q"


def ask_categories(
    pending: Sequence[Transaction],
    rules_file: Path,
    out: TextIO,
    ask: Callable[[str], str],
    personal: tuple[Path, Patterns] | None = None,
) -> None:
    """Ask once per counterparty and direction; each answer becomes a rule in `rules_file`,
    naming the category by its label.

    An answer like `+Prestiti` adds the category to `categories.yaml`, next to it, first.
    `personal` is the file of the owner's own rules and what recognises a person, anybody: the
    answer for a row that names one is saved there instead, since on the statements of the
    other person the same name means something else.
    """
    categories_file = rules_file.with_name(categories.FILE)
    own_file, people = personal or (rules_file, ())
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
    out.write(f"\n{menu(categories.load(categories_file))}\n{_HELP}")
    for n, ((counterparty, flow), rows) in enumerate(groups.items(), start=1):
        total = sum(txn.amount for txn in rows)
        out.write(f"\n[{n}/{len(groups)}] {rows[0].account}: {rows[0].description}\n")
        question = f"  {counterparty} ({len(rows)} rows, {total}) -> "
        answer = _answer(question, categories_file, out, ask)
        if answer == _STOP:
            return
        if not answer:
            continue
        extras = categories.load(categories_file)
        chosen = answer
        if answer.startswith("+"):
            new = _new(answer, flow, extras)
            categories.add(categories_file, new)
            chosen = new.key
            number = categories.names(categories.load(categories_file)).index(chosen) + 1
            under = f" under {new.group}" if new.group else ""
            side = f"{new.side}{under}"
            out.write(f"  new category {number}={new.name} ({side}) in {categories_file}\n")
        written = categories.Labels.of(categories.load(categories_file)).written(chosen)
        where = own_file if classification.names(rows[0], people) else rules_file
        classification.append_rule(where, counterparty, flow, written)
        out.write(f"  saved to {where}: {counterparty} -> {written}\n")


def menu(extras: tuple[Extra, ...]) -> str:
    """Every category with its number, one line per top-level category of the expenses."""
    label = categories.Labels.of(extras)
    number = {name: n for n, name in enumerate(categories.names(extras), start=1)}
    expenses = {group: list(leaves) for group, leaves in categories.EXPENSE_GROUPS.items()}
    for extra in extras:
        if extra.side == "expenses" and extra.key not in expenses.setdefault(extra.group_key, []):
            expenses[extra.group_key].append(extra.key)
    income = (*categories.INCOME_LEAVES, *(e.key for e in extras if e.side == "income"))
    margin = max(len(label(group)) for group in expenses) + 4
    lines = ["Expenses"]
    lines += [
        _line(f"  {label(group)}", leaves, number, margin, label)
        for group, leaves in expenses.items()
    ]
    lines += ["Income", _line("", income, number, 2, label)]
    lines += ["Transfers", _line("", categories.TRANSFERS, number, 2, label)]
    return "\n".join(lines)


def _line(
    heading: str,
    names: Sequence[str],
    number: dict[str, int],
    margin: int,
    label: categories.Labels,
) -> str:
    text = "   ".join(f"{number[name]} {label(name)}".replace(" ", "\xa0") for name in names)
    wrapped = textwrap.fill(
        text, _WIDTH, initial_indent=heading.ljust(margin), subsequent_indent=" " * margin
    )
    return wrapped.replace("\xa0", " ")


def _answer(question: str, categories_file: Path, out: TextIO, ask: Callable[[str], str]) -> str:
    """The key of a category that exists, `+...` for a new one, nothing to skip or `q` to
    stop."""
    while True:
        answer = ask(question).strip()
        extras = categories.load(categories_file)
        if answer == "?":
            out.write(f"{menu(extras)}\n{_HELP}")
            continue
        if answer in {"", _STOP}:
            return answer
        if answer.startswith("+"):
            if answer[1:].rpartition(":")[2].strip():
                return answer
            found: list[str] = []
        else:
            found = _find(answer, categories.names(extras), categories.Labels.of(extras))
            if len(found) == 1:
                return found[0]
        if found:
            known = categories.names(extras)
            label = categories.Labels.of(extras)
            choices = "   ".join(f"{known.index(name) + 1} {label(name)}" for name in found)
            out.write(f"  {answer!r} could be: {choices}\n")
            question = "  which one -> "
        else:
            question = f"  unknown category {answer!r}, try again (? lists them) -> "


def _find(answer: str, known: Sequence[str], label: categories.Labels) -> list[str]:
    """The keys `answer` can mean: its number, its key, label or old Italian name, else every
    category whose label contains it."""
    if answer.isdigit():
        return [known[int(answer) - 1]] if 1 <= int(answer) <= len(known) else []
    if categories.key(answer) in known:
        return [categories.key(answer)]
    wanted = _plain(answer)
    exact = [name for name in known if _plain(label(name)) == wanted]
    return exact or [name for name in known if wanted in _plain(label(name))]


def _plain(text: str) -> str:
    """Lower case, without accents: `attivita` finds `Attività/Svago`."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def _new(answer: str, flow: str, extras: tuple[Extra, ...]) -> Extra:
    """`+Group:Name` for a row going `flow`: money received is income, which has no groups."""
    group, _, name = (part.strip() for part in answer[1:].rpartition(":"))
    if flow == "in":
        return Extra(name, "income")
    group = group or name  # a category on its own is its own group, like Gifts
    label = categories.Labels.of(extras)
    same = [label(known) for known in categories.groups(extras) if known == categories.key(group)]
    return Extra(name, "expenses", same[0] if same else group)
