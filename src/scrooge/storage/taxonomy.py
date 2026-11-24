"""Change the categories the database holds, for the settings page: rename one, move the rows
of one to another, take out one nothing carries.

A row points to its category by id, so a new name is one row of `categories` and the whole
history follows. The built-in taxonomy (`categories.py`) is never renamed or taken out here:
`seed_categories` would only put it back. The caller runs each in a transaction of its own,
with what it changes in the YAML files.
"""

from __future__ import annotations

import json
import sqlite3

from scrooge import categories

# The categories a row can carry, by name: leaves, never the top-level expense categories.
_LEAVES = (
    "SELECT id, side, parent_id FROM categories "
    "WHERE name = ? AND (side <> 'uscite' OR parent_id IS NOT NULL)"
)


def _groups() -> str:
    return json.dumps(list(categories.USCITE))


def _leaves(conn: sqlite3.Connection, name: str) -> dict[str, int]:
    """Side -> id of every leaf category called `name`."""
    return {row["side"]: int(row["id"]) for row in conn.execute(_LEAVES, (name,))}


def carried(conn: sqlite3.Connection, name: str) -> int:
    """How many rows, staged or committed, carry the category `name`, or keep it as a choice
    made by hand that a new import puts back."""
    ids = json.dumps(list(_leaves(conn, name).values()))
    found = conn.execute(
        "SELECT (SELECT count(*) FROM transactions WHERE category_id IN "
        "(SELECT value FROM json_each(?1))) + (SELECT count(*) FROM overrides WHERE category_id "
        "IN (SELECT value FROM json_each(?1)))",
        (ids,),
    ).fetchone()
    return int(found[0])


def rename(conn: sqlite3.Connection, old: str, new: str) -> int:
    """Call the added category `old` `new`, and so the added group named like it, if any.
    Returns how many rows carry it."""
    rows = carried(conn, old)
    conn.execute(
        "UPDATE categories SET name = ?2 WHERE name = ?1 AND (side <> 'uscite' "
        "OR parent_id IS NOT NULL OR name NOT IN (SELECT value FROM json_each(?3)))",
        (old, new, _groups()),
    )
    return rows


def merge(conn: sqlite3.Connection, source: str, target: str) -> int:
    """Give every row of `source` the category `target` on the same side, which the caller
    made sure it has. Choices made by hand follow. Returns how many rows were moved."""
    into = _leaves(conn, target)
    moved = 0
    for side, number in _leaves(conn, source).items():
        moved += conn.execute(
            "UPDATE transactions SET category_id = ? WHERE category_id = ?", (into[side], number)
        ).rowcount
        conn.execute(
            "UPDATE overrides SET category_id = ? WHERE category_id = ?", (into[side], number)
        )
    return moved


def move(conn: sqlite3.Connection, name: str, group: str) -> int:
    """Put the expense category `name` under the top-level category `group`, made when there
    is none, and take out the added group it leaves empty. Returns how many rows carry it."""
    leaf, before = conn.execute(
        "SELECT id, parent_id FROM categories WHERE side = 'uscite' AND parent_id IS NOT NULL "
        "AND name = ?",
        (name,),
    ).fetchone()
    conn.execute(
        "INSERT OR IGNORE INTO categories (name, parent_id, side, sort_order) "
        "SELECT ?, NULL, 'uscite', coalesce(max(sort_order), 0) + 1 FROM categories",
        (group,),
    )
    (parent,) = conn.execute(
        "SELECT id FROM categories WHERE side = 'uscite' AND parent_id IS NULL AND name = ?",
        (group,),
    ).fetchone()
    conn.execute("UPDATE categories SET parent_id = ? WHERE id = ?", (parent, leaf))
    _drop_if_empty(conn, before)
    return carried(conn, name)


def drop(conn: sqlite3.Connection, name: str) -> None:
    """Take out the added category `name`, which nothing carries, and the added group it
    leaves empty."""
    groups = [row["parent_id"] for row in conn.execute(_LEAVES, (name,)) if row["parent_id"]]
    conn.execute(
        "DELETE FROM categories WHERE name = ? AND (side <> 'uscite' OR parent_id IS NOT NULL)",
        (name,),
    )
    for group in groups:
        _drop_if_empty(conn, group)


def _drop_if_empty(conn: sqlite3.Connection, group: int) -> None:
    """Take out the top-level category `group` when it was added and has nothing under it."""
    conn.execute(
        "DELETE FROM categories WHERE id = ? AND name NOT IN (SELECT value FROM json_each(?)) "
        "AND NOT EXISTS (SELECT 1 FROM categories c WHERE c.parent_id = categories.id)",
        (group, _groups()),
    )
