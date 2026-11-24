"""Scrooge database, migration 0011: each category gets a key (decided 2026-11-24).

A category was known by its name, which is also what people read: a new language meant
renaming the data. Now each one has a key (`groceries`, `own_transfer`), which the code, the
rules and the rows go by, and its label is looked up from the key only to be shown
(`categories.LABELS`).

- `categories.key` is new. A category of the built-in taxonomy is found where it is, as 0010
  left it (an expense only under its own group), and gets its key.
- A category someone added keeps its name, which stays its label, and gets the key made of
  that name (`categories.slug`): `Prestiti` becomes `prestiti`. When that key is already one
  on its side and level (the top-level categories of the expenses, or the categories a row
  can carry), `_2`, `_3`... is added. So an added `Groceries` under a group of one's own is
  `groceries_2`, never mistaken for the taxonomy's.
- The name of a built-in category stays as it is and is no longer read: what is shown is its
  label. The names are no longer unique; the keys are, as the names were (side, parent, key).
- `committed_transactions` gives the keys where it gave the names (`category`,
  `category_parent`).

The taxonomy and the slug are copied, not imported, so that this migration stays as it ran.
"""

from __future__ import annotations

import re
import sqlite3
import unicodedata

# The taxonomy as migration 0010 left it: each top-level category of the expenses by its
# English name, then each category a row can carry by its side, its group and its name.
_GROUPS = {
    "Home": "home",
    "Health": "health",
    "Food": "food",
    "Eating out": "eating_out",
    "Transport": "transport",
    "Phone": "phone",
    "Subscriptions": "subscriptions",
    "Personal care": "personal_care",
    "Clothing": "clothing",
    "Shopping": "shopping",
    "Travel and leisure": "travel_and_leisure",
    "Education": "education",
    "Gifts": "gifts",
    "Taxes and paperwork": "taxes_and_paperwork",
    "Banking": "banking",
    "Investments": "investments",
    "Uncategorized": "uncategorized",
}
_LEAVES = {
    ("uscite", "Home", "Rent"): "rent",
    ("uscite", "Home", "Utilities"): "utilities",
    ("uscite", "Home", "Household"): "household",
    ("uscite", "Health", "Health insurance"): "health_insurance",
    ("uscite", "Health", "Dentist"): "dentist",
    ("uscite", "Health", "Doctor/Pharmacy"): "doctor_pharmacy",
    ("uscite", "Food", "Groceries"): "groceries",
    ("uscite", "Eating out", "Eating out"): "eating_out",
    ("uscite", "Transport", "Trains"): "trains",
    ("uscite", "Transport", "Fuel"): "fuel",
    ("uscite", "Transport", "Local transport"): "local_transport",
    ("uscite", "Transport", "Flights"): "flights",
    ("uscite", "Transport", "Parking and tolls"): "parking_and_tolls",
    ("uscite", "Phone", "SIM"): "sim",
    ("uscite", "Subscriptions", "Digital subscriptions"): "digital_subscriptions",
    ("uscite", "Subscriptions", "Revolut Metal"): "revolut_metal",
    ("uscite", "Personal care", "Barber"): "barber",
    ("uscite", "Clothing", "Clothes"): "clothes",
    ("uscite", "Shopping", "Electronics"): "electronics",
    ("uscite", "Shopping", "Other shopping"): "other_shopping",
    ("uscite", "Travel and leisure", "Accommodation"): "accommodation",
    ("uscite", "Travel and leisure", "Activities"): "activities",
    ("uscite", "Travel and leisure", "Sport"): "sport",
    ("uscite", "Education", "Language lessons"): "language_lessons",
    ("uscite", "Education", "Books"): "books",
    ("uscite", "Gifts", "Gifts"): "gifts",
    ("uscite", "Gifts", "Donations"): "donations",
    ("uscite", "Taxes and paperwork", "Taxes"): "taxes",
    ("uscite", "Taxes and paperwork", "Paperwork"): "paperwork",
    ("uscite", "Banking", "Fees"): "fees",
    ("uscite", "Banking", "Withholding tax"): "withholding_tax",
    ("uscite", "Investments", "Trading"): "trading",
    ("uscite", "Investments", "Investments"): "investments",
    ("uscite", "Uncategorized", "Uncategorized"): "uncategorized",
    ("entrate", None, "Salary"): "salary",
    ("entrate", None, "Dividends"): "dividends",
    ("entrate", None, "Interest"): "interest",
    ("entrate", None, "Cashback"): "cashback",
    ("entrate", None, "Refunds"): "refunds",
    ("entrate", None, "Gifts received"): "gifts_received",
    ("entrate", None, "Trading"): "trading",
    ("entrate", None, "Fees"): "fees",
    ("entrate", None, "Investments"): "investments",
    ("entrate", None, "Uncategorized"): "uncategorized",
    ("transfer", None, "Revolut top-up"): "revolut_top_up",
    ("transfer", None, "IBKR top-up"): "ibkr_top_up",
    ("transfer", None, "Buy EUR"): "buy_eur",
    ("transfer", None, "Buy USD"): "buy_usd",
    ("transfer", None, "Withdrawals"): "withdrawals",
    ("transfer", None, "Own transfer"): "own_transfer",
}

_VIEW = """
CREATE VIEW committed_transactions AS
SELECT t.id,
       o.name  AS owner,
       a.name  AS account,
       t.date,
       t.booking_date,
       t.amount_nano,
       t.currency,
       t.description,
       t.counterparty,
       t.kind,
       c.key   AS category,
       p.key   AS category_parent,
       c.side  AS category_side,
       co.name AS counterparty_owner
FROM transactions t
JOIN accounts a   ON a.id = t.account_id
JOIN owners o     ON o.id = a.owner_id
LEFT JOIN statement_files f ON f.id = t.statement_file_id
LEFT JOIN categories c ON c.id = t.category_id
LEFT JOIN categories p ON p.id = c.parent_id
LEFT JOIN owners co    ON co.id = t.counterparty_owner_id
WHERE t.statement_file_id IS NULL OR f.committed_at IS NOT NULL
"""


def _slug(name: str) -> str:
    decomposed = unicodedata.normalize("NFKD", name.casefold())
    plain = "".join(char for char in decomposed if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", "_", plain).strip("_")


def _builtin(side: str, name: str, parent: tuple[str, str, bool] | None) -> str | None:
    """The key of the built-in category of `side` and `name` under `parent` (its side, its
    name and whether it is top-level), if this is one."""
    if parent is None:
        return _GROUPS.get(name) if side == "uscite" else _LEAVES.get((side, None, name))
    group = parent[1] if parent[0] == "uscite" and parent[2] and parent[1] in _GROUPS else ""
    return _LEAVES.get((side, group, name)) if group else None


def migrate(conn: sqlite3.Connection) -> None:
    conn.execute("ALTER TABLE categories ADD COLUMN key TEXT NOT NULL DEFAULT ''")
    rows = conn.execute("SELECT id, side, name, parent_id FROM categories ORDER BY id").fetchall()
    found = {number: (side, name, parent is None) for number, side, name, parent in rows}
    keys: dict[int, str] = {}
    for number, side, name, parent in rows:
        key = _builtin(side, name, None if parent is None else found[parent])
        if key is not None:
            keys[number] = key
    # Where a key must be unique: on one side, among its top-level categories of the expenses
    # or among the categories a row can carry.
    level = {
        number: (side, side == "uscite" and parent is None) for number, side, _, parent in rows
    }
    taken = {(level[number], key) for number, key in keys.items()}
    for number, _, name, _ in rows:
        if number in keys:
            continue
        base = _slug(name) or f"category_{number}"
        key, n = base, 1
        while (level[number], key) in taken:
            n += 1
            key = f"{base}_{n}"
        taken.add((level[number], key))
        keys[number] = key
    conn.executemany(
        "UPDATE categories SET key = ? WHERE id = ?", [(k, n) for n, k in keys.items()]
    )
    conn.execute("DROP INDEX categories_name")
    conn.execute(
        "CREATE UNIQUE INDEX categories_key ON categories (side, coalesce(parent_id, 0), key)"
    )
    conn.execute("DROP VIEW committed_transactions")
    conn.execute(_VIEW)
