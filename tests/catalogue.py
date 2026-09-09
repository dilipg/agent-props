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


SECTION_4 = re.compile(r"^## 4\. ")
TOOL_NAME = re.compile(r"^`([a-z][a-z0-9_]*)`$")


def parse_tool_names(path: Path | str) -> set[str]:
    """Every tool name section 4 tabulates, so the surface cannot drift from it.

    The same reasoning as :func:`parse_catalogue_ids`, applied to the tool
    contracts: `docs/contracts.md` section 4 is the record of what tools exist
    and `tests/unit/test_tool_surface.py` compares it to what the running
    ``MCPServer`` reports. A tool registered but undocumented, or documented
    with a typo, is then a failing test rather than a discovery at M9.

    Table rows only, and only the first cell, which is the tool name in
    backticks. Rows whose name is followed by a *(phase 1.5)* marker are still
    returned - the phase tag is in the ``Input`` cell, and the surface test's
    deferral map is where a tool's milestone is recorded.
    """
    text = Path(path).read_text(encoding="utf-8")
    names: set[str] = set()
    in_section_4 = False
    for line in text.splitlines():
        if SECTION_4.match(line):
            in_section_4 = True
            continue
        if in_section_4 and NEXT_TOP_SECTION.match(line):
            break
        if not in_section_4 or not line.startswith("|"):
            continue
        match = TOOL_NAME.match(line.split("|")[1].strip())
        if match is not None:
            names.add(match.group(1))
    return names


BOUNDARY_CODE = re.compile(r"^AP-\d{3}$")


def parse_boundary_codes(path: Path | str) -> set[str]:
    """The ``AP-*`` codes section 3.5 tabulates.

    Added at M4 for the same reason :func:`parse_catalogue_ids` exists: the
    codes are documented in one place and defined in another, and only a test
    can stop the two diverging. Table rows only, so prose that mentions a code
    is not mistaken for a row that declares one.
    """
    codes: set[str] = set()
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.startswith("|"):
            continue
        first_cell = line.split("|")[1].strip()
        if BOUNDARY_CODE.match(first_cell):
            codes.add(first_cell)
    return codes


WARNING_CODE = re.compile(r"^`([a-z][a-z0-9_]*)`$")


def parse_runtime_warnings(path: Path | str) -> set[str]:
    """The warning codes section 3.4 tabulates, for the M6 drift guard.

    Added at M6, and the scope needs stating because the vocabulary is
    deliberately **open** (ruling R-22): ``Warning.code`` is a plain string so a
    tool may add a code without a model change, and two already have -
    ``blueprint_version_missing`` at M4 and ``dataset_selection_ambiguous`` at
    M6. Both are documented in section 3.4's *prose* and defined as constants in
    the module that attaches them.

    So this parses the **table** only, and the guard compares it to
    :data:`~agentprops.models.RUNTIME_WARNING_CODES` - the three codes contracts
    3.4 tabulates and the models export. A tool-local addition is out of scope
    by construction, which is what keeps an open vocabulary and a drift guard
    from contradicting each other.

    Scoped to **section 3.4**, unlike the ``AP-*`` and ``RT-*`` parsers above.
    Those two match a distinctive id shape that appears nowhere else in the
    document; a warning code is a backticked lower-snake-case word, which is
    also exactly what section 4's tool-name column looks like. An unscoped
    version of this parser returns every tool name in the document, so the
    section walk is load-bearing rather than tidiness.
    """
    codes: set[str] = set()
    inside = False
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        subsection = SUBSECTION.match(line)
        if subsection is not None:
            inside = subsection.group(1) == "4"
            continue
        if NEXT_TOP_SECTION.match(line):
            inside = False
            continue
        if not inside or not line.startswith("|"):
            continue
        match = WARNING_CODE.match(line.split("|")[1].strip())
        if match is not None:
            codes.add(match.group(1))
    return codes
