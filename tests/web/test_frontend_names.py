"""The frontend names what it colours as the services name it (frontend/src/lib/design/data.ts).

A type of asset or a group of income renamed here and not there would quietly lose its colour.
"""

import re
from pathlib import Path

from scrooge import income, networth

DATA = Path(__file__).parents[2] / "frontend" / "src" / "lib" / "design" / "data.ts"


def _keys(table: str) -> set[str]:
    found = re.search(rf"export const {table}\b[^=]*= \{{(.*?)\}};", DATA.read_text(), re.DOTALL)
    assert found, f"{table} is not in {DATA.name}"
    return {key.strip("'\"") for key in re.findall(r"^\s*('[^']+'|\w+):", found[1], re.MULTILINE)}


def test_every_type_of_asset_has_its_colour() -> None:
    # A card, only ever owed, may wear the colour of a loss instead of a slot of its own.
    assert set(networth.ASSETS) - {networth.CARD} <= _keys("ASSET_SLOTS") <= set(networth.ASSETS)


def test_every_group_of_income_has_its_colour() -> None:
    assert _keys("INCOME_SLOTS") == set(income.GROUPS)
