"""Duplicate-key detection, for the one rule that needs the raw text.

DS-013 ("no label dimension appears twice") cannot be violated by any parsed
document: a JSON object with a repeated key collapses at parse time, and a
Python ``dict`` cannot hold one key twice. Ruling R-20 keeps the rule - the MCP
boundary genuinely receives raw text, and a submission with a duplicated
``persona`` key silently loses one of the two values - and moves detection to
where the evidence still exists.

:func:`parse_with_duplicate_keys` is that detector. It is a pure function over a
string, so `validation/` stays I/O-free: the caller reads the text, this parses
it, and the pointers it returns are handed to ``validate_dataset`` for DS-013 to
report.

Usage at the tool boundary (M4)::

    document, duplicates = parse_with_duplicate_keys(request_body)
    envelope = validate_dataset(document, resolver, duplicate_keys=duplicates)
"""

import json
from typing import Any

from agentprops.validation.pointers import pointer

__all__ = ["parse_with_duplicate_keys"]


def parse_with_duplicate_keys(text: str) -> tuple[Any, list[str]]:
    """Parse JSON text, returning the document and pointers to duplicated keys.

    The document is what ``json.loads`` would have produced - last value wins,
    exactly as before - so nothing downstream changes. The second element is one
    RFC 6901 pointer per duplicated key, in document order.

    ``json.JSONDecodeError`` propagates: malformed JSON is not a catalogue
    violation, it is a request that never became a document, and the tool
    surface answers it with a parse error rather than a rule id.

    A dict built by the hook is kept alive in ``marked`` alongside its
    duplicates, because the identity of a *discarded* duplicate value could
    otherwise be recycled by the allocator and mark the wrong object.
    """
    marked: dict[int, tuple[dict[str, Any], list[str]]] = {}

    def hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        built: dict[str, Any] = {}
        duplicates: list[str] = []
        for key, value in pairs:
            if key in built and key not in duplicates:
                duplicates.append(key)
            built[key] = value
        if duplicates:
            marked[id(built)] = (built, duplicates)
        return built

    document = json.loads(text, object_pairs_hook=hook)

    pointers: list[str] = []

    def walk(node: Any, path: tuple[str | int, ...]) -> None:
        if isinstance(node, dict):
            entry = marked.get(id(node))
            if entry is not None:
                pointers.extend(pointer(*path, key) for key in entry[1])
            for key, value in node.items():
                walk(value, (*path, key))
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, (*path, index))

    walk(document, ())
    return document, pointers
