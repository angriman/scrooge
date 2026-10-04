"""Fill in categories from `rules.yaml` (step 5 of the flow) and asset tabs from `instruments.yaml`.

Rules are regular expressions matched, case-insensitively, against a transaction's description
and counterparty; the first match wins. Extractors already set the deterministic categories
(Trading, Commissioni, top-ups...), so rules only apply to rows still without one.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path

import yaml

from finanze import categories
from finanze.models import Kind, SecurityTrade, Transaction

ASSET_TABS = frozenset({"ETFs", "Stocks", "Bonds"})


class ConfigError(Exception):
    """rules.yaml or instruments.yaml is malformed."""


class UnknownInstrumentError(Exception):
    """A security trade has no asset tab (D12): the write is blocked until the user maps it."""


@dataclass(frozen=True, slots=True)
class Rule:
    pattern: re.Pattern[str]
    category: str
    kind: Kind | None = None  # overrides the extractor's income/expense guess when set
    account: str = ""  # restricts the rule to one account when set
    direction: str = ""  # "in" or "out": restricts the rule to money received or spent

    def matches(self, txn: Transaction) -> bool:
        if self.account and self.account != txn.account:
            return False
        if self.direction and self.direction != direction(txn):
            return False
        return bool(self.pattern.search(f"{txn.description}\n{txn.counterparty}"))


@dataclass(frozen=True, slots=True)
class Instrument:
    tab: str = ""  # ETFs | Stocks | Bonds; empty keeps what the statement says
    ticker: str = ""  # name used in the sheet when it differs from the broker's symbol
    maturity: date | None = None  # for bills, when the statement does not give it (Tastytrade)


def direction(txn: Transaction) -> str:
    return "in" if txn.amount > 0 else "out"


def append_rule(path: Path, counterparty: str, flow: str, category: str) -> None:
    """Save a reviewed choice as a rule at the end of `path` (created if missing)."""
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    if "rules:" not in text:
        text += "rules:\n"
    text = text.rstrip().removesuffix("[]").rstrip() + "\n"
    # JSON strings are valid YAML, and quote whatever a merchant name may contain.
    match = json.dumps(re.escape(counterparty), ensure_ascii=False)
    name = json.dumps(category, ensure_ascii=False)
    text += f"  - {{match: {match}, category: {name}, direction: {flow}}}\n"
    path.write_text(text, encoding="utf-8")


def _load(path: Path, key: str) -> object:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        msg = f"{path}: expected a mapping with a top-level '{key}' entry"
        raise ConfigError(msg)
    return data.get(key)


def load_rules(paths: Iterable[Path]) -> tuple[Rule, ...]:
    """Read rules from each existing file, in order: earlier files take precedence."""
    rules: list[Rule] = []
    for path in paths:
        if not path.exists():
            continue
        entries = _load(path, "rules") or []
        if not isinstance(entries, list):
            msg = f"{path}: expected a top-level 'rules' list"
            raise ConfigError(msg)
        for n, entry in enumerate(entries, start=1):
            where = f"{path} rule {n}"
            if not isinstance(entry, dict) or not {"match", "category"} <= entry.keys():
                msg = f"{where}: needs 'match' and 'category'"
                raise ConfigError(msg)
            category = str(entry["category"])
            if category not in categories.ALL:
                msg = f"{where}: unknown category {category!r}"
                raise ConfigError(msg)
            try:
                pattern = re.compile(str(entry["match"]), re.IGNORECASE)
                kind = Kind(entry["kind"]) if "kind" in entry else None
            except (re.error, ValueError) as exc:
                msg = f"{where}: {exc}"
                raise ConfigError(msg) from None
            flow = str(entry.get("direction", ""))
            if flow not in {"", "in", "out"}:
                msg = f"{where}: direction must be 'in' or 'out', not {flow!r}"
                raise ConfigError(msg)
            rules.append(Rule(pattern, category, kind, str(entry.get("account", "")), flow))
    return tuple(rules)


def load_instruments(path: Path) -> dict[str, Instrument]:
    if not path.exists():
        return {}
    entries = _load(path, "instruments") or {}
    if not isinstance(entries, dict):
        msg = f"{path}: expected a top-level 'instruments' dict"
        raise ConfigError(msg)
    instruments: dict[str, Instrument] = {}
    for symbol, entry in entries.items():
        tab = str((entry or {}).get("tab", ""))
        if tab and tab not in ASSET_TABS:
            msg = f"{path}: {symbol}: unknown tab {tab!r}"
            raise ConfigError(msg)
        maturity = (entry or {}).get("maturity")
        if maturity is not None and not isinstance(maturity, date):
            msg = f"{path}: {symbol}: maturity must be a date like 2026-12-24"
            raise ConfigError(msg)
        instruments[str(symbol)] = Instrument(tab, str((entry or {}).get("ticker", "")), maturity)
    return instruments


def classify(transactions: Iterable[Transaction], rules: Sequence[Rule]) -> tuple[Transaction, ...]:
    """Give every uncategorized transaction the category of the first rule that matches it."""
    return tuple(_classify(txn, rules) for txn in transactions)


def _classify(txn: Transaction, rules: Sequence[Rule]) -> Transaction:
    if txn.category or txn.kind is Kind.INTERNAL:
        return txn
    for rule in rules:
        if rule.matches(txn):
            return replace(txn, category=rule.category, kind=rule.kind or txn.kind)
    return txn


def uncategorized(transactions: Iterable[Transaction]) -> tuple[Transaction, ...]:
    """Rows still needing the user's decision; they block the write."""
    return tuple(t for t in transactions if not t.category and t.kind is not Kind.INTERNAL)


def resolve_instruments(
    transactions: Iterable[Transaction], instruments: dict[str, Instrument]
) -> tuple[Transaction, ...]:
    """Apply `instruments.yaml` to security trades; a trade left without a tab is an error."""
    resolved: list[Transaction] = []
    for txn in transactions:
        if isinstance(txn, SecurityTrade):
            override = instruments.get(txn.ticker, Instrument())
            tab = override.tab or txn.asset_tab
            if not tab:
                msg = f"{txn.ticker}: no asset tab; add it to instruments.yaml (ETFs/Stocks/Bonds)"
                raise UnknownInstrumentError(msg)
            maturity = txn.maturity or override.maturity
            if tab == "Bonds" and maturity is None:
                msg = f"{txn.ticker}: no maturity date; add 'maturity' to instruments.yaml"
                raise UnknownInstrumentError(msg)
            ticker = override.ticker or txn.ticker
            resolved.append(replace(txn, asset_tab=tab, ticker=ticker, maturity=maturity))
        else:
            resolved.append(txn)
    return tuple(resolved)
