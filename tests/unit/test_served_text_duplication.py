"""Ruling R-78: duplication is measured by shingles, not by formatting.

M9.5's charter (R-76) named one risk - "if a prompt restates the README rather
than replacing it, this milestone has added a second place to drift and fixed
nothing" - and the milestone shipped believing it had avoided that, on the
evidence ``grep -c '^> ' README.md == 0``.

**That count is 0 and it proves nothing.** It measures blockquote formatting.
The reviewer measured the property instead, with 9-word shingles, and found 34
shared shingles in three fragments, one of them ~25 consecutive words verbatim.
R-78's general rule: when a claim is about a property, measure the property.
``grep -c '^> '`` stands to "no duplication" exactly as ``git grep -Il ''`` stood
to "no NUL byte" in R-73 - a plausible proxy that answers a different question,
wrong in the direction that looks like success.

What this guard measures
------------------------

The **served** text, rendered - not the source literals. Every registered prompt
is fetched over ``prompts/get`` and every registered resource over
``resources/read``, against a seeded store, and the result is shingled against
every **tracked** ``.md`` in the repository. Three reasons for rendering rather
than AST-scanning the string literals:

- it is the property. A caller receives the rendered text; a literal split
  across two helpers is invisible to a scan and obvious to a reader.
- it is enumerated from the **registered surface**, so a fifth prompt or a sixth
  resource is covered the moment it is registered - the same durability property
  the coverage guards have, and the absence of which is what let this
  duplication ship.
- interpolated content is included, so a prompt that starts pasting a README
  paragraph *through* a helper is caught.

``git ls-files`` rather than a glob, so an untracked scratch file or the
gitignored `.superpowers/` build reports - which quote prompt output at length,
legitimately - are outside the comparison. A document that is not in the
repository cannot drift with it.

**Scope, as R-78 was amended:** documentation a caller reads for *instructions*
- `README.md`, `CLAUDE.md` and `docs/`. `DECISIONS.md` is exempt by name, because
an append-only record whose function is quotation is citing, not drifting. The
reason lives at :data:`EXEMPT_DOCUMENTS` as well as in the ruling, and
:func:`test_the_document_exemption_has_not_eaten_the_guard` is what stops the
exemption growing into a deletion.

What is subtracted, and why it is not a hole
--------------------------------------------

**Stored document content**, removed as a token run rather than subtracted as a
set of shingles - :func:`authored` says why. A resource wraps a blueprint or a
dataset *from the store*, and in a seeded test store those documents came from
`docs/worked-example.md`, which is by design "the byte-exact source for
`tests/fixtures/`". Measured without the subtraction, the two example resources
share 478 and 464 shingles with `worked-example.md`, plus 73 and 22 with
`contracts.md` and 13 with `prd.md`. Every one of those is a fixture appearing in
the document it was authored in - not a second place to drift, and already
guarded: ruling R-44 added a drift test pinning the committed fixtures against
`worked-example.md`.

So the store's own documents are read back **from the store** and their shingles
subtracted, rather than a list of fixture filenames being written down here. That
is what keeps the subtraction from rotting: whatever the store holds is what is
subtracted, so seeding a different fixture changes nothing about this guard.

What survives the subtraction is the prose this milestone authored - every prompt
in full, and a resource body's ``note``, ``next_step`` and URIs - which is
exactly the text R-78 is about.

Tuning, per R-78
----------------

"A shingle guard can false-positive on a shared technical phrase that is not
really duplication (a rule id, a tool name). Tune the window, or exempt a named
string - never delete the guard." :data:`WINDOW` is the window and
:data:`EXEMPT` is the exemption list, which is **empty**: nothing needed
exempting once `README.md` was de-duplicated, and an entry here needs a reason
beside it.

Shown failing first
-------------------

R-77(e) is now a standing rule: a guard is not delivered until it has been run
against the thing it forbids and observed to fail. This one was run against the
un-de-duplicated `README.md` and reported all three fragments, longest first,
with their line numbers on both sides. `DECISIONS.md` `[M9.5, fix round 1]`
carries that output.
"""

from __future__ import annotations

import asyncio
import re
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

import pydantic_core
import pytest

from agentprops.models import Dataset, DatasetQuery
from agentprops.service import ServiceContext, blueprints, datasets
from toolclient import connected

REPO_ROOT: Final[Path] = Path(__file__).parents[2]

#: Words per shingle. Nine is what the reviewer measured with and what R-78
#: records. Short enough to catch a rephrased sentence, long enough that a
#: shared rule id plus its surrounding clause does not collide by accident.
WINDOW: Final = 9

#: Shingles that are shared and are **not** duplication. Empty, deliberately:
#: `README.md` was de-duplicated rather than exempted, which is the order R-78
#: puts them in. An entry here is a normalised nine-word string and must carry
#: the reason it is not duplication.
EXEMPT: Final[frozenset[str]] = frozenset()

#: Documents the comparison **skips**, by repository-relative POSIX path. A
#: different mechanism from :data:`EXEMPT`, which forgives one phrase everywhere;
#: this forgives one *file* entirely, so an entry needs the stronger reason.
#:
#: ``DECISIONS.md`` is exempt because it is an **append-only record whose function
#: is quotation** - it exists to preserve what was decided and, often, the exact
#: text that was wrong. Overlap there is *citation*, not drift, and a guard that
#: fights it converts a build log into a paraphrase, losing the thing that made
#: the log worth keeping. This guard demonstrated that on itself: the
#: ``[M9.5, fix round 1]`` entry recording it quoted its own failure output, the
#: guard flagged that as duplication, and the evidence was clipped one word below
#: the window to satisfy it. Ruling R-78 was amended for exactly that reason, and
#: the evidence is back at full length.
#:
#: **The cost, recorded here rather than only in the ruling:** a genuinely
#: duplicated *instruction* could hide in an exempt file. Accepted for this one,
#: because nobody follows a build log - R-78's scope is documentation a caller
#: reads for instructions, which is `README.md`, `CLAUDE.md` and `docs/`. An entry
#: added here for a document anyone *acts* on would be deleting the guard by
#: instalments, which is what R-78 forbids, and
#: :func:`test_the_document_exemption_has_not_eaten_the_guard` is what makes that
#: visible rather than gradual.
EXEMPT_DOCUMENTS: Final[frozenset[str]] = frozenset({"DECISIONS.md"})

#: How many overlapping shingles to print per offence, longest first. More than
#: one because an offence is usually a fragment rather than a sentence, and the
#: single longest shingle sent the first reading of this guard's output looking
#: at one of three fragments - "there is duplication somewhere" is not
#: actionable, and being actionable is this guard's whole job.
REPORTED: Final = 3

#: Markdown emphasis and code punctuation, stripped from token edges so that
#: ``**BP-014**:`` and ``BP-014`` are the same word. Without this the guard
#: would miss a copy that had been re-emphasised, which is a copy.
_EDGE = re.compile(r"^[*_`~\"'(\[]+|[*_`~\"'),.;:\]]+$")


def words(text: str) -> list[str]:
    """``text`` as lowercase words, markdown punctuation stripped from the edges."""
    return [stripped for raw in text.lower().split() if (stripped := _EDGE.sub("", raw))]


def shingles(text: str) -> set[str]:
    """Every :data:`WINDOW`-word window in ``text``, as normalised strings."""
    tokens = words(text)
    return {" ".join(tokens[index : index + WINDOW]) for index in range(len(tokens) - WINDOW + 1)}


def relative(path: Path) -> str:
    """``path`` as a repository-relative POSIX string, which is how it is named."""
    return path.relative_to(REPO_ROOT).as_posix()


def compared_markdown() -> list[Path]:
    """The tracked ``.md`` files this guard actually reads."""
    return [path for path in tracked_markdown() if relative(path) not in EXEMPT_DOCUMENTS]


def tracked_markdown() -> list[Path]:
    """Every tracked ``.md`` file, asked of git rather than globbed.

    Unfiltered on purpose: :func:`compared_markdown` applies
    :data:`EXEMPT_DOCUMENTS`, and keeping the two separate is what lets
    :func:`test_the_document_exemption_has_not_eaten_the_guard` compare what git
    found against what the guard reads.
    """
    listed = subprocess.run(
        ["git", "ls-files", "-z", "*.md"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return [REPO_ROOT / name for name in listed.stdout.split("\0") if name]


@pytest.fixture
def seeded(
    context: ServiceContext,
    blueprint_document: dict[str, Any],
    dataset_document: dict[str, Any],
    other_dataset_document: dict[str, Any],
) -> ServiceContext:
    """A store with something in it, so every prompt renders its full text.

    An empty store makes three of the four prompts fall back to their
    "nothing here yet" paragraph, which would take the bodies this guard exists
    to compare out of the comparison entirely.
    """
    blueprints.upsert(context, blueprint_document, publish=True)
    context.store.put_dataset(Dataset.model_validate(dataset_document))
    context.store.put_dataset(Dataset.model_validate(other_dataset_document))
    return context


def store_content(context: ServiceContext) -> list[list[str]]:
    """Every document the store holds, as token lists, read back from the store.

    Removed from every served text. Derived rather than listed, so it covers
    whatever this store was seeded with - see the module docstring.
    """
    documents: list[dict[str, Any]] = []
    for summary in context.store.list_blueprints(None):
        blueprint = context.store.get_blueprint(summary.agent_id, summary.version)
        if blueprint is not None:
            documents.append(blueprints.document(blueprint))
    for row in context.store.find_datasets(DatasetQuery()):
        dataset = context.store.get_dataset(str(row.id), None)
        if dataset is not None:
            documents.append(datasets.document(dataset))
    return [words(_as_served(document)) for document in documents]


def authored(text: str, documents: list[list[str]]) -> str:
    """``text`` with every stored document's token run removed. What is left is prose.

    Token-**subsequence** removal rather than set subtraction of shingles, and
    the difference is one real shingle. Set subtraction leaves the windows that
    *straddle* the boundary between a wrapped document and the resource body
    around it: measured, that reported ``'must never be called } validated_at
    2026-09-08t10:14:22z } note'`` as duplication of `worked-example.md`, which
    it is not - it is the end of a fixture abutting the start of a ``note`` key.
    Removing the run makes the wrapper's own tokens adjacent, which is what they
    would be if the body carried no document at all.
    """
    remaining = words(text)
    for document in documents:
        if not document:
            continue
        index = 0
        while index <= len(remaining) - len(document):
            if remaining[index : index + len(document)] == document:
                del remaining[index : index + len(document)]
                continue
            index += 1
    return " ".join(remaining)


def _as_served(document: dict[str, Any]) -> str:
    """One document serialised the way ``resources/read`` serialises it.

    ``pydantic_core.to_json(..., indent=2)``, because that is literally what
    `mcp`'s ``FunctionResource.read`` does with a ``dict`` return - and the
    tokens differ from ``json.dumps``'s compact form enough that a compact
    subtraction removed only a fifth of the fixture shingles. Measured: 478
    stayed at 420. Indentation itself is irrelevant, since the tokeniser splits
    on whitespace; the separator placement is not.
    """
    return pydantic_core.to_json(document, fallback=str, indent=2).decode()


async def served_text(context: ServiceContext) -> dict[str, str]:
    """Every registered prompt and resource, rendered. Keyed by what served it.

    Enumerated from the running server, so nothing here names a prompt or a URI
    and a new one is covered without a test change.
    """
    rendered: dict[str, str] = {}
    async with connected(context) as client:
        for prompt in (await client.list_prompts()).prompts:
            arguments = {
                argument.name: "location-onboarding"
                for argument in prompt.arguments or []
                if argument.required
            }
            result = await client.get_prompt(prompt.name, arguments)
            rendered[f"prompt {prompt.name}"] = "\n".join(
                getattr(message.content, "text", "") for message in result.messages
            )
        for resource in (await client.list_resources()).resources:
            uri = str(resource.uri)
            read = await client.read_resource(uri)
            rendered[f"resource {uri}"] = "\n".join(
                getattr(item, "text", "") for item in read.contents
            )
    return rendered


def test_the_renderer_finds_the_whole_surface(seeded: ServiceContext) -> None:
    """Non-vacuity. Every assertion below passes against an empty dict."""
    rendered = asyncio.run(served_text(seeded))
    assert len(rendered) >= 7, f"expected four prompts and three resources, got {sorted(rendered)}"
    thin = [name for name, text in rendered.items() if len(words(text)) < WINDOW]
    assert not thin, (
        f"these rendered to fewer than {WINDOW} words, so they shingle to nothing: {thin}"
    )


def test_the_shingler_finds_a_planted_copy() -> None:
    """The guard's own guard: the comparison must detect an actual copy.

    Without this, a normaliser that dropped every token - or a window wider than
    the texts - would report "no duplication" for ever, which is precisely the
    failure R-78 is about. Both directions: a verbatim run of nine words
    collides, and a paraphrase of the same idea does not.
    """
    original = "two nodes sharing a tool_name where position cannot disambiguate them at all"
    verbatim = f"Some preamble. {original} Some trailing text."
    paraphrase = "when a pair of steps reuse one tool name and their order cannot tell them apart"
    assert shingles(original) & shingles(verbatim), "the shingler misses a verbatim copy"
    assert not shingles(original) & shingles(paraphrase), "the shingler collides on a paraphrase"


def test_the_subtraction_finds_the_stored_documents(seeded: ServiceContext) -> None:
    """Non-vacuity for the subtraction, which would otherwise hide everything.

    A ``store_content`` that came back empty would leave the guard strictly
    stricter, so this is not the dangerous direction - but one that came back
    *enormous* (every shingle in the repository, say) would silently subtract
    the duplication away, and that is the direction R-78 warns about. Both
    bounds: it finds the seeded documents, and it does **not** contain the prose
    a prompt authors.
    """
    stored = store_content(seeded)
    assert len(stored) == 3, f"expected the seeded blueprint and two datasets, got {len(stored)}"
    assert all(len(document) > 100 for document in stored), (
        f"a document read back as {[len(d) for d in stored]} tokens, which removes nothing"
    )
    prose = (
        "Read this repository's agent and author an agent-props blueprint for it, using the "
        "agent-props MCP tools."
    )
    assert authored(prose, stored) == " ".join(words(prose)), (
        "the removal touches prompt prose, which would hide real duplication"
    )


def test_the_markdown_listing_is_not_empty() -> None:
    """Blames `git ls-files` rather than the docs when the listing comes back empty."""
    listed = tracked_markdown()
    names = {path.name for path in listed}
    assert {"README.md", "CLAUDE.md", "DECISIONS.md"} <= names, (
        f"git ls-files did not return the three documents this repo is read through: "
        f"{sorted(names)}"
    )
    assert all(path.exists() for path in listed), "git listed a .md file that is not on disk"


def test_the_document_exemption_has_not_eaten_the_guard() -> None:
    """:data:`EXEMPT_DOCUMENTS` skips a file; it must not skip the scope.

    R-78's amended scope is "documentation a caller reads for **instructions**:
    `README.md`, `CLAUDE.md`, and `docs/`". A per-file exemption is the mechanism
    that could grow until the guard covers nothing, one honest-looking entry at a
    time, so the four claims that stop it are asserted rather than trusted:

    - every exempt name is a file git actually tracks, so an exemption cannot go
      stale into a no-op that reads as protection - the same shape as
      ``test_every_deferral_names_a_documented_tool``;
    - the compared set still holds the two documents a caller is told to read;
    - it still holds something under `docs/`, which R-78's scope names;
    - each exempt file really is skipped, so the exemption does what it says.
    """
    tracked = {relative(path) for path in tracked_markdown()}
    unknown = EXEMPT_DOCUMENTS - tracked
    assert not unknown, f"EXEMPT_DOCUMENTS names files git does not track: {sorted(unknown)}"

    compared = {relative(path) for path in compared_markdown()}
    assert compared, "the exemption skipped every tracked document"
    assert {"README.md", "CLAUDE.md"} <= compared, (
        f"the exemption reaches a document a caller reads for instructions: {sorted(compared)}"
    )
    assert any(name.startswith("docs/") for name in compared), (
        "no document under docs/ is compared; R-78's amended scope names it explicitly"
    )
    assert not (EXEMPT_DOCUMENTS & compared), "an exempt document is being compared anyway"


def test_no_served_text_is_duplicated_in_a_tracked_document(seeded: ServiceContext) -> None:
    """Ruling R-78, and ruling R-76's named risk measured rather than proxied.

    A prompt or a resource body may not share a nine-word run with any tracked
    ``.md``. The README explains *why* each prompt exists and what it is for;
    the prompt says the thing. When both say the thing, there are two places to
    drift and the milestone fixed nothing.

    Reports every offence with both line numbers and the longest overlapping run
    first, because "there is duplication somewhere" is not actionable and this
    guard's whole job is to be.
    """
    rendered = asyncio.run(served_text(seeded))
    documents = {path: path.read_text(encoding="utf-8") for path in tracked_markdown()}
    offences = duplications(rendered, documents, store_content(seeded))
    assert not offences, (
        "served text is duplicated in tracked documentation (ruling R-78). Move the text into "
        "the prompt and leave the document explaining why it exists, or - if a shingle is a "
        "shared rule id rather than duplication - widen WINDOW or add it to EXEMPT with a "
        "reason, or - if the document is an append-only record rather than an "
        "instruction - to EXEMPT_DOCUMENTS with the stronger reason R-78 asks for.\n"
        + "\n".join(offences)
    )


def duplications(
    rendered: Mapping[str, str], documents: Mapping[Path, str], stored: list[list[str]]
) -> list[str]:
    """Every served-text/document overlap, worst first. The comparison itself.

    A function rather than the body of one test, so
    :func:`test_the_document_exemption_lets_a_citation_pass_and_still_catches_an_instruction`
    can drive **both ends** of :data:`EXEMPT_DOCUMENTS` on synthetic inputs -
    which is the only way to assert that an exempt file passes without editing a
    real one and the standing rule is to test both ends, not the convenient one.

    ``documents`` is handed in unfiltered and the exemption is applied here, so
    the test exercises the real filter rather than a copy of it.
    """
    ranked: list[tuple[int, str]] = []
    for source, text in sorted(rendered.items()):
        served = shingles(authored(text, stored)) - EXEMPT
        for path, document in sorted(documents.items()):
            if relative(path) in EXEMPT_DOCUMENTS:
                continue
            shared = served & shingles(document)
            if not shared:
                continue
            worst = sorted(shared, key=lambda item: (-len(item), item))[:REPORTED]
            shown = "".join(f"\n    {shingle!r}{_where(document, shingle)}" for shingle in worst)
            ranked.append(
                (
                    len(shared),
                    f"{source} shares {len(shared)} {WINDOW}-word shingle(s) with "
                    f"{relative(path)}{shown}",
                )
            )
    ranked.sort(reverse=True)
    return [line for _, line in ranked]


#: A nine-word run used to drive both ends of the document exemption. Long enough
#: to shingle, and deliberately not a sentence from any prompt or document, so it
#: cannot pass or fail for a reason other than the one under test.
_PLANTED = "a planted run of exactly nine words for the exemption control"


def test_the_document_exemption_lets_a_citation_pass_and_still_catches_an_instruction() -> None:
    """Both ends of :data:`EXEMPT_DOCUMENTS`, on synthetic inputs.

    The same copied text in two documents: reported for `README.md`, silent for
    the exempt `DECISIONS.md`. Asserting only the passing half would be the
    vacuous shape - an exemption that swallowed *everything* would satisfy it -
    so the failing half is asserted first and the message is checked to name the
    document, not merely to be non-empty.
    """
    rendered = {"prompt planted": _PLANTED}
    assert len(words(_PLANTED)) >= WINDOW, "the control text is too short to shingle"

    caught = duplications(rendered, {REPO_ROOT / "README.md": _PLANTED}, [])
    assert len(caught) == 1 and "README.md" in caught[0], (
        f"the guard missed a duplication in a compared document: {caught}"
    )

    for name in sorted(EXEMPT_DOCUMENTS):
        assert not duplications(rendered, {REPO_ROOT / name: _PLANTED}, []), (
            f"{name} is exempt and was reported anyway"
        )


def _where(document: str, shingle: str) -> str:
    """`` (line N)`` for the first line whose window starts the shingle, or ``""``.

    Located by re-shingling line by line rather than by string search, because
    the shingle is normalised and the document is not.
    """
    for number, line in enumerate(document.splitlines(), start=1):
        if shingle in shingles(line):
            return f" (line {number})"
    return ""
