"""Read the rule ids out of `docs/contracts.md`, so the catalogue can be diffed.

`docs/contracts.md` section 3 is the single catalogue of record (ruling R-25).
This parser is what lets a test compare it to `validation/registry.py`, which is
the only thing standing between the prose and the code drifting apart.

Scope, per ruling R-12: **sections 3.1 to 3.3 only**, ids matching
``^(BP|DS|SK)-\\d{3}$``. Section 3.4 is a response-code table - `fetch_step`
response codes with no document to validate, no JSON pointer and no possible
broken fixture - and the document now says so under its own section 3 preamble.

The caller passes the path. `worked-example.md`'s snippet says
``parse_catalogue_ids("contracts.md")``, which resolves against the CWD to a
file that does not exist; ruling R-22 has the test resolve it from its own
module instead, so the one test that prevents catalogue drift compares rather
than dying of ``FileNotFoundError``.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

RULE_ID = re.compile(r"^(BP|DS|SK)-\d{3}$")
SECTION_3 = re.compile(r"^## 3\. ")
SUBSECTION = re.compile(r"^### 3\.(\d+)")
NEXT_TOP_SECTION = re.compile(r"^## \d+\. ")

#: The subsections that hold rules. 3.4 is runtime response codes.
RULE_SUBSECTIONS: Final[frozenset[str]] = frozenset({"1", "2", "3"})


def parse_catalogue_ids(path: Path | str) -> set[str]:
    """Every rule id documented in sections 3.1 to 3.3 of a contracts document."""
    text = Path(path).read_text(encoding="utf-8")
    found: set[str] = set()
    in_section_3 = False
    in_rule_subsection = False
    for line in text.splitlines():
        if SECTION_3.match(line):
            in_section_3 = True
            in_rule_subsection = False
            continue
        if in_section_3 and NEXT_TOP_SECTION.match(line):
            break
        subsection = SUBSECTION.match(line)
        if subsection is not None:
            in_rule_subsection = subsection.group(1) in RULE_SUBSECTIONS
            continue
        if not (in_section_3 and in_rule_subsection) or not line.startswith("|"):
            continue
        first_cell = line.split("|")[1].strip()
        if RULE_ID.match(first_cell):
            found.add(first_cell)
    return found


RUNTIME_CODE = re.compile(r"^RT-E\d{2}$")


def parse_runtime_codes(path: Path | str) -> set[str]:
    """The ``RT-*`` codes section 3.4 *tabulates*, for the test that pins scope.

    Table rows only, so prose that mentions a code - and the document now
    explains in prose why there is no RT-E05 - is not mistaken for a row that
    declares one.
    """
    codes: set[str] = set()
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.startswith("|"):
            continue
        first_cell = line.split("|")[1].strip()
        if RUNTIME_CODE.match(first_cell):
            codes.add(first_cell)
    return codes
