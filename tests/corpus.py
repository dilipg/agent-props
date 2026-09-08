"""The rejection corpus, built from the mutation manifest at collection time.

`docs/worked-example.md` section 6: **do not hand-write broken JSON files** -
they rot the moment a schema changes. `tests/fixtures/broken/manifest.json`
holds a declarative case list, and this module applies each case's RFC 6902
patch list to a deep copy of the golden fixture it names.

Three things live here rather than in a test module, because more than one suite
needs them: the patch applier, the resolver stubs, and the case tables.

**The resolver stubs** are ruling R-11's other half. `validation/` reaches the
store through an injected two-method ``Resolver``, so the corpus supplies one:

- :class:`CorpusResolver` knows the golden blueprint as published and knows no
  datasets, which is exactly what the corpus expects - DS-001 passes and DS-031
  fires.
- :data:`BLUEPRINT_RESOLVERS` maps a case's ``resolver`` key to a stub. The
  default for a *blueprint* case knows nothing published, because validating a
  blueprint on its own terms must not fire BP-016; the
  ``blueprint-already-published`` stub is what the BP-016 case selects.

**Two case tables**, because ruling R-20's DS-013 case cannot be a mutation:

- :data:`CASES` - the JSON Patch cases.
- :data:`RAW_TEXT_CASES` - text substitutions applied to the fixture's raw
  bytes, for the rules whose evidence only exists before parsing.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from conftest import FIXTURES_DIR

MANIFEST_PATH = FIXTURES_DIR / "broken" / "manifest.json"
MANIFEST: dict[str, Any] = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

BASE_PATHS: dict[str, Path] = {
    "blueprint": FIXTURES_DIR / MANIFEST["base_blueprint"],
    "dataset": FIXTURES_DIR / MANIFEST["base_dataset"],
}

GOLDEN_BLUEPRINT: dict[str, Any] = json.loads(BASE_PATHS["blueprint"].read_text(encoding="utf-8"))

#: Every dataset fixture the suite treats as clean. `arun-escalated.json` is the
#: one that had never been machine-checked before M2.
GOLDEN_DATASET_PATHS = [
    FIXTURES_DIR / "datasets" / "priya-missing-docs.json",
    FIXTURES_DIR / "datasets" / "arun-escalated.json",
]


class CorpusResolver:
    """The resolver the dataset corpus runs against (ruling R-11).

    Knows the golden blueprint as published, and knows no datasets at all - so
    DS-001 passes on every dataset case and DS-031 fires on the one that
    supersedes a dangling id.

    ``published_blueprints`` is a list rather than a flag so the BP-016 case can
    reuse the same class with the golden blueprint present.
    """

    def __init__(self, published: Sequence[Mapping[str, Any]] = (), datasets: Sequence[str] = ()):
        self._published = {
            (document["agent_id"], document["version"]): document for document in published
        }
        self._datasets = set(datasets)

    def get_published_blueprint(self, agent_id: str, version: str) -> Mapping[str, Any] | None:
        return self._published.get((agent_id, version))

    def dataset_exists(self, dataset_id: str) -> bool:
        return dataset_id in self._datasets


#: What every dataset case gets: the golden blueprint is published, no dataset
#: ids are known.
DATASET_RESOLVER = CorpusResolver(published=[GOLDEN_BLUEPRINT])

#: What every blueprint case gets by default: nothing is published yet, so
#: BP-016 cannot fire. The BP-016 case selects the other one by name.
BLUEPRINT_RESOLVERS: dict[str, CorpusResolver] = {
    "default": CorpusResolver(),
    "blueprint-already-published": CorpusResolver(published=[GOLDEN_BLUEPRINT]),
}


def unescape(token: str) -> str:
    """RFC 6901 reference-token unescaping: ``~1`` -> ``/``, ``~0`` -> ``~``."""
    return token.replace("~1", "/").replace("~0", "~")


def resolve(document: Any, pointer: str) -> tuple[Any, str | int]:
    """Return ``(container, key)`` for an RFC 6901 pointer's final segment."""
    tokens = [unescape(token) for token in pointer.split("/")[1:]]
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


def exists(container: Any, key: str | int) -> bool:
    """Whether ``key`` addresses something that is already there."""
    if isinstance(container, list):
        return isinstance(key, int) and 0 <= key < len(container)
    return key in container


def apply_operation(document: Any, operation: Mapping[str, Any]) -> None:
    """Apply one RFC 6902 operation in place.

    The four operations the manifest uses - ``add``, ``remove``, ``replace``,
    ``copy`` - and no more. A fifth would be a case that could have been written
    with these.

    ``replace`` asserts the target exists, as RFC 6902 requires. Without that,
    a ``replace`` whose path had drifted out of the fixture would silently
    become an ``add``: the exact-set gate catches that in almost every case,
    but not in one whose violation *is* an extra key, which would then pass for
    entirely the wrong reason.
    """
    op = operation["op"]
    value = read(document, operation["from"]) if op == "copy" else operation.get("value")
    container, key = resolve(document, operation["path"])
    if op in {"remove", "replace"} and not exists(container, key):
        raise AssertionError(
            f"{op} targets {operation['path']!r}, which does not exist in the base fixture - "
            "the manifest path has drifted"
        )
    if op == "remove":
        del container[key]
    elif op == "add" and isinstance(container, list):
        container.insert(int(key), copy.deepcopy(value))
    else:
        container[key] = copy.deepcopy(value)


def base_document(target: str) -> dict[str, Any]:
    """A fresh deep copy of a base fixture. Never share one between cases."""
    document: dict[str, Any] = json.loads(BASE_PATHS[target].read_text(encoding="utf-8"))
    return document


def mutated(case: Mapping[str, Any]) -> dict[str, Any]:
    """The document a case describes: base fixture plus its patch list."""
    document = base_document(case["target"])
    for operation in case["mutate"]:
        apply_operation(document, operation)
    return document


def raw_text(case: Mapping[str, Any]) -> str:
    """The raw JSON *text* a ``raw_text_cases`` entry describes.

    Each substitution must match exactly once. A manifest whose anchor text has
    drifted out of the fixture fails loudly here rather than silently producing
    an unmutated document that proves nothing.
    """
    text = BASE_PATHS[case["target"]].read_text(encoding="utf-8")
    for substitution in case["replace"]:
        needle = substitution["find"]
        occurrences = text.count(needle)
        if occurrences != 1:
            raise AssertionError(
                f"{case['id']}: expected exactly one occurrence of {needle!r} in "
                f"{BASE_PATHS[case['target']].name}, found {occurrences}"
            )
        text = text.replace(needle, substitution["with"])
    return text


def parse_raises(case: Mapping[str, Any]) -> bool:
    """Whether the mutated document is expected not to parse as its model.

    True for exactly the cases whose rule is *also* enforced by a strict
    Pydantic annotation (ruling R-23). The rule still fires: the validator runs
    against the raw dict.
    """
    return bool(case.get("parse_raises", False))


CASES: list[dict[str, Any]] = list(MANIFEST["cases"])
RAW_TEXT_CASES: list[dict[str, Any]] = list(MANIFEST["raw_text_cases"])

CASE_IDS: list[str] = [case["id"] for case in CASES]
RAW_TEXT_CASE_IDS: list[str] = [case["id"] for case in RAW_TEXT_CASES]

#: Every rule id any case declares, from the `expect` lists rather than the case
#: names, so a renamed case cannot fake coverage.
COVERED_RULE_IDS: frozenset[str] = frozenset(
    rule for case in CASES + RAW_TEXT_CASES for rule in case["expect"]
)
