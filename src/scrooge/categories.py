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

# Every name of the taxonomy is written once, here: code names a category by its constant,
# never by its text, so a name changes in one place. A leaf and a top-level category of the
# same text are two constants: `GROUP_` names the top-level one.

# What `unmatched` in the config may give the expenses and income no rule matches, when
# categories are not tracked: net worth needs none. A leaf of both sides and its own group.
UNSORTED = "Uncategorized"

# The top-level categories of the expenses.
GROUP_HOME = "Home"
GROUP_HEALTH = "Health"
GROUP_FOOD = "Food"
GROUP_TRANSPORT = "Transport"
GROUP_PHONE = "Phone"
GROUP_SUBSCRIPTIONS = "Subscriptions"
GROUP_PERSONAL_CARE = "Personal care"
GROUP_CLOTHING = "Clothing"
GROUP_SHOPPING = "Shopping"
GROUP_TRAVEL = "Travel and leisure"
GROUP_EDUCATION = "Education"
GROUP_TAXES = "Taxes and paperwork"
GROUP_BANKING = "Banking"

# The leaves of the expenses.
RENT = "Rent"
UTILITIES = "Utilities"
HOUSEHOLD = "Household"
HEALTH_INSURANCE = "Health insurance"
DENTIST = "Dentist"
DOCTOR = "Doctor/Pharmacy"
GROCERIES = "Groceries"
EATING_OUT = "Eating out"
TRAINS = "Trains"
FUEL = "Fuel"
LOCAL_TRANSPORT = "Local transport"
FLIGHTS = "Flights"
PARKING = "Parking and tolls"
SIM = "SIM"
DIGITAL_SUBSCRIPTIONS = "Digital subscriptions"
REVOLUT_METAL = "Revolut Metal"
BARBER = "Barber"
CLOTHES = "Clothes"
ELECTRONICS = "Electronics"
OTHER_SHOPPING = "Other shopping"
ACCOMMODATION = "Accommodation"
ACTIVITIES = "Activities"
SPORT = "Sport"
LANGUAGE_LESSONS = "Language lessons"
BOOKS = "Books"
GIFTS = "Gifts"
DONATIONS = "Donations"
TAXES = "Taxes"
PAPERWORK = "Paperwork"
WITHHOLDING_TAX = "Withholding tax"
# Leaves of both sides: what was paid and what came back.
FEES = "Fees"
TRADING = "Trading"  # what trading won or lost
INVESTMENTS = "Investments"  # securities bought and sold: principal, never income or spending

# Top-level categories named like their leaf.
GROUP_EATING_OUT = EATING_OUT
GROUP_GIFTS = GIFTS
GROUP_INVESTMENTS = INVESTMENTS  # of the leaves INVESTMENTS and TRADING

# The leaves of the income, besides those of both sides.
SALARY = "Salary"
DIVIDENDS = "Dividends"
INTEREST = "Interest"
CASHBACK = "Cashback"
REFUNDS = "Refunds"
GIFTS_RECEIVED = "Gifts received"

# The transfers.
REVOLUT_TOP_UP = "Revolut top-up"
IBKR_TOP_UP = "IBKR top-up"
WITHDRAWALS = "Withdrawals"
OWN_TRANSFER = "Own transfer"  # between my own accounts, when nothing else says it better
BUY_CURRENCIES = ("EUR", "USD")  # the currencies an exchange into has a category of its own


def buy(currency: str) -> str:
    """The category of an exchange into `currency`, one of BUY_CURRENCIES."""
    return f"Buy {currency}"


USCITE: dict[str, tuple[str, ...]] = {
    GROUP_HOME: (RENT, UTILITIES, HOUSEHOLD),
    GROUP_HEALTH: (HEALTH_INSURANCE, DENTIST, DOCTOR),
    GROUP_FOOD: (GROCERIES,),
    GROUP_EATING_OUT: (EATING_OUT,),
    GROUP_TRANSPORT: (TRAINS, FUEL, LOCAL_TRANSPORT, FLIGHTS, PARKING),
    GROUP_PHONE: (SIM,),
    GROUP_SUBSCRIPTIONS: (DIGITAL_SUBSCRIPTIONS, REVOLUT_METAL),
    GROUP_PERSONAL_CARE: (BARBER,),
    GROUP_CLOTHING: (CLOTHES,),
    GROUP_SHOPPING: (ELECTRONICS, OTHER_SHOPPING),
    GROUP_TRAVEL: (ACCOMMODATION, ACTIVITIES, SPORT),
    GROUP_EDUCATION: (LANGUAGE_LESSONS, BOOKS),
    GROUP_GIFTS: (GIFTS, DONATIONS),
    GROUP_TAXES: (TAXES, PAPERWORK),
    GROUP_BANKING: (FEES, WITHHOLDING_TAX),
    GROUP_INVESTMENTS: (TRADING, INVESTMENTS),
    UNSORTED: (UNSORTED,),
}
ENTRATE: tuple[str, ...] = (
    SALARY,
    DIVIDENDS,
    INTEREST,
    CASHBACK,
    REFUNDS,
    GIFTS_RECEIVED,
    TRADING,
    FEES,
    INVESTMENTS,
    UNSORTED,
)
TRANSFERS: tuple[str, ...] = (
    REVOLUT_TOP_UP,
    IBKR_TOP_UP,
    *map(buy, BUY_CURRENCIES),
    WITHDRAWALS,
    OWN_TRANSFER,
)
# The Italian names the taxonomy had until 2026-11-24 (migration 0010 renamed them in the
# database). A rules file, `categories.yaml` or the config that still names one means the
# English one: the user's files are never rewritten for it.
ITALIAN: dict[str, str] = {
    "Casa": GROUP_HOME,
    "Affitto": RENT,
    "Utenze": UTILITIES,
    "Arredamento e casa": HOUSEHOLD,
    "Salute": GROUP_HEALTH,
    "Assicurazione sanitaria": HEALTH_INSURANCE,
    "Dentista": DENTIST,
    "Medico/Farmacia": DOCTOR,
    "Alimentari": GROUP_FOOD,
    "Spesa": GROCERIES,
    "Ristoranti e bar": EATING_OUT,
    "Trasporti": GROUP_TRANSPORT,
    "Treni": TRAINS,
    "Carburante": FUEL,
    "Trasporti locali": LOCAL_TRANSPORT,
    "Voli": FLIGHTS,
    "Parcheggi e pedaggi": PARKING,
    "Telefonia": GROUP_PHONE,
    "Abbonamenti": GROUP_SUBSCRIPTIONS,
    "Abbonamenti digitali": DIGITAL_SUBSCRIPTIONS,
    "Cura personale": GROUP_PERSONAL_CARE,
    "Barbiere": BARBER,
    "Abbigliamento": GROUP_CLOTHING,
    "Vestiti": CLOTHES,
    "Acquisti": GROUP_SHOPPING,
    "Elettronica": ELECTRONICS,
    "Acquisti vari": OTHER_SHOPPING,
    "Viaggi e svago": GROUP_TRAVEL,
    "Alloggi": ACCOMMODATION,
    "Attività/Svago": ACTIVITIES,
    "Formazione": GROUP_EDUCATION,
    "Lezioni di lingua": LANGUAGE_LESSONS,
    "Libri": BOOKS,
    "Regali": GIFTS,
    "Donazioni": DONATIONS,
    "Tasse e burocrazia": GROUP_TAXES,
    "Tasse": TAXES,
    "Documenti e burocrazia": PAPERWORK,
    "Banca e broker": GROUP_BANKING,
    "Commissioni": FEES,
    "Ritenute": WITHHOLDING_TAX,
    "Investimenti": INVESTMENTS,
    "Non classificato": UNSORTED,
    "Stipendio": SALARY,
    "Dividendi": DIVIDENDS,
    "Interessi": INTEREST,
    "Rimborsi": REFUNDS,
    "Regali ricevuti": GIFTS_RECEIVED,
    "Topup Revolut": REVOLUT_TOP_UP,
    "Topup IBKR": IBKR_TOP_UP,
    **{f"Acquisto {currency}": buy(currency) for currency in BUY_CURRENCIES},
    "Prelievi": WITHDRAWALS,
    "Giroconto": OWN_TRANSFER,
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
