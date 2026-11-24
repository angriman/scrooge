"""The agreed two-level category taxonomy (D6). The sheet gets the leaf category.

The taxonomy below is the base. `categories.yaml` adds to it (never removes or renames):

    categories:
      expenses:
        Loans: [Loans]   # top-level category: its leaves
      income: [Loans]

A file written before 2026-11-24 says `uscite` and `entrate`, and may name the taxonomy's
categories by their Italian names (`ITALIAN`): both are read as they were meant.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import takewhile
from pathlib import Path
from typing import TypeGuard

import yaml

from scrooge import yamlfiles
from scrooge.errors import ConfigError

# What `unmatched` in the config may give the expenses and income no rule matches, when
# categories are not tracked: net worth needs none.
UNSORTED = "Uncategorized"
USCITE: dict[str, tuple[str, ...]] = {
    "Home": ("Rent", "Utilities", "Household"),
    "Health": ("Health insurance", "Dentist", "Doctor/Pharmacy"),
    "Food": ("Groceries",),
    "Eating out": ("Eating out",),
    "Transport": ("Trains", "Fuel", "Local transport", "Flights", "Parking and tolls"),
    "Phone": ("SIM",),
    "Subscriptions": ("Digital subscriptions", "Revolut Metal"),
    "Personal care": ("Barber",),
    "Clothing": ("Clothes",),
    "Shopping": ("Electronics", "Other shopping"),
    "Travel and leisure": ("Accommodation", "Activities", "Sport"),
    "Education": ("Language lessons", "Books"),
    "Gifts": ("Gifts", "Donations"),
    "Taxes and paperwork": ("Taxes", "Paperwork"),
    "Banking": ("Fees", "Withholding tax"),
    "Investments": ("Trading", "Investments"),
    UNSORTED: (UNSORTED,),
}
ENTRATE: tuple[str, ...] = (
    "Salary",
    "Dividends",
    "Interest",
    "Cashback",
    "Refunds",
    "Gifts received",
    "Trading",
    "Fees",
    "Investments",
    UNSORTED,
)
TRANSFERS: tuple[str, ...] = (
    "Revolut top-up",
    "IBKR top-up",
    "Buy EUR",
    "Buy USD",
    "Withdrawals",
    "Own transfer",  # between my own accounts, when none of the above says it better
)
# The Italian names the taxonomy had until 2026-11-24 (migration 0010 renamed them in the
# database). A rules file, `categories.yaml` or the config that still names one means the
# English one: the user's files are never rewritten for it.
ITALIAN: dict[str, str] = {
    "Casa": "Home",
    "Affitto": "Rent",
    "Utenze": "Utilities",
    "Arredamento e casa": "Household",
    "Salute": "Health",
    "Assicurazione sanitaria": "Health insurance",
    "Dentista": "Dentist",
    "Medico/Farmacia": "Doctor/Pharmacy",
    "Alimentari": "Food",
    "Spesa": "Groceries",
    "Ristoranti e bar": "Eating out",
    "Trasporti": "Transport",
    "Treni": "Trains",
    "Carburante": "Fuel",
    "Trasporti locali": "Local transport",
    "Voli": "Flights",
    "Parcheggi e pedaggi": "Parking and tolls",
    "Telefonia": "Phone",
    "Abbonamenti": "Subscriptions",
    "Abbonamenti digitali": "Digital subscriptions",
    "Cura personale": "Personal care",
    "Barbiere": "Barber",
    "Abbigliamento": "Clothing",
    "Vestiti": "Clothes",
    "Acquisti": "Shopping",
    "Elettronica": "Electronics",
    "Acquisti vari": "Other shopping",
    "Viaggi e svago": "Travel and leisure",
    "Alloggi": "Accommodation",
    "Attività/Svago": "Activities",
    "Formazione": "Education",
    "Lezioni di lingua": "Language lessons",
    "Libri": "Books",
    "Regali": "Gifts",
    "Donazioni": "Donations",
    "Tasse e burocrazia": "Taxes and paperwork",
    "Tasse": "Taxes",
    "Documenti e burocrazia": "Paperwork",
    "Banca e broker": "Banking",
    "Commissioni": "Fees",
    "Ritenute": "Withholding tax",
    "Investimenti": "Investments",
    "Non classificato": UNSORTED,
    "Stipendio": "Salary",
    "Dividendi": "Dividends",
    "Interessi": "Interest",
    "Rimborsi": "Refunds",
    "Regali ricevuti": "Gifts received",
    "Topup Revolut": "Revolut top-up",
    "Topup IBKR": "IBKR top-up",
    "Acquisto EUR": "Buy EUR",
    "Acquisto USD": "Buy USD",
    "Prelievi": "Withdrawals",
    "Giroconto": "Own transfer",
}


def canonical(name: str) -> str:
    """The category `name` means: the English one for an Italian name the taxonomy had."""
    return ITALIAN.get(name, name)


# Every leaf category, in the order rows are written to the sheet.
ORDER: tuple[str, ...] = tuple(
    dict.fromkeys([*(leaf for leaves in USCITE.values() for leaf in leaves), *ENTRATE, *TRANSFERS])
)
ALL: frozenset[str] = frozenset(ORDER)

FILE = "categories.yaml"  # looked for next to the first rules file
SIDES = ("uscite", "entrate")  # the sides' names in the database and in `Extra`
# How `categories.yaml` names each side, and how it named them before.
_KEYS = {"expenses": "uscite", "income": "entrate", "uscite": "uscite", "entrate": "entrate"}


@dataclass(frozen=True, slots=True)
class Extra:
    """A leaf category added by `categories.yaml`."""

    name: str
    side: str  # "uscite" | "entrate"
    group: str = ""  # uscite only: the top-level category it sits under


def load(path: Path) -> tuple[Extra, ...]:
    """The categories `path` adds to the taxonomy; none when the file does not exist."""
    uscite, entrate = _read(path)
    return (
        *(Extra(leaf, "uscite", group) for group, leaves in uscite.items() for leaf in leaves),
        *(Extra(name, "entrate") for name in entrate),
    )


def names(extras: tuple[Extra, ...]) -> tuple[str, ...]:
    """Every leaf category: the taxonomy in its order, then the added ones."""
    return tuple(dict.fromkeys([*ORDER, *(extra.name for extra in extras)]))


def groups(extras: tuple[Extra, ...]) -> tuple[str, ...]:
    """Every top-level category of the Uscite."""
    return tuple(dict.fromkeys([*USCITE, *(e.group for e in extras if e.side == "uscite")]))


def add(path: Path, extra: Extra) -> None:
    """Save one more category in `path` (created if missing), keeping its leading comments."""
    with yamlfiles.LOCK:
        uscite, entrate = _read(path)
        leaves = uscite.setdefault(extra.group, []) if extra.side == "uscite" else entrate
        if extra.name not in leaves:
            leaves.append(extra.name)
        _write(path, uscite, entrate)


def rename(path: Path, old: str, new: str) -> None:
    """Call the added category `old` `new` in `path`, everywhere it is added, and so the group
    of its own it was added with, if it was; the order stays as it was."""
    with yamlfiles.LOCK:
        uscite, entrate = _read(path)
        swap = {old: new}
        uscite = {
            group if group in USCITE else swap.get(group, group): [swap.get(n, n) for n in leaves]
            for group, leaves in uscite.items()
        }
        _write(path, uscite, [swap.get(name, name) for name in entrate])


def drop(path: Path, name: str) -> None:
    """Take the added category `name` out of `path`, and the group it leaves empty with it."""
    with yamlfiles.LOCK:
        uscite, entrate = _read(path)
        _write(path, _without(uscite, name), [entry for entry in entrate if entry != name])


def move(path: Path, name: str, group: str) -> None:
    """Put the added expense category `name` under `group` in `path` (a new group when there
    is none of that name), at its end; the group it leaves empty goes."""
    with yamlfiles.LOCK:
        uscite, entrate = _read(path)
        moved = _without(uscite, name)
        moved.setdefault(group, []).append(name)
        _write(path, moved, entrate)


def _without(uscite: dict[str, list[str]], name: str) -> dict[str, list[str]]:
    """The groups without the leaf `name`; a group it leaves empty is left out."""
    kept = {group: [leaf for leaf in leaves if leaf != name] for group, leaves in uscite.items()}
    return {group: leaves for group, leaves in kept.items() if leaves or not uscite[group]}


def _write(path: Path, uscite: dict[str, list[str]], entrate: list[str]) -> None:
    """Write the additions to `path` again, keeping its leading comments."""
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    header = "".join(takewhile(lambda line: line.startswith("#"), text.splitlines(keepends=True)))
    body = yaml.safe_dump(
        {"categories": {"expenses": uscite, "income": entrate}},
        allow_unicode=True,
        sort_keys=False,
    )
    yamlfiles.write(path, header + body)


def _read(path: Path) -> tuple[dict[str, list[str]], list[str]]:
    data = (yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else None) or {}
    added = (data.get("categories") or {}) if isinstance(data, dict) else None
    if not isinstance(added, dict) or not added.keys() <= _KEYS.keys():
        msg = f"{path}: expected a top-level 'categories' with 'expenses' and 'income'"
        raise ConfigError(msg)
    sides: dict[str, object] = {}
    for key, value in added.items():
        if sides.get(_KEYS[key]) and value:
            msg = f"{path}: '{key}' and the other name of that side are both given: keep one"
            raise ConfigError(msg)
        sides[_KEYS[key]] = sides.get(_KEYS[key]) or value
    uscite = sides.get("uscite") or {}
    entrate = sides.get("entrate") or []
    if (
        not isinstance(uscite, dict)
        or not all(_is_names(leaves) for leaves in uscite.values())
        or not _is_names(entrate)
    ):
        msg = (
            f"{path}: 'expenses' maps each top-level category to a list of names, "
            "'income' is a list of names"
        )
        raise ConfigError(msg)
    expenses: dict[str, list[str]] = {}
    for group, leaves in uscite.items():
        expenses.setdefault(canonical(str(group)), []).extend(map(canonical, leaves))
    return expenses, [canonical(name) for name in entrate]


def _is_names(value: object) -> TypeGuard[list[str]]:
    return isinstance(value, list) and all(isinstance(name, str) and name for name in value)
