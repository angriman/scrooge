"""The agreed two-level category taxonomy (D6). The sheet gets the leaf category.

The taxonomy below is the base. `categories.yaml` adds to it (never removes or renames):

    categories:
      uscite:
        Prestiti: [Prestiti]   # top-level category: its leaves
      entrate: [Prestiti]
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import takewhile
from pathlib import Path

import yaml

from finanze.errors import ConfigError

USCITE: dict[str, tuple[str, ...]] = {
    "Casa": ("Affitto", "Utenze", "Arredamento e casa"),
    "Salute": ("Assicurazione sanitaria", "Dentista", "Medico/Farmacia"),
    "Alimentari": ("Spesa",),
    "Ristoranti e bar": ("Ristoranti",),
    "Trasporti": ("Treni", "Carburante", "Trasporti locali", "Voli", "Parcheggi e pedaggi"),
    "Telefonia": ("SIM",),
    "Abbonamenti": ("Abbonamenti digitali", "Revolut Metal"),
    "Cura personale": ("Barbiere",),
    "Abbigliamento": ("Vestiti",),
    "Acquisti": ("Elettronica", "Acquisti vari"),
    "Viaggi e svago": ("Alloggi", "Attività/Svago", "Sport"),
    "Formazione": ("Lezioni di lingua", "Libri"),
    "Regali": ("Regali", "Donazioni"),
    "Tasse e burocrazia": ("Tasse", "Documenti e burocrazia"),
    "Banca e broker": ("Commissioni", "Ritenute"),
    "Investimenti": ("Trading", "Investimenti"),
}
ENTRATE: tuple[str, ...] = (
    "Stipendio",
    "Dividendi",
    "Interessi",
    "Cashback",
    "Rimborsi",
    "Regali ricevuti",
    "Trading",
    "Commissioni",
    "Investimenti",
)
TRANSFERS: tuple[str, ...] = (
    "Topup Revolut",
    "Topup IBKR",
    "Acquisto EUR",
    "Acquisto USD",
    "Prelievi",
)

# Every leaf category, in the order rows are written to the sheet.
ORDER: tuple[str, ...] = tuple(
    dict.fromkeys([*(leaf for leaves in USCITE.values() for leaf in leaves), *ENTRATE, *TRANSFERS])
)
ALL: frozenset[str] = frozenset(ORDER)

FILE = "categories.yaml"  # looked for next to the first rules file
SIDES = ("uscite", "entrate")


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
    uscite, entrate = _read(path)
    leaves = uscite.setdefault(extra.group, []) if extra.side == "uscite" else entrate
    if extra.name not in leaves:
        leaves.append(extra.name)
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    header = "".join(takewhile(lambda line: line.startswith("#"), text.splitlines(keepends=True)))
    body = yaml.safe_dump(
        {"categories": {"uscite": uscite, "entrate": entrate}},
        allow_unicode=True,
        sort_keys=False,
    )
    path.write_text(header + body, encoding="utf-8")


def _read(path: Path) -> tuple[dict[str, list[str]], list[str]]:
    data = (yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else None) or {}
    added = (data.get("categories") or {}) if isinstance(data, dict) else None
    if not isinstance(added, dict) or not added.keys() <= set(SIDES):
        msg = f"{path}: expected a top-level 'categories' with 'uscite' and 'entrate'"
        raise ConfigError(msg)
    uscite = added.get("uscite") or {}
    entrate = added.get("entrate") or []
    if (
        not isinstance(uscite, dict)
        or not all(_is_names(leaves) for leaves in uscite.values())
        or not _is_names(entrate)
    ):
        msg = (
            f"{path}: 'uscite' maps each top-level category to a list of names, "
            "'entrate' is a list of names"
        )
        raise ConfigError(msg)
    return {str(group): list(leaves) for group, leaves in uscite.items()}, list(entrate)


def _is_names(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(name, str) and name for name in value)
