"""Ruling R-72: the surface emits three envelope shapes, and the harness can build all three.

The bug this file exists to stop recurring
------------------------------------------

`blueprint_validate` and `dataset_validate` return `{ok, errors}` with **no
`data` key** — whatever the outcome. Per ruling R-13 a document that trips only
BP-019, DS-007, DS-027 or DS-032 comes back `ok: true` with *warning*-severity
items in `errors`. The web app routed that through its `data` reader, which
called `Object.keys(undefined)`, threw, had the throw swallowed, and announced
"shape and catalogue clean" over a document the catalogue had warned about.

It survived a whole milestone because `web/src/test/server.ts` could only
construct a success-with-`data` envelope, so all six clause-1 tests stubbed the
validate tools with a wrapper **no validate tool produces**. Six tests agreed
with each other about a shape the service never sends.

What this file measures, and why it is the Python side
------------------------------------------------------

The web suite can assert that its harness builds three shapes. It cannot assert
that three is the right number, because it never speaks to the service. So this
file does the half that needs a real store:

1. Call **every tool the web app names**, against a real store, over a real MCP
   session — reads, both validators in all three of their outcomes, and both
   writes.
2. Classify each reply structurally and assert it matches **exactly one** of the
   three documented shapes. A fourth shape fails here.
3. Assert all three are actually **observed** — otherwise (2) passes vacuously
   on a surface that only ever sends one.
4. Assert `web/src/test/server.ts` declares a constructor for **each shape
   observed**, by name. That is the cross-language coupling: a shape the service
   emits that the harness cannot build fails here, in the language that can see
   the service.

Step 4 is a text check on the harness, in the same idiom as
`test_web_writes_through_tools.py`. It is deliberately not a shared JSON
artefact: a generated file is a third thing to keep in step, and the guard's job
is to notice when two things have drifted, not to make them one thing.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, Final

import pytest

import agentprops.server
from agentprops.service import ServiceContext
from toolclient import invoke
from unit.test_web_writes_through_tools import strip_comments

REPO_ROOT: Final[Path] = Path(agentprops.server.__file__).resolve().parents[3]
HARNESS: Final[Path] = REPO_ROOT / "web" / "src" / "test" / "server.ts"
FIXTURES: Final[Path] = Path(__file__).resolve().parents[1] / "fixtures"

BLUEPRINT_FILE: Final[Path] = FIXTURES / "blueprints" / "location-onboarding-1.0.0.json"
PRIYA_FILE: Final[Path] = FIXTURES / "datasets" / "priya-missing-docs.json"


def load(path: Path) -> dict[str, Any]:
    document: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return document


BLUEPRINT: Final[dict[str, Any]] = load(BLUEPRINT_FILE)
PRIYA: Final[dict[str, Any]] = load(PRIYA_FILE)


#: The three shapes `docs/contracts.md` section 1 documents, as structural
#: predicates over the wire object. Named identically to
#: `web/src/test/server.ts`'s ``ENVELOPE_SHAPES`` keys, which is what step 4
#: compares against.
SHAPES: Final[dict[str, Callable[[dict[str, Any]], bool]]] = {
    "success-with-data": lambda e: e.get("ok") is True and "data" in e and "warnings" in e,
    "validate-clean": lambda e: e.get("ok") is True and "data" not in e and "errors" in e,
    "failure-with-errors": lambda e: e.get("ok") is False and "data" not in e and "errors" in e,
}


def classify(envelope: dict[str, Any]) -> str:
    """The one shape ``envelope`` matches, or a loud description of the problem."""
    matched = [name for name, predicate in SHAPES.items() if predicate(envelope)]
    if len(matched) == 1:
        return matched[0]
    return f"UNCLASSIFIED(matched={matched}, keys={sorted(envelope)})"


def bundle(*datasets: dict[str, Any]) -> dict[str, Any]:
    """The bundle `web/src/mcp/tools.ts::datasetSave` builds."""
    return {
        "format": "agentprops.bundle",
        "format_version": 1,
        "agent_id": BLUEPRINT["agent_id"],
        "blueprints": [],
        "datasets": list(datasets),
    }


def warned_dataset() -> dict[str, Any]:
    """A dataset that trips **DS-027 only** — a warning, not an error.

    Intent byte-identical to narrative. PRD 5.7 calls this "what a hurried
    author does", and `DatasetDetail.tsx` cites DS-027 as the mistake the detail
    screen exists to catch. It is the finding that was invisible in the editor.
    """
    document: dict[str, Any] = json.loads(json.dumps(PRIYA))
    document["provenance"]["intent"] = document["narrative"]
    return document


def broken_dataset() -> dict[str, Any]:
    """A dataset that trips DS-026 — an error."""
    document: dict[str, Any] = json.loads(json.dumps(PRIYA))
    document["provenance"]["intent"] = "x"
    return document


@pytest.fixture
async def library(context: ServiceContext) -> ServiceContext:
    """The published blueprint and one golden dataset, via the app's write path."""
    published = await invoke(context, "blueprint_upsert", blueprint=BLUEPRINT, publish=True)
    assert published["ok"] is True, published
    imported = await invoke(context, "dataset_import", bundle=bundle(PRIYA))
    assert imported["ok"] is True, imported
    return context


async def observed(library: ServiceContext) -> dict[str, str]:
    """Every call the web app can make, mapped to the shape it answered with.

    Keyed by a label rather than by tool name, because the two validators answer
    with a *different* shape depending on the document and both outcomes matter.
    """
    calls: list[tuple[str, str, dict[str, Any]]] = [
        # Reads.
        ("store_status", "store_status", {}),
        ("agent_list", "agent_list", {}),
        ("blueprint_list", "blueprint_list", {}),
        ("blueprint_get", "blueprint_get", {"agent_id": BLUEPRINT["agent_id"]}),
        ("label_vocabulary", "label_vocabulary", {"agent_id": BLUEPRINT["agent_id"]}),
        ("dataset_find", "dataset_find", {}),
        ("dataset_get", "dataset_get", {"dataset_id": PRIYA["id"]}),
        # The two validators, in all three outcomes each.
        ("blueprint_validate clean", "blueprint_validate", {"blueprint": BLUEPRINT}),
        ("dataset_validate clean", "dataset_validate", {"dataset": PRIYA}),
        ("dataset_validate warned", "dataset_validate", {"dataset": warned_dataset()}),
        ("dataset_validate broken", "dataset_validate", {"dataset": broken_dataset()}),
        # A rejection from a non-validator, so the failure shape is not only
        # ever produced by the validators.
        ("blueprint_get missing", "blueprint_get", {"agent_id": "nope", "version": "9.9.9"}),
        # The two writes.
        ("blueprint_upsert", "blueprint_upsert", {"blueprint": BLUEPRINT, "publish": False}),
        ("dataset_import", "dataset_import", {"bundle": bundle(PRIYA)}),
    ]
    found: dict[str, str] = {}
    for label, tool, arguments in calls:
        envelope = await invoke(library, tool, **arguments)
        found[label] = classify(envelope)
    return found


# ----------------------------------------------------------- the measurement


async def test_every_reply_matches_exactly_one_documented_shape(
    library: ServiceContext,
) -> None:
    """A fourth shape, or an ambiguous one, fails here."""
    found = await observed(library)
    unclassified = {label: shape for label, shape in found.items() if shape.startswith("UNCLASS")}
    assert not unclassified, (
        f"these replies match no documented envelope shape, or more than one: {unclassified}. "
        f"contracts section 1 documents three (ruling R-72); a fourth needs documenting there, "
        f"in SHAPES here, and in web/src/test/server.ts's ENVELOPE_SHAPES."
    )


async def test_all_three_shapes_are_actually_observed(library: ServiceContext) -> None:
    """The non-vacuity of the test above.

    A surface that only ever sent one shape would satisfy "matches exactly one"
    trivially. Naming which calls produced which shape also documents the
    surface: the validators are the only source of `validate-clean`, and it is
    the shape the app's reader used to crash on.
    """
    found = await observed(library)
    seen = set(found.values())
    assert seen == set(SHAPES), (
        f"expected all three documented shapes, observed {sorted(seen)}. Per-call: "
        f"{json.dumps(found, indent=1, sort_keys=True)}"
    )


async def test_the_validators_never_carry_data_even_when_clean(
    library: ServiceContext,
) -> None:
    """The precise fact R-72 turns on, asserted on its own.

    Not "the validators sometimes answer without `data`" — **never** with it.
    So a reader that routes a validate reply through the `data` accessor is
    broken on every call, not on an edge case, which is why the swallowed
    exception fired on every clean debounce tick.
    """
    for tool, arguments in (
        ("blueprint_validate", {"blueprint": BLUEPRINT}),
        ("dataset_validate", {"dataset": PRIYA}),
        ("dataset_validate", {"dataset": warned_dataset()}),
        ("dataset_validate", {"dataset": broken_dataset()}),
    ):
        envelope = await invoke(library, tool, **arguments)
        assert "data" not in envelope, f"{tool} grew a data key: {sorted(envelope)}"
        assert "errors" in envelope
        assert "warnings" not in envelope, (
            f"{tool} grew a warnings key; on this shape warning-severity findings travel in "
            f"`errors` (ruling R-13), and a second channel would be ambiguous"
        )


async def test_a_warning_only_document_is_ok_true_with_findings(
    library: ServiceContext,
) -> None:
    """Ruling R-13's reply, measured. This is the one the editor discarded."""
    envelope = await invoke(library, "dataset_validate", dataset=warned_dataset())
    assert envelope["ok"] is True, envelope
    assert [finding["rule"] for finding in envelope["errors"]] == ["DS-027"]
    assert {finding["severity"] for finding in envelope["errors"]} == {"warning"}


async def test_an_error_document_is_ok_false(library: ServiceContext) -> None:
    """The other side, so the assertion above is about severity and not about DS-027."""
    envelope = await invoke(library, "dataset_validate", dataset=broken_dataset())
    assert envelope["ok"] is False
    assert {finding["severity"] for finding in envelope["errors"]} == {"error"}


async def test_a_warning_the_app_can_actually_reach_carries_code_and_detail(
    library: ServiceContext,
) -> None:
    """Ground rule 3's channel, and the field names the renderer must use.

    `dataset_get` on an archived dataset warns. The web app's `ToolWarning`
    declared `{code, message, context}` until this fix round, and nothing failed
    — because nothing rendered a warning. So the field set is asserted exactly.
    """
    await invoke(library, "dataset_archive", dataset_id=PRIYA["id"])
    envelope = await invoke(library, "dataset_get", dataset_id=PRIYA["id"])
    assert envelope["ok"] is True
    assert envelope["warnings"], "an archived dataset read by id must warn (PRD 5.6)"
    for item in envelope["warnings"]:
        assert set(item) == {"code", "detail"}, (
            f"a Warning is {{code, detail}} per models/errors.py; got {sorted(item)}"
        )
    assert [item["code"] for item in envelope["warnings"]] == ["dataset_archived"]


# ------------------------------------------- the cross-language coupling


def harness_shapes() -> set[str]:
    """The shape names `web/src/test/server.ts` declares a constructor for.

    Read out of ``ENVELOPE_SHAPES``'s keys. A text read rather than a shared
    artefact for the reason in the module docstring: the guard's job is to
    notice drift between two independent statements, and a generated file would
    remove the independence that makes the comparison meaningful.
    """
    source = HARNESS.read_text(encoding="utf-8")
    block = re.search(r"export const ENVELOPE_SHAPES = \{(.*?)\n\} as const;", source, re.S)
    assert block is not None, (
        f"{HARNESS} no longer declares ENVELOPE_SHAPES; the harness and this guard have "
        f"drifted apart"
    )
    return set(re.findall(r'^  "([a-z-]+)": \{', block.group(1), re.M))


def test_the_harness_declares_a_constructor_for_every_documented_shape() -> None:
    """Ruling R-72's requirement, checked across the language boundary.

    A shape the service emits that the harness cannot construct is how the
    original bug hid. The harness must be able to build all three.
    """
    declared = harness_shapes()
    assert declared, "no shapes parsed out of ENVELOPE_SHAPES; the regex or the file has changed"
    missing = set(SHAPES) - declared
    assert not missing, (
        f"web/src/test/server.ts cannot construct these shapes: {sorted(missing)}. A harness "
        f"that cannot build a reply the service sends manufactures agreement rather than "
        f"testing anything (ruling R-72)."
    )
    extra = declared - set(SHAPES)
    assert not extra, (
        f"the harness declares shapes the service does not emit: {sorted(extra)}. Either the "
        f"surface grew a shape and SHAPES here needs it, or the harness is modelling fiction."
    )


async def test_the_harness_shape_names_match_what_the_service_emits(
    library: ServiceContext,
) -> None:
    """The two halves compared against the *observed* set, not the declared one.

    :func:`test_the_harness_declares_a_constructor_for_every_documented_shape`
    compares the harness with this file's :data:`SHAPES`. This compares it with
    what the running service actually produced — so if the service ever stops
    emitting one of the three, or starts emitting a fourth, the harness is held
    to the measurement rather than to the documentation.
    """
    seen = set((await observed(library)).values())
    assert seen == harness_shapes(), (
        f"the service emitted {sorted(seen)} and the harness can build {sorted(harness_shapes())}"
    )


def test_the_harness_cannot_forge_a_validate_reply_with_a_data_key() -> None:
    """The specific fiction the old harness produced, named so it cannot return.

    Every clause-1 test used to stub the validators with
    ``ok("report", {ok, errors})`` — a success envelope wrapping a report. If
    that idiom reappears in the web suite, the shape the app is tested against
    is once again not the shape it receives.
    """
    web_tests = sorted((REPO_ROOT / "web" / "src").rglob("*.test.ts*"))
    assert web_tests, "no web test files found; this guard is reading the wrong tree"
    forged = re.compile(r'ok\(\s*"report"')

    # Comments are stripped first, reusing the clause-5 guard's stripper. Every
    # test file that *used* the forged wrapper now explains in prose why it does
    # not, and a scan over raw source would fail on the explanation - which is
    # the same trade `test_web_writes_through_tools.py` already made and the
    # reason that stripper lives in one place rather than two.
    def code(path: Path) -> str:
        return strip_comments(path.read_text(encoding="utf-8"))

    # `server.test.ts` is the one file allowed to build the fiction, because it
    # builds it in order to *reject* it: it constructs the wrapper and asserts
    # `findingsIn` refuses it. The exemption is proved rather than assumed - the
    # file must still contain the idiom in code, or that assertion has been
    # deleted and the exemption covers nothing.
    demonstration = REPO_ROOT / "web" / "src" / "test" / "server.test.ts"
    assert forged.search(code(demonstration)), (
        f"{demonstration.name} no longer constructs the data-wrapped report it exists to "
        f"reject; either restore that assertion or drop this exemption"
    )

    offences: dict[str, list[str]] = {}
    for path in web_tests:
        if path == demonstration:
            continue
        hits = forged.findall(code(path))
        if hits:
            offences[str(path.relative_to(REPO_ROOT))] = hits
    assert not offences, (
        f"these tests stub a validate tool with a data-wrapped report, which no validate tool "
        f"sends: {offences}. Use validated([...]) from src/test/server.ts (ruling R-72)."
    )
