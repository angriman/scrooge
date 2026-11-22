"""Golden-file helpers shared by the extractor tests."""

import json
from dataclasses import asdict
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from scrooge.models import Extraction, PensionCertificate

FIXTURES = Path(__file__).parents[1] / "fixtures"


def jsonable(value: Any) -> Any:
    if isinstance(value, Decimal | date):
        return str(value)
    if isinstance(value, tuple):
        return [jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: jsonable(item) for key, item in value.items()}
    return value


def assert_matches_golden(result: Extraction, source: str) -> None:
    actual = [jsonable(asdict(txn)) for txn in result.transactions]
    expected = json.loads((FIXTURES / source / "expected.json").read_text(encoding="utf-8"))
    assert actual == expected


def assert_certificate_matches_golden(result: PensionCertificate, source: str) -> None:
    expected = json.loads((FIXTURES / source / "expected.json").read_text(encoding="utf-8"))
    assert jsonable(asdict(result)) == expected
