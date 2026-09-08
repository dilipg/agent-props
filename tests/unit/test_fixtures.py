"""The committed fixtures exist, parse, and still match the document they came from.

M0 asserted the first two. Ruling R-44 adds the third, and the hole it closes is
worth stating: `docs/worked-example.md` declares itself the source of two of
these files, **and nothing in the suite made that true.** The byte-diff was
performed once, by M0's reviewer, as a manual check that was never committed. So
the fenced blocks and the fixtures could drift silently - and the entire reason
those two fixtures are trusted is that they are byte-exact copies of the spec's
own worked example.

The near-miss that exposed it: `uv run ruff format tests/ docs` reformatted the
fenced Python in both `contracts.md` and `worked-example.md`, because an
explicitly named path overrides `extend-exclude`. It was caught in `git diff`
and reverted. **Run `ruff format` with no path argument.**

This is the same reasoning as the catalogue-drift test in
`test_validation_drift.py`: a document that declares itself the source of truth
for a committed artifact needs a test making that true, or it becomes a document
that merely used to be the source of truth.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Final

import pytest

from conftest import FIXTURES_DIR

WORKED_EXAMPLE: Final[Path] = Path(__file__).parents[2] / "docs" / "worked-example.md"

FIXTURE_FILES = [
    FIXTURES_DIR / "blueprints" / "location-onboarding-1.0.0.json",
    FIXTURES_DIR / "datasets" / "priya-missing-docs.json",
    FIXTURES_DIR / "datasets" / "arun-escalated.json",
    FIXTURES_DIR / "broken" / "manifest.json",
]

#: The fixtures ruling R-44 guards, mapped to the `worked-example.md` heading
#: whose fenced JSON block is their source. **Exactly two**, and the two that
#: are not here are excluded for reasons that are not oversights - see
#: :func:`test_the_drift_guard_covers_exactly_the_two_byte_exact_fixtures`,
#: which is where the exclusions are stated so that the next author neither
#: extends this wrongly nor deletes it as broken.
GUARDED: Final[dict[str, str]] = {
    "blueprints/location-onboarding-1.0.0.json": "## 3.",
    "datasets/priya-missing-docs.json": "## 4.",
}

#: Guarded by nothing, on purpose, with the reason. Read by the test that keeps
#: the two maps exhaustive between them.
UNGUARDED: Final[dict[str, str]] = {
    "broken/manifest.json": (
        "deliberately extended past section 6's 28 cases by ruling R-14, which requires the "
        "corpus to cover every BP-* and DS-* rule - so it is *supposed* to differ from the "
        "document, and a byte comparison would fail on every case M2 added"
    ),
    "datasets/arun-escalated.json": (
        "authored from section 5's prose ('build this as a variant with...'), which carries no "
        "JSON block at all - there is nothing to compare it against. M2's corpus is what "
        "machine-checks it instead: it is asserted clean against the golden blueprint"
    ),
}


def read_lines(path: Path) -> str:
    """``path`` as text, with line endings normalised to LF.

    The one concession in this guard, and it is git's doing rather than a
    softening of the comparison. On a Windows checkout with
    ``core.autocrlf=true`` the worktree copy of `worked-example.md` is CRLF
    while the two golden fixtures are LF - measured, not assumed - even though
    **the index stores all three as LF**, so in the repository they are byte
    identical. Comparing the raw worktree bytes would therefore fail on a fact
    about the checkout, on one platform, for every reader.

    Everything a drift guard is for survives the normalisation: content,
    whitespace, indentation, key order and number formatting are all still
    compared exactly. Only the choice of line terminator - which git owns and
    the specification does not - is excluded.
    """
    return path.read_text(encoding="utf-8")


def fenced_json_blocks(document: Path) -> dict[str, str]:
    """Every ```` ```json ```` block in ``document``, keyed by its section heading.

    Sliced out of the text without re-serialising, so the comparison stays
    literal: ``json.load`` on both sides would compare *values* and pass
    happily while the indentation, key order and number formatting the spec
    fixed had all changed.
    """
    text = read_lines(document)
    blocks: dict[str, str] = {}
    heading = ""
    lines = text.splitlines(keepends=True)
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.startswith("## "):
            heading = line.strip()
        if line.rstrip("\r\n") == "```json":
            body: list[str] = []
            index += 1
            while index < len(lines) and lines[index].rstrip("\r\n") != "```":
                body.append(lines[index])
                index += 1
            blocks.setdefault(heading, "".join(body))
        index += 1
    return blocks


def test_fixture_files_exist() -> None:
    for path in FIXTURE_FILES:
        assert path.is_file(), f"missing fixture: {path}"


def test_fixture_files_parse_as_json() -> None:
    for path in FIXTURE_FILES:
        with path.open(encoding="utf-8") as f:
            document = json.load(f)
        assert isinstance(document, dict), f"expected a JSON object at top level: {path}"


def test_the_worked_example_still_has_the_blocks_the_guard_reads() -> None:
    """Guard against an extractor that silently finds nothing.

    The comparison below would fail loudly on an empty parse, but it would blame
    the fixture. This blames the extractor - or the renamed heading.
    """
    blocks = fenced_json_blocks(WORKED_EXAMPLE)
    assert len(blocks) >= 2, f"found {len(blocks)} fenced JSON blocks in {WORKED_EXAMPLE}"
    for heading in GUARDED.values():
        matching = [found for found in blocks if found.startswith(heading)]
        assert len(matching) == 1, f"expected exactly one {heading!r} section, got {matching}"


@pytest.mark.parametrize("relative", sorted(GUARDED))
def test_the_committed_fixture_is_byte_exact_against_the_worked_example(relative: str) -> None:
    """Ruling R-44's guard. The fixture and the fenced block are the same text.

    Byte for byte, modulo the line-ending translation git applies on checkout -
    :func:`read_lines` carries the measurement and the reasoning.

    If this fails, **update both sides together**: whichever one was edited, the
    other is now wrong, and the reason these two fixtures are trusted at all is
    that they are copies rather than transcriptions. Do not "fix" it by
    regenerating the fixture from the model - that would make the spec's own
    example unverifiable, which is the state ruling R-44 exists to end.
    """
    blocks = fenced_json_blocks(WORKED_EXAMPLE)
    heading = GUARDED[relative]
    fenced = next(body for found, body in blocks.items() if found.startswith(heading))
    committed = read_lines(FIXTURES_DIR / relative)
    assert committed == fenced, (
        f"{relative} has drifted from {WORKED_EXAMPLE.name} section {heading.strip('# .')}. "
        "Update both sides together; the fixture is a byte-exact copy by design (ruling R-44)."
    )


def test_the_drift_guard_covers_exactly_the_two_byte_exact_fixtures() -> None:
    """The exclusions, stated where the next author will read them (ruling R-44).

    Two of the four committed fixtures are **not** byte-compared, and neither is
    an oversight:

    - ``broken/manifest.json`` is deliberately larger than section 6's list.
      Ruling R-14 required the corpus to be extended to full ``BP-*``/``DS-*``
      coverage, so it *must* differ from the document.
    - ``datasets/arun-escalated.json`` was authored from section 5's **prose**.
      There is no JSON block to compare it against.

    Adding either to :data:`GUARDED` will fail, and this assertion is what says
    why before someone tries.
    """
    assert set(GUARDED) | set(UNGUARDED) == {
        str(path.relative_to(FIXTURES_DIR)).replace("\\", "/") for path in FIXTURE_FILES
    }, "a committed fixture is neither guarded nor explicitly excluded"
    assert not set(GUARDED) & set(UNGUARDED)
    assert all(reason for reason in UNGUARDED.values()), "an exclusion needs its reason"
    blocks = fenced_json_blocks(WORKED_EXAMPLE)
    assert not any(found.startswith("## 5.") for found in blocks), (
        "section 5 has gained a JSON block, so arun-escalated.json could now be guarded too"
    )
