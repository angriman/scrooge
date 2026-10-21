from pathlib import Path

import pytest

from tests.web.builders import Site


@pytest.fixture
def site(folder: Path) -> Site:
    """The app over an empty database, with the import page, over the fixture statements."""
    made = Site(folder)
    made.client = made.app(imports=made.setup)
    return made
