"""Fill in categories from `rules.yaml` (step 5 of the flow) and asset tabs from `instruments.yaml`.

Rules are regular expressions matched, case-insensitively, against a transaction's description
and counterparty; the first match wins. Extractors already set the deterministic categories
(Trading, Commissioni, top-ups...), so rules only apply to rows still without one.

A rule that gives a transfer category (Topup Revolut, Giroconto...) to a row the extractor took
for income or an expense also makes it a transfer, which is never income or an expense: the
answers of `--review` need no `kind`.

Two people share the rules about shops (D14), and each has a file of their own for the rows
that name either of them: what a name means depends on whose account it is read on (my wife's
name on my account is a refund, on hers a move between her own accounts). And no rule makes a
transfer of a row that names the other person: money between the two is income or an expense
of each.
"""

from __future__ import annotations

import json
import re
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path

import yaml

from finanze import categories
from finanze.errors import ConfigError
from finanze.models import Holding, Kind, SecurityTrade, Transaction

ASSET_TABS = frozenset({"ETFs", "Stocks", "Bonds"})
_FLOWS = frozenset({Kind.EXPENSE, Kind.INCOME})

Patterns = Sequence[re.Pattern[str]]  # what recognises a person: names, IBANs


class UnknownInstrumentError(Exception):
    """A security trade has no asset tab (D12): the write is blocked until the user maps it."""


@dataclass(frozen=True, slots=True)
class Rule:
    pattern: re.Pattern[str]
    category: str
    kind: Kind | None = None  # overrides the extractor's income/expense guess when set
    account: str = ""  # restricts the rule to one account when set
    direction: str = ""  # "in" or "out": restricts the rule to money received or spent
    # Set by `between_people` on rules giving a transfer category: who the other people are.
    unless: Patterns = ()

    def matches(self, txn: Transaction) -> bool:
        if self.account and self.account != txn.account:
            return False
        if self.direction and self.direction != direction(txn):
            return False
        if self.unless and txn.kind in _FLOWS and names(txn, self.unless):
            return False  # money between two people is not a move between one's own accounts
        # Stripped: statements pad some texts with spaces, and --review saves the name stripped
        # as a whole-text rule, which would otherwise never match the row it was made for.
        return bool(self.pattern.search(f"{txn.description.strip()}\n{txn.counterparty.strip()}"))


@dataclass(frozen=True, slots=True)
class Instrument:
    tab: str = ""  # ETFs | Stocks | Bonds; empty keeps what the statement says
    ticker: str = ""  # name used in the sheet when it differs from the broker's symbol
    maturity: date | None = None  # for bills, when the statement does not give it (Tastytrade)
    price_symbol: str = ""  # where its prices are fetched under another symbol than its own


# Characters YAML refuses even inside quotes; JSON leaves them as they are. Some statements hold
# them (a name encoded twice), and one would make the whole rules file unreadable.
_UNREADABLE = re.compile("[\x7f-\x84\x86-\x9f\ufffe\uffff]")


def direction(txn: Transaction) -> str:
    return "in" if txn.amount > 0 else "out"


def append_rule(
    path: Path, counterparty: str, flow: str, category: str, *, first: bool = False
) -> None:
    """Save a reviewed choice as a rule at the end of `path` (created if missing).

    The rule matches `counterparty` as a whole description or counterparty, not as a substring,
    so it covers the rows the user was shown and nothing else. `first` puts it before the
    rules already in the file instead: the first rule to match wins, so that is where a
    choice that corrects one of them belongs.
    """
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    if "rules:" not in text:
        text += "rules:\n"
    text = text.rstrip().removesuffix("[]").rstrip() + "\n"
    # JSON strings are valid YAML, and quote whatever a merchant name may contain.
    match = _UNREADABLE.sub(
        lambda found: f"\\u{ord(found[0]):04x}",
        json.dumps(f"(?m)^{re.escape(counterparty)}$", ensure_ascii=False),
    )
    name = json.dumps(category, ensure_ascii=False)
    rule = f"  - {{match: {match}, category: {name}, direction: {flow}}}\n"
    head, key, rest = text.partition("rules:\n") if first else (text, "", "")
    path.write_text(head + key + rule + rest, encoding="utf-8")


def _load(path: Path, key: str) -> object:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        msg = f"{path}: expected a mapping with a top-level '{key}' entry"
        raise ConfigError(msg)
    return data.get(key)


def load_rules(paths: Iterable[Path], known: Collection[str] = categories.ALL) -> tuple[Rule, ...]:
    """Read rules from each existing file, in order: earlier files take precedence.

    `known` is every category a rule may name: the taxonomy plus what `categories.yaml` adds.
    """
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
            if category not in known:
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
        instruments[str(symbol)] = Instrument(
            tab,
            str((entry or {}).get("ticker", "")),
            maturity,
            str((entry or {}).get("price_symbol", "")),
        )
    return instruments


PERSONAL = "rules.{owner}.yaml"  # one person's own rules, next to the first rules file


def personal_file(rules_file: Path, owner: str) -> Path:
    """Where the rules of `owner` alone are kept: tried before the rules both people share."""
    return rules_file.with_name(PERSONAL.format(owner=owner.lower()))


def between_people(rules: Iterable[Rule], others: Patterns) -> tuple[Rule, ...]:
    """The rules as they apply to one person's statements when `others` recognise the rest.

    Those giving a transfer category no longer match a row naming one of the others (D14).
    """
    return tuple(
        replace(rule, unless=others) if rule.category in categories.TRANSFERS else rule
        for rule in rules
    )


def names(txn: Transaction, people: Patterns) -> bool:
    """Whether the text of `txn` holds one of the names or IBANs of `people`."""
    text = f"{txn.description}\n{txn.counterparty}"
    return any(pattern.search(text) for pattern in people)


def classify(transactions: Iterable[Transaction], rules: Sequence[Rule]) -> tuple[Transaction, ...]:
    """Give every uncategorized transaction the category of the first rule that matches it."""
    return tuple(_classify(txn, rules) for txn in transactions)


def _classify(txn: Transaction, rules: Sequence[Rule]) -> Transaction:
    if txn.category or txn.kind is Kind.INTERNAL:
        return txn
    for rule in rules:
        if rule.matches(txn):
            return replace(txn, category=rule.category, kind=rule.kind or _kind(txn, rule))
    return txn


def _kind(txn: Transaction, rule: Rule) -> Kind:
    """The extractor's kind, unless the category says its income or expense is a transfer."""
    if rule.category in categories.TRANSFERS and txn.kind in _FLOWS:
        return Kind.TRANSFER
    return txn.kind


def uncategorized(transactions: Iterable[Transaction]) -> tuple[Transaction, ...]:
    """Rows still needing the user's decision; they block the write."""
    return tuple(t for t in transactions if not t.category and t.kind is not Kind.INTERNAL)


def resolve_holding(holding: Holding, instruments: Mapping[str, Instrument]) -> Holding:
    """Apply `instruments.yaml` to a holding; one left without a tab is an error."""
    override = instruments.get(holding.ticker, Instrument())
    tab = override.tab or holding.asset_tab
    if not tab:
        msg = f"{holding.ticker}: no asset tab; add it to instruments.yaml (ETFs/Stocks/Bonds)"
        raise UnknownInstrumentError(msg)
    return replace(holding, asset_tab=tab, ticker=override.ticker or holding.ticker)


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
