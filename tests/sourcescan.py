"""One AST walk: every string literal a test passes to a named call.

The mechanism behind three coverage guards - one per registered surface. M4's
tool guard is the original, and its durable property is that it enumerates the
**registered** surface and asks whether each entry appears in a call somewhere
under `tests/`, rather than comparing two hand-kept lists. M9.5's prompt and
resource guards need exactly the same walk over a different set of function
names, so the walk lives here and is called three times.

Why here rather than copied into the second guard
-------------------------------------------------

Two copies of a scanner is two things to keep in step, and a guard that has
silently stopped matching is the one failure this whole family of tests exists
to prevent - a scanner that finds nothing makes every coverage assertion pass
vacuously. Each caller therefore keeps its own *non-vacuity* control (the
"finds something" test and the "does not credit a name nobody calls" test),
because those are claims about that caller's surface; only the walk is shared.

Parsed rather than imported, for the reason `test_validation_drift.py` gives: a
name is checked against the source, so a test that exists but is skipped or
renamed is all visible, and no import order matters.

**Positional arguments only.** ``client.get_prompt("author-a-blueprint", {})``
counts; ``call_tool(name=variable)`` does not, and that is deliberate - a
keyword or a variable is a name the scan cannot verify, so crediting it would be
crediting a guess.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable
from pathlib import Path

__all__ = ["literals_passed_to"]


def literals_passed_to(directory: Path, function_names: Iterable[str]) -> set[str]:
    """Every string literal passed positionally to one of ``function_names``.

    Matches on the call's *final* attribute or its bare name, so
    ``client.get_prompt(...)``, ``self.get_prompt(...)`` and ``get_prompt(...)``
    are all the same call to this walk. A test reaching a surface through a new
    spelling therefore needs that spelling added to its guard's name set -
    which is a visible decision rather than a silent gap.
    """
    wanted = frozenset(function_names)
    found: set[str] = set()
    for path in sorted(directory.rglob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            function = node.func
            name = function.attr if isinstance(function, ast.Attribute) else None
            if name is None and isinstance(function, ast.Name):
                name = function.id
            if name not in wanted:
                continue
            found.update(
                argument.value
                for argument in node.args
                if isinstance(argument, ast.Constant) and isinstance(argument.value, str)
            )
    return found
