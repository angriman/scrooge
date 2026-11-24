"""`scrooge migrate-files`: write the user's YAML files again with today's names, once.

The rules files, `categories.yaml` and the config may name a category by the Italian name it
had until 2026-11-24 (`categories.legacy`), and `categories.yaml` may call its sides `uscite`
and `entrate` (`categories.OLD_SIDES`). The app still reads them so, at every load. This
writes each such name as the app writes it today (`Labels.written`), so that one day it no
longer has to read the old ones.

Only those names change. A file is never dumped again: each name is replaced where YAML says
it begins and ends, so its comments, order, blank lines and quotes stay as they were. The new
text is parsed again and must mean the same categories as before, else the file is refused.
Before a file is written, it is kept as it was next to itself (`rules.yaml.before-migrate-
files`, never over an older copy). A second run finds nothing to change.
"""

from __future__ import annotations

import shutil
import stat
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from itertools import count
from pathlib import Path
from typing import Literal, TextIO

import yaml
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

from scrooge import categories, classification, yamlfiles
from scrooge.errors import ConfigError

Kind = Literal["rules", "categories", "config"]  # what a file is, and so where it names categories
KEPT = ".before-migrate-files"  # the copy of a file as it was, next to it
_STRING = "tag:yaml.org,2002:str"
_FLOW = frozenset(",[]{}#:")  # what a plain name must not hold, in a flow collection or not


class RefusedError(ConfigError):
    """A file that cannot be written again without changing what it means."""


@dataclass(frozen=True, slots=True)
class Change:
    """One name written again: on which line, and as what."""

    line: int
    old: str
    new: str


@dataclass(frozen=True, slots=True)
class Plan:
    """What one file would become."""

    path: Path
    text: str  # as it is
    new: str  # as it would be written
    changes: tuple[Change, ...]


@dataclass(frozen=True, slots=True)
class _Edit:
    start: int
    end: int
    text: str  # what goes between `start` and `end`
    change: Change


def plan(path: Path, kind: Kind, labels: categories.Labels) -> Plan | None:
    """What `path` would become with today's names; None when there is no such file. A file
    that would mean other categories once written so raises `RefusedError`."""
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    edits = sorted(_edits(_compose(text, path), kind, labels, path), key=lambda e: e.start)
    new = text
    for edit in reversed(edits):  # from the end, so that the places before stay right
        new = new[: edit.start] + edit.text + new[edit.end :]
    if edits:
        _check(text, new, kind, labels, path)
    return Plan(path, text, new, tuple(edit.change for edit in edits))


def write(found: Plan) -> Path:
    """Write `found` in its file, kept first as it was; where the copy was kept. A file changed
    since it was read is refused."""
    with yamlfiles.LOCK:
        path = found.path
        if path.read_text(encoding="utf-8") != found.text:
            msg = f"{path}: changed while it was read: run again"
            raise RefusedError(msg)
        kept = kept_as(path)
        shutil.copy2(path, kept)
        yamlfiles.write(path, found.new)
        path.chmod(stat.S_IMODE(kept.stat().st_mode))  # readable by whom it was before
    return kept


def kept_as(path: Path) -> Path:
    """Where the copy of `path` goes: `<name>.before-migrate-files`, else the first of `.2`,
    `.3`, ... that is free."""
    first = path.with_name(path.name + KEPT)
    others = (first.with_name(f"{first.name}.{n}") for n in count(2))
    return first if not first.exists() else next(name for name in others if not name.exists())


def _compose(text: str, path: Path) -> Node | None:
    try:
        root: Node | None = yaml.compose(text, Loader=yaml.SafeLoader)
    except yaml.YAMLError as exc:
        msg = f"{path}: {exc}"
        raise RefusedError(msg) from None
    return root


def _edits(root: Node | None, kind: Kind, labels: categories.Labels, path: Path) -> list[_Edit]:
    """Where `root` names a category by an old name, and what it is written as today."""
    found: dict[int, _Edit] = {}  # by where it starts: an alias is the node it names, once

    def name(node: Node | None) -> None:
        if isinstance(node, ScalarNode) and node.tag == _STRING and categories.legacy(node.value):
            _add(found, node, labels.written(categories.key(node.value)))

    if kind == "rules":
        listed = _value(root, "rules")
        for rule in listed.value if isinstance(listed, SequenceNode) else ():
            name(_value(rule, "category"))
    elif kind == "config":
        name(_value(root, "unmatched"))
    else:
        added = _value(root, "categories")
        if isinstance(added, MappingNode):
            for side, value in added.value:
                _side(found, added, side, path)
                _each_name(value, name)
    return list(found.values())


def _side(found: dict[int, _Edit], added: MappingNode, side: Node, path: Path) -> None:
    """The side `side` of `categories.yaml` by today's name, unless the file has that too."""
    if not isinstance(side, ScalarNode) or side.value not in categories.OLD_SIDES:
        return
    new = categories.OLD_SIDES[side.value]
    if _value(added, new) is not None:
        msg = (
            f"{path}: both '{side.value}' and '{new}' are given: "
            f"move what '{side.value}' lists under '{new}' by hand"
        )
        raise RefusedError(msg)
    _add(found, side, new)


def _each_name(value: Node, name: Callable[[Node], None]) -> None:
    """Call `name` on every category a side of `categories.yaml` names: the top-level ones of
    the expenses and every leaf."""
    if isinstance(value, MappingNode):
        for group, leaves in value.value:
            name(group)
            _each_name(leaves, name)
    elif isinstance(value, SequenceNode):
        for leaf in value.value:
            name(leaf)


def _add(found: dict[int, _Edit], node: ScalarNode, new: str) -> None:
    start, end = node.start_mark.index, node.end_mark.index
    change = Change(node.start_mark.line + 1, node.value, new)
    found[start] = _Edit(start, end, _scalar(node, new), change)


def _scalar(node: ScalarNode, text: str) -> str:
    """`text` as YAML, quoted as `node` was, or plain when it was and can stay so."""
    if node.style == "'":
        return "'" + text.replace("'", "''") + "'"
    if node.style is None and not _FLOW & set(text) and yaml.safe_load(text) == text:
        return text
    return classification.quoted(text)


def _value(root: Node | None, key: str) -> Node | None:
    if not isinstance(root, MappingNode):
        return None
    return next(
        (value for name, value in root.value if isinstance(name, ScalarNode) and name.value == key),
        None,
    )


def _check(text: str, new: str, kind: Kind, labels: categories.Labels, path: Path) -> None:
    """Refuse `new` unless it means what `text` meant and names nothing by an old name."""
    try:
        same = _meaning(new, kind, path) == _meaning(text, kind, path)
        same = same and not _edits(_compose(new, path), kind, labels, path)
    except (yaml.YAMLError, ConfigError):  # what the new text breaks: an anchor, a block
        same = False
    if not same:
        msg = (
            f"{path}: today's names cannot be written in it without changing what it says: "
            "change them by hand"
        )
        raise RefusedError(msg)


def _meaning(text: str, kind: Kind, path: Path) -> object:
    """The parsed `text` with each category it names by its key."""
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        return data
    if kind == "categories":
        added = categories.parse(text, path)
        return {**data, "categories": [(e.side, e.group_key, e.key) for e in added]}
    if kind == "config":
        named = data.get("unmatched")
        return {**data, "unmatched": categories.key(named)} if isinstance(named, str) else data
    rules = data.get("rules")
    if not isinstance(rules, list):
        return data
    return {**data, "rules": [_keyed(rule) for rule in rules]}


def _keyed(rule: object) -> object:
    if not isinstance(rule, dict) or not isinstance(rule.get("category"), str):
        return rule
    return {**rule, "category": categories.key(rule["category"])}


def files(rules: Iterable[Path], people: Iterable[str], config: Path) -> list[tuple[Path, Kind]]:
    """The files the app reads, each once: each person's own rules file, the rules files
    (the first being the one both people share), the `categories.yaml` next to it and the
    config."""
    listed = list(rules)
    shared = listed[0]
    found: list[tuple[Path, Kind]] = [
        *((classification.personal_file(shared, person), "rules") for person in people),
        *((path, "rules") for path in listed),
        (shared.with_name(categories.FILE), "categories"),
        (config, "config"),
    ]
    seen: set[Path] = set()
    unique: list[tuple[Path, Kind]] = []
    for path, kind in found:
        if path.resolve() not in seen:
            seen.add(path.resolve())
            unique.append((path, kind))
    return unique


def run(found: Iterable[tuple[Path, Kind]], out: TextIO, *, apply: bool) -> int:
    """Say what each file would become, and with `apply` write it; 1 if a file was refused."""
    places = list(found)
    added = next(path for path, kind in places if kind == "categories")
    labels = categories.Labels.of(categories.load(added))
    refused = changed = 0
    for path, kind in places:
        try:
            found_plan = plan(path, kind, labels)
            if found_plan is None:
                out.write(f"{path}: not found, skipped\n")
                continue
            if not found_plan.changes:
                out.write(f"{path}: nothing to change\n")
                continue
            changed += 1
            out.write(f"{path}:\n")
            out.writelines(f"  {c.line}: {c.old} -> {c.new}\n" for c in found_plan.changes)
            if apply:
                out.write(f"  written; as it was: {write(found_plan).name}\n")
        except RefusedError as exc:
            refused += 1
            out.write(f"error: {exc}\n")
    if changed and not apply:
        out.write("Nothing was written: run again with --write to write it.\n")
    elif not changed and not refused:
        out.write("Every file has today's names already.\n")
    return 1 if refused else 0
