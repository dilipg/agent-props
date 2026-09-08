"""Every rejection-corpus mutation must still *parse*. This is ruling R-04.

M2's gate is that each broken case comes back with exactly the rule ids its
manifest entry declares. That is only reachable if the document gets as far as
the validator, so a mutation that trips a model constraint instead of a rule
silently costs M2 a rule id and there is nothing in `models/` to notice.

So this suite applies `tests/fixtures/broken/manifest.json` to the golden
fixtures and asserts each of the 28 mutated documents still parses - and still
round-trips, since M2 hands the parsed model to the rules and reads pointers off
the original document.

Failures here are *this* package's bug, not the corpus's. A case that will not
parse means a model is enforcing something the catalogue owns.

The patch applier below is a deliberate minimum: the four RFC 6902 operations
the manifest actually uses, and nothing more. M2 builds the real corpus loader;
duplicating thirty lines is cheaper than either package importing the other's
test helpers.
"""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest

from agentprops.models import Blueprint, Dataset
from conftest import FIXTURES_DIR

MANIFEST = json.loads((FIXTURES_DIR / "broken" / "manifest.json").read_text(encoding="utf-8"))

MODELS = {"blueprint": Blueprint, "dataset": Dataset}
BASES = {
    "blueprint": MANIFEST["base_blueprint"],
    "dataset": MANIFEST["base_dataset"],
}

CASES = [(case["id"], case) for case in MANIFEST["cases"]]


def unescape(token: str) -> str:
    return token.replace("~1", "/").replace("~0", "~")


def resolve(document: Any, pointer: str) -> tuple[Any, str | int]:
    """Return ``(container, key)`` for an RFC 6901 pointer's final segment."""
    tokens = [unescape(t) for t in pointer.split("/")[1:]]
    container = document
    for token in tokens[:-1]:
        container = container[int(token)] if isinstance(container, list) else container[token]
    last = tokens[-1]
    if isinstance(container, list):
        return container, len(container) if last == "-" else int(last)
    return container, last


def read(document: Any, pointer: str) -> Any:
    container, key = resolve(document, pointer)
    return container[key]


def apply_operation(document: Any, operation: dict[str, Any]) -> None:
    op = operation["op"]
    value = read(document, operation["from"]) if op == "copy" else operation.get("value")
    container, key = resolve(document, operation["path"])
    if op == "remove":
        del container[key]
    elif op == "add" and isinstance(container, list):
        container.insert(int(key), copy.deepcopy(value))
    else:
        container[key] = copy.deepcopy(value)


def mutated(case: dict[str, Any]) -> tuple[type[Blueprint] | type[Dataset], dict[str, Any]]:
    target = case["target"]
    base = json.loads((FIXTURES_DIR / BASES[target]).read_text(encoding="utf-8"))
    for operation in case["mutate"]:
        apply_operation(base, operation)
    return MODELS[target], base


def test_the_corpus_manifest_is_not_empty() -> None:
    assert len(CASES) >= 28, f"expected the shipped corpus, found {len(CASES)} cases"


@pytest.mark.parametrize(("case_id", "case"), CASES, ids=[case_id for case_id, _ in CASES])
def test_broken_case_still_parses(case_id: str, case: dict[str, Any]) -> None:
    """No mutation may raise ``ValidationError``. Every one must reach the rules."""
    model, document = mutated(case)
    parsed = model.model_validate(document)
    assert parsed is not None, case_id


@pytest.mark.parametrize(("case_id", "case"), CASES, ids=[case_id for case_id, _ in CASES])
def test_broken_case_still_round_trips(case_id: str, case: dict[str, Any]) -> None:
    """And no mutation may be silently normalised away on the way back out."""
    model, document = mutated(case)
    dumped = model.model_validate(document).model_dump(mode="json", exclude_unset=True)
    assert dumped == document, case_id


def test_the_mutations_actually_changed_something() -> None:
    """Guard against a patch applier that quietly no-ops.

    ``DS-008-constant-entity-drift`` is a known exception: ruling R-14 records
    that its first operation replaces a value with itself, and one of its two
    operations is therefore a genuine no-op. The case as a whole still changes
    the document, which is all this asserts.
    """
    unchanged: list[str] = []
    for case_id, case in CASES:
        target = case["target"]
        base = json.loads((FIXTURES_DIR / BASES[target]).read_text(encoding="utf-8"))
        _, after = mutated(case)
        if after == base:
            unchanged.append(case_id)
    assert not unchanged, f"these cases mutate nothing, so they prove nothing: {unchanged}"
