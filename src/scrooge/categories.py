"""The agreed two-level category taxonomy (D6), named by keys and shown by labels.

A category is known by its key (`groceries`, `own_transfer`): the database, the code and the
rules all name it so. People see its label, from a table per language (`LABELS`), so a new
language or a label written better is a change of that table, never of the stored data.

The taxonomy below is the base. `categories.yaml` adds to it (never removes or renames):

    categories:
      expenses:
        Loans: [Loans]   # top-level category: its leaves
      income: [Loans]

A category added there, or on a page, has no translation: its label is the name it was given,
and its key is made from that name (`key`). A file may name a built-in category by its key, by
its English label or by the Italian name it had until 2026-11-24, and a file written before
then says `uscite` and `entrate`: all are read as they were meant, and never rewritten for it.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from itertools import takewhile
from pathlib import Path
from typing import TypeGuard

import yaml

from scrooge import yamlfiles
from scrooge.errors import ConfigError

# Every key of the taxonomy is written once, here: code names a category by its constant,
# never by its text. A leaf and a top-level category of the same label share their key
# (`GROUP_` names the top-level one): a key is looked up on one side and one level, as the
# database keeps it. A key is made of the English label it was given (`slug`) and never
# changes after, whatever its labels become.

# What `unmatched` in the config may give the expenses and income no rule matches, when
# categories are not tracked: net worth needs none. A leaf of both sides and its own group.
UNSORTED = "uncategorized"

# The top-level categories of the expenses.
GROUP_HOME = "home"
GROUP_HEALTH = "health"
GROUP_FOOD = "food"
GROUP_TRANSPORT = "transport"
GROUP_PHONE = "phone"
GROUP_SUBSCRIPTIONS = "subscriptions"
GROUP_PERSONAL_CARE = "personal_care"
GROUP_CLOTHING = "clothing"
GROUP_SHOPPING = "shopping"
GROUP_TRAVEL = "travel_and_leisure"
GROUP_EDUCATION = "education"
GROUP_TAXES = "taxes_and_paperwork"
GROUP_BANKING = "banking"

# The leaves of the expenses.
RENT = "rent"
UTILITIES = "utilities"
HOUSEHOLD = "household"
HEALTH_INSURANCE = "health_insurance"
DENTIST = "dentist"
DOCTOR = "doctor_pharmacy"
GROCERIES = "groceries"
EATING_OUT = "eating_out"
TRAINS = "trains"
FUEL = "fuel"
LOCAL_TRANSPORT = "local_transport"
FLIGHTS = "flights"
PARKING = "parking_and_tolls"
SIM = "sim"
DIGITAL_SUBSCRIPTIONS = "digital_subscriptions"
REVOLUT_METAL = "revolut_metal"
BARBER = "barber"
CLOTHES = "clothes"
ELECTRONICS = "electronics"
OTHER_SHOPPING = "other_shopping"
ACCOMMODATION = "accommodation"
ACTIVITIES = "activities"
SPORT = "sport"
LANGUAGE_LESSONS = "language_lessons"
BOOKS = "books"
GIFTS = "gifts"
DONATIONS = "donations"
TAXES = "taxes"
PAPERWORK = "paperwork"
WITHHOLDING_TAX = "withholding_tax"
# Leaves of both sides: what was paid and what came back.
FEES = "fees"
TRADING = "trading"  # what trading won or lost
INVESTMENTS = "investments"  # securities bought and sold: principal, never income or spending

# Top-level categories named like their leaf.
GROUP_EATING_OUT = EATING_OUT
GROUP_GIFTS = GIFTS
GROUP_INVESTMENTS = INVESTMENTS  # of the leaves INVESTMENTS and TRADING

# The leaves of the income, besides those of both sides.
SALARY = "salary"
DIVIDENDS = "dividends"
INTEREST = "interest"
CASHBACK = "cashback"
REFUNDS = "refunds"
GIFTS_RECEIVED = "gifts_received"

# The transfers.
REVOLUT_TOP_UP = "revolut_top_up"
IBKR_TOP_UP = "ibkr_top_up"
WITHDRAWALS = "withdrawals"
OWN_TRANSFER = "own_transfer"  # between my own accounts, when nothing else says it better
BUY_CURRENCIES = ("EUR", "USD")  # the currencies an exchange into has a category of its own


def buy(currency: str) -> str:
    """The category of an exchange into `currency`, one of BUY_CURRENCIES."""
    return f"buy_{currency.lower()}"


EXPENSE_GROUPS: dict[str, tuple[str, ...]] = {
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
INCOME_LEAVES: tuple[str, ...] = (
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

# What people see of each key, in English: the app's language.
LABELS_EN: dict[str, str] = {
    UNSORTED: "Uncategorized",
    GROUP_HOME: "Home",
    GROUP_HEALTH: "Health",
    GROUP_FOOD: "Food",
    GROUP_TRANSPORT: "Transport",
    GROUP_PHONE: "Phone",
    GROUP_SUBSCRIPTIONS: "Subscriptions",
    GROUP_PERSONAL_CARE: "Personal care",
    GROUP_CLOTHING: "Clothing",
    GROUP_SHOPPING: "Shopping",
    GROUP_TRAVEL: "Travel and leisure",
    GROUP_EDUCATION: "Education",
    GROUP_TAXES: "Taxes and paperwork",
    GROUP_BANKING: "Banking",
    RENT: "Rent",
    UTILITIES: "Utilities",
    HOUSEHOLD: "Household",
    HEALTH_INSURANCE: "Health insurance",
    DENTIST: "Dentist",
    DOCTOR: "Doctor/Pharmacy",
    GROCERIES: "Groceries",
    EATING_OUT: "Eating out",
    TRAINS: "Trains",
    FUEL: "Fuel",
    LOCAL_TRANSPORT: "Local transport",
    FLIGHTS: "Flights",
    PARKING: "Parking and tolls",
    SIM: "SIM",
    DIGITAL_SUBSCRIPTIONS: "Digital subscriptions",
    REVOLUT_METAL: "Revolut Metal",
    BARBER: "Barber",
    CLOTHES: "Clothes",
    ELECTRONICS: "Electronics",
    OTHER_SHOPPING: "Other shopping",
    ACCOMMODATION: "Accommodation",
    ACTIVITIES: "Activities",
    SPORT: "Sport",
    LANGUAGE_LESSONS: "Language lessons",
    BOOKS: "Books",
    GIFTS: "Gifts",
    DONATIONS: "Donations",
    TAXES: "Taxes",
    PAPERWORK: "Paperwork",
    WITHHOLDING_TAX: "Withholding tax",
    FEES: "Fees",
    TRADING: "Trading",
    INVESTMENTS: "Investments",
    SALARY: "Salary",
    DIVIDENDS: "Dividends",
    INTEREST: "Interest",
    CASHBACK: "Cashback",
    REFUNDS: "Refunds",
    GIFTS_RECEIVED: "Gifts received",
    REVOLUT_TOP_UP: "Revolut top-up",
    IBKR_TOP_UP: "IBKR top-up",
    **{buy(currency): f"Buy {currency}" for currency in BUY_CURRENCIES},
    WITHDRAWALS: "Withdrawals",
    OWN_TRANSFER: "Own transfer",
}
# The same in Italian: the names the taxonomy had until 2026-11-24. A rules file,
# `categories.yaml` or the config that still names one means its key.
LABELS_IT: dict[str, str] = {
    UNSORTED: "Non classificato",
    GROUP_HOME: "Casa",
    GROUP_HEALTH: "Salute",
    GROUP_FOOD: "Alimentari",
    GROUP_TRANSPORT: "Trasporti",
    GROUP_PHONE: "Telefonia",
    GROUP_SUBSCRIPTIONS: "Abbonamenti",
    GROUP_PERSONAL_CARE: "Cura personale",
    GROUP_CLOTHING: "Abbigliamento",
    GROUP_SHOPPING: "Acquisti",
    GROUP_TRAVEL: "Viaggi e svago",
    GROUP_EDUCATION: "Formazione",
    GROUP_TAXES: "Tasse e burocrazia",
    GROUP_BANKING: "Banca e broker",
    RENT: "Affitto",
    UTILITIES: "Utenze",
    HOUSEHOLD: "Arredamento e casa",
    HEALTH_INSURANCE: "Assicurazione sanitaria",
    DENTIST: "Dentista",
    DOCTOR: "Medico/Farmacia",
    GROCERIES: "Spesa",
    EATING_OUT: "Ristoranti e bar",
    TRAINS: "Treni",
    FUEL: "Carburante",
    LOCAL_TRANSPORT: "Trasporti locali",
    FLIGHTS: "Voli",
    PARKING: "Parcheggi e pedaggi",
    SIM: "SIM",
    DIGITAL_SUBSCRIPTIONS: "Abbonamenti digitali",
    REVOLUT_METAL: "Revolut Metal",
    BARBER: "Barbiere",
    CLOTHES: "Vestiti",
    ELECTRONICS: "Elettronica",
    OTHER_SHOPPING: "Acquisti vari",
    ACCOMMODATION: "Alloggi",
    ACTIVITIES: "Attività/Svago",
    SPORT: "Sport",
    LANGUAGE_LESSONS: "Lezioni di lingua",
    BOOKS: "Libri",
    GIFTS: "Regali",
    DONATIONS: "Donazioni",
    TAXES: "Tasse",
    PAPERWORK: "Documenti e burocrazia",
    WITHHOLDING_TAX: "Ritenute",
    FEES: "Commissioni",
    TRADING: "Trading",
    INVESTMENTS: "Investimenti",
    SALARY: "Stipendio",
    DIVIDENDS: "Dividendi",
    INTEREST: "Interessi",
    CASHBACK: "Cashback",
    REFUNDS: "Rimborsi",
    GIFTS_RECEIVED: "Regali ricevuti",
    REVOLUT_TOP_UP: "Topup Revolut",
    IBKR_TOP_UP: "Topup IBKR",
    **{buy(currency): f"Acquisto {currency}" for currency in BUY_CURRENCIES},
    WITHDRAWALS: "Prelievi",
    OWN_TRANSFER: "Giroconto",
}
LABELS: dict[str, dict[str, str]] = {"en": LABELS_EN, "it": LABELS_IT}
LANGUAGE = "en"  # what the pages and the terminal show
# The key of every label a file may name a built-in category by.
_BY_LABEL: dict[str, str] = {
    label: key for table in (LABELS_IT, LABELS_EN) for key, label in table.items()
}


def slug(name: str) -> str:
    """`name` as a key: lower case, without accents, a `_` for what is not a letter or digit."""
    decomposed = unicodedata.normalize("NFKD", name.casefold())
    plain = "".join(char for char in decomposed if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", "_", plain).strip("_")


def key(name: str) -> str:
    """The key a file or a form means by `name`: a built-in category by its key, its English
    label or its Italian name; any other name by its `slug`, the key a category added with
    that name has. "" for a name without a letter or a digit."""
    return _BY_LABEL.get(name.strip()) or slug(name)


def label(name: str, added: Mapping[str, str] | None = None, language: str = LANGUAGE) -> str:
    """What people see of the key `name`: its label for a built-in category; else the name an
    added category was given, from `added` (key -> name); else the key itself."""
    return LABELS[language].get(name) or (added or {}).get(name, name)


@dataclass(frozen=True, slots=True)
class Labels:
    """The labels of the categories one database or one `categories.yaml` holds: called with a
    key, its label (`label`); "" for no category."""

    added: Mapping[str, str] = field(default_factory=dict)  # key -> the name it was given
    language: str = LANGUAGE

    def __call__(self, name: str | None) -> str:
        return label(name, self.added, self.language) if name else ""

    def written(self, name: str) -> str:
        """How a file the app writes names the key `name`: by its label, which people read,
        unless that label means another key (an added `Groceries` of its own); then by key."""
        shown = self(name)
        return shown if key(shown) == name else name

    @classmethod
    def of(cls, extras: Iterable[Extra]) -> Labels:
        """The labels of the taxonomy and of what `categories.yaml` adds."""
        added = {}
        for extra in extras:
            added[extra.key] = extra.name
            if extra.group:
                added.setdefault(extra.group_key, extra.group)
        return cls(added)


# Every leaf category, in the order rows are written to the sheet.
ORDER: tuple[str, ...] = tuple(
    dict.fromkeys(
        [
            *(leaf for leaves in EXPENSE_GROUPS.values() for leaf in leaves),
            *INCOME_LEAVES,
            *TRANSFERS,
        ]
    )
)
ALL: frozenset[str] = frozenset(ORDER)
GROUPS: frozenset[str] = frozenset(EXPENSE_GROUPS)

FILE = "categories.yaml"  # looked for next to the first rules file
SIDES = ("uscite", "entrate")  # the sides' names in the database and in `Extra`
# How `categories.yaml` names each side, and how it named them before.
_SIDE_NAMES = {"expenses": "uscite", "income": "entrate", "uscite": "uscite", "entrate": "entrate"}


def builtin(side: str, name: str, *, group: bool = False) -> bool:
    """Whether the key `name` is a category of the taxonomy on `side`: a top-level one of the
    expenses with `group`, else one a row can carry."""
    if group:
        return side == "uscite" and name in EXPENSE_GROUPS
    leaves = {
        "uscite": {leaf for leaves in EXPENSE_GROUPS.values() for leaf in leaves},
        "entrate": set(INCOME_LEAVES),
        "transfer": set(TRANSFERS),
    }
    return name in leaves.get(side, set())


@dataclass(frozen=True, slots=True)
class Extra:
    """A leaf category added by `categories.yaml`, by the names written there."""

    name: str
    side: str  # "uscite" | "entrate"
    group: str = ""  # uscite only: the top-level category it sits under

    @property
    def key(self) -> str:
        return key(self.name)

    @property
    def group_key(self) -> str:
        return key(self.group) if self.group else ""


def load(path: Path) -> tuple[Extra, ...]:
    """The categories `path` adds to the taxonomy; none when the file does not exist."""
    uscite, entrate = _read(path)
    return (
        *(Extra(leaf, "uscite", group) for group, leaves in uscite.items() for leaf in leaves),
        *(Extra(name, "entrate") for name in entrate),
    )


def names(extras: tuple[Extra, ...]) -> tuple[str, ...]:
    """The key of every leaf category: the taxonomy in its order, then the added ones."""
    return tuple(dict.fromkeys([*ORDER, *(extra.key for extra in extras)]))


def groups(extras: tuple[Extra, ...]) -> tuple[str, ...]:
    """The key of every top-level category of the expenses."""
    return tuple(
        dict.fromkeys([*EXPENSE_GROUPS, *(e.group_key for e in extras if e.side == "uscite")])
    )


def add(path: Path, extra: Extra) -> None:
    """Save one more category in `path` (created if missing), keeping its leading comments.
    It goes under the group written there that has the key of its own group, if one has."""
    with yamlfiles.LOCK:
        uscite, entrate = _read(path)
        if extra.side == "uscite":
            group = next((g for g in uscite if key(g) == extra.group_key), extra.group)
            leaves = uscite.setdefault(group, [])
        else:
            leaves = entrate
        if extra.key not in map(key, leaves):
            leaves.append(extra.name)
        _write(path, uscite, entrate)


def rename(path: Path, old: str, new: str) -> None:
    """Call the added category of key `old` `new` in `path`, everywhere it is added, and so the
    group of its own it was added with, if it was; the order stays as it was."""
    with yamlfiles.LOCK:
        uscite, entrate = _read(path)

        def swap(name: str) -> str:
            return new if key(name) == old else name

        uscite = {
            group if key(group) in EXPENSE_GROUPS else swap(group): [swap(n) for n in leaves]
            for group, leaves in uscite.items()
        }
        _write(path, uscite, [swap(name) for name in entrate])


def drop(path: Path, name: str) -> None:
    """Take the added category of key `name` out of `path`, and the group it leaves empty."""
    with yamlfiles.LOCK:
        uscite, entrate = _read(path)
        _write(path, _without(uscite, name), [entry for entry in entrate if key(entry) != name])


def move(path: Path, name: str, group: str) -> None:
    """Put the added expense category of key `name` under the top-level category `group` in
    `path` (one written there with its key, else a new one of that name), at its end; the
    group it leaves empty goes."""
    with yamlfiles.LOCK:
        uscite, entrate = _read(path)
        written = next((n for leaves in uscite.values() for n in leaves if key(n) == name), name)
        moved = _without(uscite, name)
        under = next((g for g in moved if key(g) == key(group)), group)
        moved.setdefault(under, []).append(written)
        _write(path, moved, entrate)


def _without(uscite: dict[str, list[str]], name: str) -> dict[str, list[str]]:
    """The groups without the leaf of key `name`; a group it leaves empty is left out."""
    kept = {group: [n for n in leaves if key(n) != name] for group, leaves in uscite.items()}
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
    """The groups of expenses and their leaves, and the income, by the names written."""
    data = (yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else None) or {}
    added = (data.get("categories") or {}) if isinstance(data, dict) else None
    if not isinstance(added, dict) or not added.keys() <= _SIDE_NAMES.keys():
        msg = f"{path}: expected a top-level 'categories' with 'expenses' and 'income'"
        raise ConfigError(msg)
    sides: dict[str, object] = {}
    for name, value in added.items():
        if sides.get(_SIDE_NAMES[name]) and value:
            msg = f"{path}: '{name}' and the other name of that side are both given: keep one"
            raise ConfigError(msg)
        sides[_SIDE_NAMES[name]] = sides.get(_SIDE_NAMES[name]) or value
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
    return {str(group): list(leaves) for group, leaves in uscite.items()}, list(entrate)


def _is_names(value: object) -> TypeGuard[list[str]]:
    return isinstance(value, list) and all(isinstance(name, str) and name for name in value)
