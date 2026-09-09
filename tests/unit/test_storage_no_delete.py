"""Ground rule 6, enforced twice: the Protocol has no delete, and no code issues one.

`docs/contracts.md` section 6 asks for the first half by name: "**There is no
``delete_*`` method on the Protocol.** That is deliberate and enforced by a test
that asserts the Protocol has no method whose name starts with ``delete``."

M3's acceptance criterion asks for more than that - "confirms no code path
deletes a row" - which the Protocol's shape alone cannot establish, because a
``set_archived`` that flipped the flag by deleting and re-inserting the row
would satisfy the shape perfectly. So the second half reads the AST of every
module in `storage/`, including the Alembic package, and asserts that none of
them constructs a ``DELETE``.

M7 grew both halves rather than adding a third: the call set learned pymongo's
spellings, because ``delete_one`` is not ``delete`` and the old set would have
said nothing about the new adapter; and the adapter check became parameterised
over both adapters.

Both halves are static, so neither depends on a backend being available.
Everything is checked against the parsed AST rather than by grepping text, so
docstrings that discuss deletion - and these do - cannot trip it.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import agentprops.storage
from agentprops.storage import Store
from agentprops.storage.mongo import MongoStore
from agentprops.storage.sql import SqlStore

STORAGE_DIR = Path(agentprops.storage.__file__).resolve().parent
STORAGE_FILES = sorted(STORAGE_DIR.rglob("*.py"))

#: Every way SQLAlchemy Core, the ORM, Alembic **or pymongo** can express row
#: deletion.
#:
#: ``sqlalchemy.delete(t)``, ``table.delete()``, ``session.delete(obj)``,
#: ``query.delete()`` and Alembic's ``op.bulk_delete`` are all spelled as a call
#: whose final attribute is one of these. ``drop_table`` is deliberately absent:
#: a migration's ``downgrade`` tearing down a throwaway database is DDL, not the
#: destruction of a row anyone authored.
#:
#: **The pymongo spellings were added at M7, and not one of them is ``delete``.**
#: ``delete_one``, ``delete_many``, ``find_one_and_delete``, ``drop`` and
#: ``drop_database`` are what a document store calls it, so the set that guarded
#: `sql.py` perfectly would have passed over an adapter that emptied a
#: collection on every write. That is the shape of guard failure this build has
#: now named three times - a check that is precise about the code it was written
#: against and silent about the code that arrives next - so the set grew with
#: the adapter rather than after it.
DELETING_CALLS = frozenset(
    {
        "delete",
        "delete_all",
        "bulk_delete",
        "truncate",
        "delete_one",
        "delete_many",
        "find_one_and_delete",
        "drop",
        "drop_database",
        "drop_collection",
        "remove",
    }
)

#: Raw SQL is the other route in, and it would not look like a call at all.
DELETING_SQL = ("delete from", "truncate table")


def parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def dotted(node: ast.expr) -> str:
    """``table.delete`` from an ``Attribute``/``Name`` chain, best effort."""
    parts: list[str] = []
    current: ast.expr = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    return ".".join(reversed(parts))


def test_the_storage_file_table_is_not_empty() -> None:
    assert len(STORAGE_FILES) > 3, f"no storage modules found under {STORAGE_DIR}"


def test_the_protocol_declares_no_delete_method() -> None:
    """Contracts section 6's own requirement, verbatim."""
    offenders = [name for name in dir(Store) if name.startswith("delete")]
    assert offenders == [], f"Store declares a delete method: {offenders}"


@pytest.mark.parametrize("adapter", [SqlStore, MongoStore], ids=lambda a: a.__name__)
def test_no_adapter_declares_a_delete_method(adapter: type) -> None:
    """The Protocol is a minimum, so an adapter could add one. Neither does.

    Parameterised over both adapters from M7, because "the adapter" stopped
    being singular - and a guard naming one class says nothing about the other.
    """
    offenders = [name for name in dir(adapter) if name.startswith("delete")]
    assert offenders == [], f"{adapter.__name__} declares a delete method: {offenders}"


@pytest.mark.parametrize("path", STORAGE_FILES, ids=lambda p: p.name)
def test_no_storage_module_issues_a_delete(path: Path) -> None:
    """No ``delete()`` call anywhere in `storage/`, migrations included.

    Archive is a flag and a dataset edit is copy-on-write, so there is nothing
    for a ``DELETE`` to do. The one write that touches an existing dataset row
    is ``set_archived``, and it is an ``UPDATE``.
    """
    offences: list[str] = []
    for node in ast.walk(parse(path)):
        if not isinstance(node, ast.Call):
            continue
        name = dotted(node.func)
        if name and name.rsplit(".", 1)[-1] in DELETING_CALLS:
            offences.append(f"line {node.lineno}: {name}()")
    assert not offences, f"{path.name} deletes rows: {offences}"


@pytest.mark.parametrize("path", STORAGE_FILES, ids=lambda p: p.name)
def test_no_storage_module_carries_delete_sql(path: Path) -> None:
    """Raw SQL is the route the call check cannot see.

    Checked against string literals in the AST, with docstrings excluded, so the
    prose in these modules - which discusses ``DELETE`` at length, and has to -
    is not what is being searched. Excluding them by AST position rather than by
    content is the difference between a guard and a guess.
    """
    tree = parse(path)
    prose = {
        id(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
    }
    offences: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if id(node) in prose:
            continue
        lowered = node.value.lower()
        offences.extend(
            f"line {node.lineno}: {fragment!r}" for fragment in DELETING_SQL if fragment in lowered
        )
    assert not offences, f"{path.name} carries deleting SQL: {offences}"
