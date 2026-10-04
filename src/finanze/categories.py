"""The agreed two-level category taxonomy (D6). The sheet gets the leaf category."""

from __future__ import annotations

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
