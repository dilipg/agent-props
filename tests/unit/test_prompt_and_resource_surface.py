"""The prompt and resource surfaces' drift guards. M9.5, ruling R-76.

`test_tool_surface.py` is the pattern, and the reasoning transfers exactly: a
surface kept in code and a suite kept by hand will diverge unless a test
compares them. R-76 asks for the coverage guard to be extended to both new
surfaces "enumerating from the **registered** surface rather than a literal
list - the property that made M4's tool guard durable, and the absence of which
let a 14th tool have slipped through".

**A guard listing today's three prompts by name is the defect it exists to
prevent.** So nothing below names a prompt or a resource except the two
*controls*, which exist to catch a scanner that has stopped matching. Everything
else is parametrised over what the running ``MCPServer`` reports, which means a
fourth prompt or a sixth resource is covered the moment it is registered and
named in the failure message when it is not tested.

The guards, and what each one would catch
-----------------------------------------

``test_the_server_registered_its_*``
    Non-vacuity, first, because every test below would pass against an empty
    surface and would blame the wrong thing while doing it. This is the same
    reason `test_tool_surface.py` opens with a count.

``test_every_registered_*_declares_a_description``
    A description is what a caller reads before choosing. For a prompt it is
    also what appears in the slash-command list these prompts exist to
    populate, so an undescribed prompt is a menu entry with no label.

``test_every_registered_prompt_argument_declares_a_description``
    Same claim one level down. ``prompts/get`` arguments are the parameterisation
    R-76 asks for; an argument with no description is one a caller has to guess.

``test_every_registered_*_answers_*``
    Runtime, and fully mechanical: prompt arguments are synthesised from the
    registered argument list and template URIs from the registered template, so
    a new entry is exercised with no test change at all. Against an **empty**
    store, deliberately - that is the case where a prompt is most tempted to
    promise something that does not exist.

``test_every_registered_*_has_a_dedicated_test``
    The coverage half. An AST walk over `tests/` collects every literal handed
    to ``get_prompt`` / ``read_resource``; each registered name must appear.
    Templates are matched with the SDK's own RFC 6570 matcher, so a test that
    reads one concrete URI covers the template that serves it.

``test_no_prompt_or_resource_body_is_an_envelope``
    Ruling R-43(b) as a mechanical check rather than a comment. Both surfaces
    have their own protocol shape and neither may grow ``{ok, data, warnings}``.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final

import pytest
from mcp import Client
from mcp.shared.uri_template import UriTemplate
from mcp_types import Prompt as RegisteredPrompt
from mcp_types import Resource as RegisteredResource
from mcp_types import ResourceTemplate as RegisteredTemplate

from agentprops.server import (
    DEFAULT_HTTP_HOST,
    DEFAULT_HTTP_PATH,
    DEFAULT_HTTP_PORT,
    mcp,
)
from agentprops.service import ServiceContext, examples, prompts
from agentprops.validation import RULE_REGISTRY
from sourcescan import literals_passed_to
from toolclient import connected

TESTS_DIR: Final[Path] = Path(__file__).parents[1]

#: The call spellings a test reaches ``prompts/get`` through - the protocol
#: method itself plus `test_prompts_contract.py`'s helper, exactly as
#: ``TOOL_CALL_FUNCTIONS`` covers ``call_tool`` plus ``invoke`` and ``attempt``.
#: A test using a new spelling needs it added here, which is a visible decision
#: rather than a silent gap in the guard.
PROMPT_CALL_FUNCTIONS: Final[frozenset[str]] = frozenset({"get_prompt", "prompt_text"})

#: The call spellings a test reaches ``resources/read`` through.
RESOURCE_CALL_FUNCTIONS: Final[frozenset[str]] = frozenset({"read_resource", "resource_body"})

#: A prompt name and a resource URI that must **never** be registered, used as
#: the scanners' negative controls.
#:
#: Both are named for ``blueprint_infer``, which `docs/contracts.md` section 4
#: tags *(phase 1.5)* and says "Not in phase 1" - so, unlike a deferred tool
#: that will one day land, these stay valid controls for the whole of this
#: build. That was the flaw `test_tool_surface.py` had to fix once already,
#: recorded in `DECISIONS.md` under `[M5]`.
#: Neither may appear in a ``get_prompt``/``read_resource`` call anywhere under
#: `tests/` - the first draft of this file used a URI it also passed to
#: ``read_resource`` in the unknown-URI test, and the control credited itself.
UNCALLED_PROMPT_CONTROL: Final = "infer-a-blueprint"
UNCALLED_RESOURCE_CONTROL: Final = "agentprops://inferred-blueprint/never-registered"

#: Every resource on this surface is a JSON document.
JSON_MIME: Final = "application/json"

#: The envelope keys ruling R-43(b) forbids on these two surfaces.
ENVELOPE_KEYS: Final[frozenset[str]] = frozenset({"ok", "data", "warnings", "errors"})


def registered_prompts() -> list[RegisteredPrompt]:
    """What the running server reports for ``prompts/list``.

    ``asyncio.run`` at collection time, for the reason
    `test_tool_surface.py::registered` gives: the list has to be a module-level
    constant so it can parametrise a test, and no event loop is running during
    collection.
    """
    return asyncio.run(mcp.list_prompts())


def registered_resources() -> list[RegisteredResource]:
    return asyncio.run(mcp.list_resources())


def registered_templates() -> list[RegisteredTemplate]:
    return asyncio.run(mcp.list_resource_templates())


PROMPTS: Final[list[RegisteredPrompt]] = registered_prompts()
PROMPT_NAMES: Final[tuple[str, ...]] = tuple(sorted(prompt.name for prompt in PROMPTS))

RESOURCES: Final[list[RegisteredResource]] = registered_resources()
RESOURCE_URIS: Final[tuple[str, ...]] = tuple(sorted(str(row.uri) for row in RESOURCES))

TEMPLATES: Final[list[RegisteredTemplate]] = registered_templates()
TEMPLATE_URIS: Final[tuple[str, ...]] = tuple(sorted(row.uri_template for row in TEMPLATES))


# ------------------------------------------------------------- non-vacuity


def test_the_server_registered_its_prompts() -> None:
    """R-76's measured starting point was ``prompts: []``, so this is the fix.

    Every test below would pass vacuously against an empty surface. A lower
    bound rather than an equality, because a fourth prompt is an addition to
    cover, not a regression to fail.
    """
    assert len(PROMPT_NAMES) >= 3, (
        f"expected at least R-76's minimum of three prompts, got {PROMPT_NAMES}; R-76's "
        f"finding was that this list was empty"
    )


def test_the_server_registered_its_resources() -> None:
    """``resources: []`` was the other half of R-76's finding."""
    assert len(RESOURCE_URIS) >= 3, f"expected at least three resources, got {RESOURCE_URIS}"
    assert len(TEMPLATE_URIS) >= 2, (
        f"expected at least two resource templates - R-76 asks for the published blueprints "
        f"and one example dataset per agent, both of which are per-agent - got {TEMPLATE_URIS}"
    )


def test_both_capabilities_are_advertised_and_now_populated() -> None:
    """The finding restated as an assertion: advertised **and** registered.

    The server has advertised ``prompts`` and ``resources`` in its
    ``initialize`` reply since M4. Advertising a capability and serving nothing
    under it is what R-76 was written about, so both halves are asserted
    together rather than separately.
    """
    capabilities = asyncio.run(_capabilities())
    assert capabilities.get("prompts") is not None, "the prompts capability is not advertised"
    assert capabilities.get("resources") is not None, "the resources capability is not advertised"
    assert PROMPT_NAMES and RESOURCE_URIS, "a capability is advertised with nothing under it"


async def _capabilities() -> dict[str, Any]:
    async with Client(mcp) as client:
        dumped: dict[str, Any] = client.server_capabilities.model_dump(mode="json")
    return dumped


# ------------------------------------------------------------- descriptions


@pytest.mark.parametrize("prompt", PROMPTS, ids=lambda prompt: str(prompt.name))
def test_every_registered_prompt_declares_a_description(prompt: RegisteredPrompt) -> None:
    """The description is the slash-command label a caller picks from."""
    assert (prompt.description or "").strip(), f"{prompt.name} has no description"


@pytest.mark.parametrize("prompt", PROMPTS, ids=lambda prompt: str(prompt.name))
def test_every_registered_prompt_argument_declares_a_description(
    prompt: RegisteredPrompt,
) -> None:
    """An argument with no description is one a caller has to guess at."""
    undescribed = [
        argument.name
        for argument in prompt.arguments or []
        if not (argument.description or "").strip()
    ]
    assert not undescribed, f"{prompt.name} has undescribed arguments: {undescribed}"


@pytest.mark.parametrize("resource", RESOURCES, ids=lambda row: str(row.uri))
def test_every_registered_resource_declares_a_description_and_json(
    resource: RegisteredResource,
) -> None:
    assert (resource.description or "").strip(), f"{resource.uri} has no description"
    assert resource.mime_type == JSON_MIME, (
        f"{resource.uri} advertises {resource.mime_type!r}; the SDK's default is text/plain, "
        f"so this has to be passed rather than assumed"
    )


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda row: str(row.uri_template))
def test_every_registered_template_declares_a_description_and_json(
    template: RegisteredTemplate,
) -> None:
    assert (template.description or "").strip(), f"{template.uri_template} has no description"
    assert template.mime_type == JSON_MIME, f"{template.uri_template} is not JSON"


# ------------------------------------------------------------- runtime, empty store


def minimal_arguments(prompt: RegisteredPrompt) -> dict[str, str]:
    """The emptiest legal argument set, from the prompt's own registration.

    ``""`` for each required argument, which is the same choice
    `test_tool_surface.py::sample` makes and for the same reason: the call
    should reach the prompt and exercise its own handling rather than depend on
    anything being in the store. ``prompts/get`` takes ``dict[str, str]``, so
    there is no other type to synthesise.
    """
    return {argument.name: "" for argument in prompt.arguments or [] if argument.required}


@pytest.mark.parametrize("prompt", PROMPTS, ids=lambda prompt: str(prompt.name))
async def test_every_registered_prompt_answers_a_message(
    context: ServiceContext, prompt: RegisteredPrompt
) -> None:
    """Mechanical every-prompt coverage, derived from the registry.

    The store is **empty**, which is the point: this is the case where a prompt
    is most tempted to name an example that does not exist, and every prompt
    must still answer usable text rather than raising or returning nothing.
    """
    async with connected(context) as client:
        result = await client.get_prompt(prompt.name, minimal_arguments(prompt))
    assert result.messages, f"{prompt.name} returned no messages"
    for message in result.messages:
        assert message.role == "user", f"{prompt.name} returned a {message.role} message"
        assert message.content.type == "text", f"{prompt.name} returned non-text content"
        text = getattr(message.content, "text", "")
        assert text.strip(), f"{prompt.name} returned an empty message"


@pytest.mark.parametrize("uri", RESOURCE_URIS)
async def test_every_registered_resource_answers_json(context: ServiceContext, uri: str) -> None:
    """Every static resource parses as JSON on an empty store, and says so honestly."""
    body = await read_json(context, uri)
    assert isinstance(body, dict), f"{uri} did not return a JSON object"
    assert body, f"{uri} returned an empty object, which says nothing"


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda row: str(row.uri_template))
async def test_every_registered_template_answers_json(
    context: ServiceContext, template: RegisteredTemplate
) -> None:
    """Template URIs are expanded from the registered template, not written out."""
    parsed = UriTemplate.parse(template.uri_template)
    uri = parsed.expand(dict.fromkeys(parsed.variable_names, "probe"))
    body = await read_json(context, uri)
    assert isinstance(body, dict), f"{uri} did not return a JSON object"
    assert body.get("available") is False, (
        f"{uri} claims a document is available in an empty store; a resource that promises "
        f"an example this store does not hold is the dishonesty ruling R-76 names"
    )
    assert (body.get("note") or "").strip(), f"{uri} reports unavailable without saying why"


async def test_a_template_and_its_builder_agree(context: ServiceContext) -> None:
    """The registered template must match the URI `service/examples.py` builds.

    ``BLUEPRINT_URI_TEMPLATE`` is used two ways - as an RFC 6570 template in the
    ``@mcp.resource`` decorator and as a ``str.format`` template in
    :func:`~agentprops.service.examples.blueprint_uri` - because the two
    syntaxes coincide for the simple ``{name}`` form. That coincidence is
    load-bearing: it is what stops the URI a resource is registered at from
    drifting from the URI a prompt and the catalogue name. So it is
    round-tripped rather than trusted, including a value that needs escaping.
    """
    built = examples.blueprint_uri("agent with spaces", "1.0.0")
    matched = UriTemplate.parse(examples.BLUEPRINT_URI_TEMPLATE).match(built)
    assert matched == {"agent_id": "agent with spaces", "version": "1.0.0"}, (
        f"the registered template does not match its own builder's output {built!r}"
    )
    assert examples.BLUEPRINT_URI_TEMPLATE in TEMPLATE_URIS
    assert examples.AGENT_EXAMPLE_DATASET_URI_TEMPLATE in TEMPLATE_URIS

    dataset_uri = examples.agent_example_dataset_uri("loc/al")
    assert UriTemplate.parse(examples.AGENT_EXAMPLE_DATASET_URI_TEMPLATE).match(dataset_uri) == {
        "agent_id": "loc/al"
    }
    body = await read_json(context, built)
    assert body["agent_id"] == "agent with spaces", "the template did not decode its variable"


@pytest.mark.parametrize("uri", RESOURCE_URIS)
async def test_no_resource_body_is_an_envelope(context: ServiceContext, uri: str) -> None:
    """Ruling R-43(b): ``resources/read`` has its own shape, not a ``SuccessEnvelope``."""
    body = await read_json(context, uri)
    offences = ENVELOPE_KEYS & set(body)
    assert not offences, (
        f"{uri} carries envelope keys {sorted(offences)}; R-76 says explicitly not to wrap "
        f"resources/read in a SuccessEnvelope"
    )


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda row: str(row.uri_template))
async def test_no_template_body_is_an_envelope(
    context: ServiceContext, template: RegisteredTemplate
) -> None:
    """The same for the templates, asserted rather than inferred.

    The template bodies come from the same two ``service/examples.py`` functions
    the static resources use, so the property holds by construction today - and
    "by construction" is the kind of reasoning this build has repeatedly found to
    be one refactor out of date. One parametrised assertion costs nothing.
    """
    parsed = UriTemplate.parse(template.uri_template)
    uri = parsed.expand(dict.fromkeys(parsed.variable_names, "probe"))
    body = await read_json(context, uri)
    offences = ENVELOPE_KEYS & set(body)
    assert not offences, f"{uri} carries envelope keys {sorted(offences)}"


@pytest.mark.parametrize("prompt", PROMPTS, ids=lambda prompt: str(prompt.name))
async def test_every_prompt_serves_prose_and_not_a_serialised_document(
    context: ServiceContext, prompt: RegisteredPrompt
) -> None:
    """``prompts/get`` answers with text a model reads, not with a document.

    The **positive** half, and the one that actually asserts something today.
    An earlier version of this file had only the negative half below, which
    ``continue``s past any message that does not parse as JSON - so against all
    four current prompts it asserted nothing at all while reading as coverage.
    That is the vacuous-guard shape ruling R-77(e) is about, caught in review
    rather than by running it, which is worse.
    """
    async with connected(context) as client:
        result = await client.get_prompt(prompt.name, minimal_arguments(prompt))
    for message in result.messages:
        text = getattr(message.content, "text", "")
        assert as_json_object(text) is None, (
            f"{prompt.name} served a JSON object rather than prompt text; prompts/get has no "
            f"envelope and its content is prose (ruling R-43(b))"
        )


@pytest.mark.parametrize("prompt", PROMPTS, ids=lambda prompt: str(prompt.name))
async def test_no_prompt_message_is_an_envelope(
    context: ServiceContext, prompt: RegisteredPrompt
) -> None:
    """Ruling R-43(b) for ``prompts/get``, and **dormant while every prompt is prose**.

    Stated rather than implied: the test above asserts no current prompt returns
    a JSON object, so this one's body does not execute for any of the four. It is
    a tripwire for the day a prompt legitimately serves a structured message -
    then it starts asserting that the structure is not a ``SuccessEnvelope``. It
    is *not* evidence about today's surface, and the previous docstring let it
    read as if it were.
    """
    async with connected(context) as client:
        result = await client.get_prompt(prompt.name, minimal_arguments(prompt))
    for message in result.messages:
        parsed = as_json_object(getattr(message.content, "text", ""))
        if parsed is None:
            continue
        assert not (ENVELOPE_KEYS & set(parsed)), (
            f"{prompt.name} returned a serialised envelope instead of prompt text"
        )


def as_json_object(text: str) -> dict[str, Any] | None:
    """``text`` parsed, if it is a JSON object; ``None`` otherwise."""
    try:
        parsed = json.loads(text)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


async def read_json(context: ServiceContext, uri: str) -> dict[str, Any]:
    """One ``resources/read``, parsed. Asserts the mime type on the way through."""
    async with connected(context) as client:
        result = await client.read_resource(uri)
    assert result.contents, f"{uri} returned no contents"
    payload: dict[str, Any] = {}
    for item in result.contents:
        assert item.mime_type == JSON_MIME, f"{uri} served {item.mime_type!r}"
        payload = json.loads(getattr(item, "text", ""))
    return payload


# ------------------------------------------------------------- coverage


def prompt_names_in_test_sources() -> set[str]:
    return literals_passed_to(TESTS_DIR, PROMPT_CALL_FUNCTIONS)


def resource_uris_in_test_sources() -> set[str]:
    return literals_passed_to(TESTS_DIR, RESOURCE_CALL_FUNCTIONS)


def test_the_prompt_scanner_finds_something() -> None:
    """Blames the scanner rather than the suite when the parse comes back empty."""
    found = prompt_names_in_test_sources()
    assert len(found) >= 3, f"the AST scan of {TESTS_DIR} found only {sorted(found)}"


def test_the_resource_scanner_finds_something() -> None:
    found = resource_uris_in_test_sources()
    assert len(found) >= 3, f"the AST scan of {TESTS_DIR} found only {sorted(found)}"


def test_every_registered_prompt_has_a_dedicated_test() -> None:
    """Every registered prompt name appears in a ``get_prompt`` call under `tests/`.

    Enumerated from the registry, so a fourth prompt fails this the moment it is
    registered - and the message names it rather than saying a count is wrong.
    """
    unexercised = set(PROMPT_NAMES) - prompt_names_in_test_sources()
    assert not unexercised, (
        f"these prompts are registered but no test calls them by name: {sorted(unexercised)}"
    )


def test_every_registered_resource_has_a_dedicated_test() -> None:
    unexercised = set(RESOURCE_URIS) - resource_uris_in_test_sources()
    assert not unexercised, (
        f"these resources are registered but no test reads them by URI: {sorted(unexercised)}"
    )


def templates_matching(uri: str, templates: Sequence[str] = TEMPLATE_URIS) -> list[str]:
    """Every template in ``templates`` whose pattern accepts ``uri``.

    ``templates`` is a parameter so the crediting rule below can be unit-tested
    against a *hypothetical* over-broad template without registering one on the
    live server, which would leak into every other test in the process.
    """
    return [
        template for template in templates if UriTemplate.parse(template).match(uri) is not None
    ]


def credits(uri: str, template: str, templates: Sequence[str] = TEMPLATE_URIS) -> bool:
    """Whether a test that reads ``uri`` counts as covering ``template``.

    The crediting rule, in one place so the guard and its control apply the
    same one. A URI credits a template only when the resource manager would
    actually route it there: not when a **static** resource claims it (concrete
    resources are checked first), and not when **another** template also
    matches it (the manager routes it to whichever registered first, leaving the
    other untested).
    """
    return uri not in RESOURCE_URIS and templates_matching(uri, templates) == [template]


def test_every_registered_resource_template_has_a_dedicated_test() -> None:
    """A template is covered by a test that reads a URI **only that template** serves.

    Matched with the SDK's own RFC 6570 matcher rather than by string prefix, so
    the guard agrees with the resource manager about which template answers a
    URI - the only definition of "covered" that means anything here.

    Two exclusions close the breadth hole, which is the mirror image of the
    prefix hazard :data:`~agentprops.service.examples.AGENT_EXAMPLE_DATASET_URI_TEMPLATE`'s
    segment order was chosen to avoid. A template broad enough to accept a URI
    something else already serves would otherwise be credited for free:

    - a **static** resource's own URI does not credit a template, because the
      resource manager checks concrete resources first and would never route
      that URI to the template at all;
    - a URI matching **more than one** template credits neither, because the
      manager routes it to whichever comes first in registration order and the
      other one is untested.

    So ``agentprops://{a}/{b}`` cannot ride on the existing tests, which
    :func:`test_the_crediting_rule_rejects_the_ways_a_template_could_ride_free`
    proves rather than asserts in prose.

    **One case this rule cannot decide**, named so a reader knows the boundary
    rather than assuming there is none: a URI that a *miss* test asserts is
    unroutable - ``agentprops://catalogue/no-such-thing`` - would be routed to an
    over-broad template if one were registered, and would credit it. That is
    caught by the miss test failing (the read would succeed and report no error),
    not by this guard. Two guards, one property, failing at different moments.
    """
    tested = resource_uris_in_test_sources()
    uncovered = [
        template for template in TEMPLATE_URIS if not any(credits(uri, template) for uri in tested)
    ]
    assert not uncovered, (
        f"no test reads a URI served by these templates alone: {uncovered}; a template covered "
        f"only by a synthesised probe inside this file, by a static resource's URI, or by a URI "
        f"another template also matches is covered by nothing"
    )


def test_the_crediting_rule_rejects_the_ways_a_template_could_ride_free() -> None:
    """:func:`credits`, unit-tested against a hypothetical over-broad template.

    ``agentprops://{a}/{b}`` is the mirror image of the prefix hazard the
    per-agent template's segment order was chosen to avoid: broad enough to
    accept URIs other things already serve. Three assertions for the three ways
    it could be credited, and a fourth so the rule is not vacuously strict -
    without that last one, a ``credits`` that always returned ``False`` would
    pass this test and fail every template forever.

    The hypothetical template is passed in rather than registered, because
    registering one on the module-level ``mcp`` instance would leak into every
    other test in the process.
    """
    two = "agentprops://{a}/{b}"
    three = "agentprops://{a}/{b}/{c}"
    hypothetical = (*TEMPLATE_URIS, two, three)

    static = examples.EXAMPLE_BLUEPRINT_URI
    assert static in RESOURCE_URIS
    assert UriTemplate.parse(two).match(static) is not None, "the control URI is not accepted"
    assert not credits(static, two, hypothetical), (
        "a static resource's URI credited a template; the resource manager checks concrete "
        "resources first and would never route it there"
    )

    shared = examples.blueprint_uri("location-onboarding", "1.0.0")
    assert set(templates_matching(shared, hypothetical)) == {
        three,
        examples.BLUEPRINT_URI_TEMPLATE,
    }, "the ambiguity control does not actually collide with a registered template"
    assert not credits(shared, three, hypothetical), "an ambiguous URI credited a template"
    assert not credits(shared, examples.BLUEPRINT_URI_TEMPLATE, hypothetical), (
        "an ambiguous URI credited the template that happens to be registered first, which is "
        "an ordering dependency rather than coverage"
    )

    assert credits("agentprops://only/broad", two, hypothetical), (
        "the rule credits nothing at all, so every template would fail its coverage test"
    )


def test_the_scanners_do_not_credit_a_name_nobody_calls() -> None:
    """The guards' own guard: a scanner must not match an arbitrary string literal.

    Without this, a scanner that collected every string in the test tree would
    pass the coverage tests forever - including for a name that appears only in
    a docstring, a comment or a constant like the controls above.

    Three assertions, because two of them keep the control honest: the control
    must not be registered (or the test would be asserting about a real entry),
    and it must appear as a literal *somewhere* under `tests/` (or the scan
    would trivially not find it and this would prove nothing).
    """
    assert UNCALLED_PROMPT_CONTROL not in PROMPT_NAMES
    assert UNCALLED_RESOURCE_CONTROL not in RESOURCE_URIS
    sources = "".join(path.read_text(encoding="utf-8") for path in TESTS_DIR.rglob("test_*.py"))
    assert f'"{UNCALLED_PROMPT_CONTROL}"' in sources
    assert f'"{UNCALLED_RESOURCE_CONTROL}"' in sources
    assert UNCALLED_PROMPT_CONTROL not in prompt_names_in_test_sources()
    assert UNCALLED_RESOURCE_CONTROL not in resource_uris_in_test_sources()


# ------------------------------------------------------------- derived, not literal


def test_the_endpoint_the_wiring_prompt_shows_is_the_one_the_server_serves_on() -> None:
    """``service/prompts.py`` spells the default URL, and it must still be true.

    `service/` may not import `server/`, so the URL cannot be built from
    ``DEFAULT_HTTP_HOST``/``PORT``/``PATH`` where it is used - which leaves a
    literal, which is the shape of claim that goes stale silently. This is the
    test that makes it fail loudly instead: it rebuilds the URL from the
    server's own three constants and compares.
    """
    served = f"http://{DEFAULT_HTTP_HOST}:{DEFAULT_HTTP_PORT}{DEFAULT_HTTP_PATH}"
    assert served == prompts.DEFAULT_HTTP_URL, (
        f"the prompt tells a caller to connect to {prompts.DEFAULT_HTTP_URL}, but "
        f"`python -m agentprops.server --transport http` serves on {served}"
    )


def test_the_prompt_states_the_live_blueprint_rule_count(context: ServiceContext) -> None:
    """The ``BP-*`` count in ``author-a-blueprint`` is derived from the registry.

    A prompt that spelled "Nineteen" would be a claim that goes stale the
    milestone a twentieth ``BP-*`` rule lands - the class of false statement
    this build has spent nine milestones learning to distrust, and one the
    README's own step-2 text carried. So the number is counted at call time and
    re-counted here, independently, from the registry rather than from the
    prompt module's own helper.
    """
    expected = sum(1 for rule in RULE_REGISTRY if rule.startswith("BP-"))
    assert expected > 0, "the registry enumeration found no BP-* rules"
    text = prompts.author_a_blueprint(context, "")
    assert f"{expected} `BP-*` rules" in text, (
        f"the prompt does not state the live count of {expected} BP-* rules"
    )
