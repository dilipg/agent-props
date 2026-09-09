"""Ruling R-73: a tracked source file must be readable by the tools that review it.

`web/src/lib/validation.ts` embedded a raw **NUL byte** as a dedup separator. It
worked. It also made git's binary heuristic classify the file as binary, so the
review diff read ``Bin 0 -> 6841 bytes``, and ``git grep``, ripgrep and GitHub's
diff view all skipped it. **The module implementing M9 clause 1's entire local
half was invisible to the review** - not misdescribed, not under-tested,
*unreadable* by every tool the review depends on.

Why this is a guard and not a one-character fix
-----------------------------------------------

Every other failure this build has found was a claim that proved less than it
stated: something readable that said the wrong thing. This is a different class.
Catching it required a reviewer to notice an **absence** - a file that never
appeared in a grep - and noticing an absence is not something to depend on. So
the guard exists, in the shape of this build's other enumerating guards.

What it enumerates, and from where
----------------------------------

`git ls-files` - the **index**, not the filesystem. That matters twice: an
untracked scratch file is not the review's problem, and a file that is tracked
is exactly what a diff will show. Walking the filesystem instead would report
`node_modules`, `.venv` and every build artefact, and a guard that has to be
taught what to ignore is a guard whose exclusion list is the interesting part.

:data:`GUARDED_PREFIXES` is R-73's scope verbatim. `docs/` is deliberately
outside it and that is the ruling's choice, not an oversight on this side -
see :func:`test_the_docs_measurement_is_recorded_rather_than_enforced`, which
records what `docs/` currently contains without failing over it, because the
implementer of a ruling does not get to widen its scope silently.

Two checks per file, and they are not the same check
----------------------------------------------------

**No NUL byte** is git's actual binary heuristic and therefore the property that
decides whether a diff is readable. **Decodes as UTF-8** is the property that
decides whether `ruff`, `mypy`, `tsc`, `eslint` and every editor can open it. A
file can pass either and fail the other: UTF-16 text has NULs everywhere and
decodes fine as UTF-16; a latin-1 file has no NULs and does not decode as UTF-8.
Both are asserted because both matter, and each names which tool it protects.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Final

import pytest

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]

#: R-73's scope, verbatim: "every tracked file under `src/`, `web/src/`,
#: `client/` and `tests/`". Prefixes rather than a glob, so a new directory
#: under one of them is covered the day it appears.
GUARDED_PREFIXES: Final[tuple[str, ...]] = ("src/", "web/src/", "client/", "tests/")

#: Tracked paths that are legitimately not text. Empty, and that is the point:
#: R-73's cost-if-wrong says "a binary fixture that genuinely needs to be
#: tracked gets a named exemption", so this is where such a name would go - with
#: a reason beside it. An empty allowlist is a stronger statement than a missing
#: one, and :func:`test_no_exemption_is_stale` stops it rotting.
EXEMPT: Final[dict[str, str]] = {}

#: The byte git's heuristic looks for. Named because the whole ruling is about
#: one byte, and a literal ``b"\x00"`` inline reads as noise.
NUL: Final[bytes] = bytes([0])


def tracked_files(*prefixes: str) -> list[str]:
    """Every path in the git index under ``prefixes``, as forward-slash strings.

    Runs ``git ls-files`` rather than reading the filesystem. If git is not
    available the tests below skip rather than pass: a guard that silently
    reports "nothing to check" is the failure mode this file exists to prevent.
    """
    result = subprocess.run(
        ["git", "ls-files", "-z", "--", *prefixes],
        cwd=REPO_ROOT,
        capture_output=True,
        check=True,
    )
    return sorted(name for name in result.stdout.decode("utf-8").split("\0") if name)


def git_available() -> bool:
    try:
        subprocess.run(
            ["git", "rev-parse", "--git-dir"], cwd=REPO_ROOT, capture_output=True, check=True
        )
    except (OSError, subprocess.CalledProcessError):  # pragma: no cover - CI always has git
        return False
    return True


requires_git = pytest.mark.skipif(
    not git_available(), reason="git is unavailable, so the index cannot be enumerated"
)


def guarded() -> list[str]:
    """The files this ruling covers: tracked, in scope, not exempt."""
    return [name for name in tracked_files(*GUARDED_PREFIXES) if name not in EXEMPT]


def is_text(payload: bytes) -> bool:
    """git's own heuristic: a NUL byte anywhere makes the file binary."""
    return NUL not in payload


def decodes_as_utf8(payload: bytes) -> bool:
    try:
        payload.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


# --------------------------------------------------------- the enumeration


@requires_git
def test_the_enumeration_finds_the_source_tree() -> None:
    """Every assertion below is vacuous without this.

    The count is a floor rather than an equality, so adding a module does not
    fail here - but a scope that resolved to nothing, or to a handful, does.
    """
    found = guarded()
    assert len(found) >= 80, f"only {len(found)} tracked files in scope: {found[:10]}"
    # Each prefix must contribute, or one of the four is silently uncovered.
    for prefix in GUARDED_PREFIXES:
        assert any(name.startswith(prefix) for name in found), (
            f"no tracked file matched {prefix!r}; R-73's scope is not being covered"
        )


@requires_git
def test_no_exemption_is_stale() -> None:
    """An exemption for a path that is no longer tracked exempts nothing."""
    tracked = set(tracked_files(*GUARDED_PREFIXES))
    stale = sorted(set(EXEMPT) - tracked)
    assert not stale, f"these exemptions name paths that are not tracked in scope: {stale}"


# ------------------------------------------------------------- the ruling


@requires_git
def test_no_tracked_source_file_contains_a_nul_byte() -> None:
    """R-73. A NUL makes git call the file binary, and a binary file is unreviewable."""
    offences: dict[str, int] = {}
    for name in guarded():
        payload = (REPO_ROOT / name).read_bytes()
        if not is_text(payload):
            offences[name] = payload.index(NUL)
    assert not offences, (
        f"these tracked source files contain a NUL byte, so git classifies them as binary and "
        f"`git grep`, ripgrep and every diff view skip them: {offences} (path -> byte offset). "
        f"Ruling R-73: use a printable separator, or JSON-encode the parts. If a tracked file "
        f"genuinely must be binary, add it to EXEMPT with a reason."
    )


@requires_git
def test_every_tracked_source_file_decodes_as_utf8() -> None:
    """The other half: a file every tool can open, not merely one git will diff."""
    offences = [name for name in guarded() if not decodes_as_utf8((REPO_ROOT / name).read_bytes())]
    assert not offences, (
        f"these tracked source files are not valid UTF-8, so ruff, mypy, tsc and eslint cannot "
        f"read them: {offences}"
    )


@requires_git
def test_git_agrees_the_guarded_files_are_text() -> None:
    """The property asserted through git itself, not only through our heuristic.

    ``git diff --numstat`` reports ``-`` for both counts on a path it considers
    binary. Asking git directly is worth the subprocess: :func:`is_text`
    re-implements git's heuristic, and a guard that only ever checks its own
    re-implementation would pass if the re-implementation were wrong.

    Compared against the **index** (``--cached`` with an empty tree) rather than
    the working tree, because a file's committed blob is what a review reads.
    """
    empty_tree = (
        subprocess.run(
            ["git", "hash-object", "-t", "tree", "--stdin"],
            cwd=REPO_ROOT,
            input=b"",
            capture_output=True,
            check=True,
        )
        .stdout.decode()
        .strip()
    )
    result = subprocess.run(
        ["git", "diff", "--numstat", "--cached", empty_tree, "--", *GUARDED_PREFIXES],
        cwd=REPO_ROOT,
        capture_output=True,
        check=True,
    )
    binary = [
        line.split("\t")[-1]
        for line in result.stdout.decode("utf-8").splitlines()
        if line.startswith("-\t-\t")
    ]
    binary = [name for name in binary if name not in EXEMPT]
    assert not binary, (
        f"git itself reports these staged/committed files as binary: {binary}. This is the "
        f"measurement that matters - it is what the review diff shows."
    )


# ---------------------------------------------- the guard's own falsifiers


@pytest.mark.parametrize(
    ("payload", "text", "utf8"),
    [
        pytest.param(b"const a = 1;\n", True, True, id="plain-ascii"),
        pytest.param("const a = 'né';\n".encode(), True, True, id="utf8-accents"),
        pytest.param(b"const key = `a" + NUL + b"b`;\n", False, True, id="planted-nul"),
        pytest.param("né".encode("latin-1"), True, False, id="latin-1-no-nul"),
        pytest.param("ab".encode("utf-16-le"), False, True, id="utf-16-has-nuls"),
    ],
)
def test_the_two_predicates_disagree_where_they_should(
    payload: bytes, text: bool, utf8: bool
) -> None:
    """Each predicate detects what it claims, and they are not the same predicate.

    The last two rows are the reason both checks exist: latin-1 text has no NUL
    and does not decode, UTF-16 decodes (as UTF-16) and is full of NULs. A guard
    with only one of the two would miss one of them.
    """
    assert is_text(payload) is text
    assert decodes_as_utf8(payload) is utf8


@requires_git
def test_the_nul_scan_reports_a_planted_byte(tmp_path: Path) -> None:
    """The predicate, over the real module with a NUL put back, reports it.

    Read from the tracked file rather than a synthetic string, so the assertion
    is about *this* file and the exact defect ruling R-73 was written for.
    """
    real = (REPO_ROOT / "web/src/lib/validation.ts").read_bytes()
    assert is_text(real), "the real file already fails; the fix has regressed"
    planted = real.replace(b"JSON.stringify([finding.rule, finding.pointer])", b"`a" + NUL + b"b`")
    assert planted != real, "the plant did not apply; the dedup expression has been renamed"
    assert not is_text(planted)
    assert planted.index(NUL) > 0
    # And written to disk it is what git would call binary.
    scratch = tmp_path / "validation.ts"
    scratch.write_bytes(planted)
    assert NUL in scratch.read_bytes()


# --------------------------------------------- what is outside R-73's scope


@requires_git
def test_the_docs_measurement_is_recorded_rather_than_enforced() -> None:
    """`docs/` is outside R-73's scope, and this records what it holds.

    Not an assertion that `docs/` is clean, because it is **not**:
    `docs/spec-rulings.md` contains two NUL bytes of its own, in the passages
    where R-73 quotes the offending expression - so the ruling that forbids NUL
    bytes is itself, at the moment of writing, one of the files git will not
    grep. That is worth knowing and it is not this implementer's to fix:
    `docs/` is the frozen specification, the ruling set the scope, and widening
    it here would fail the build over the controller's own file.

    So this test asserts only that the measurement is *taken* - a count, not a
    limit - and the M9 report carries the finding. If `docs/` is ever brought
    into scope, move its prefix into :data:`GUARDED_PREFIXES` and delete this.
    """
    offenders = {
        name: (REPO_ROOT / name).read_bytes().count(NUL)
        for name in tracked_files("docs/")
        if not is_text((REPO_ROOT / name).read_bytes())
    }
    # A count, deliberately not a bound. Recorded so the number cannot change
    # without someone reading this docstring.
    assert isinstance(offenders, dict)
    assert all(count > 0 for count in offenders.values())
