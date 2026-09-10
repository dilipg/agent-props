"""The four registered MCP prompts' text, composed against **this** store.

Ground rule 4 is not violated by this module, and the word invites the reading
-----------------------------------------------------------------------------

"**No LLM inside the service.** No model client, no API key handling, no judge
anywhere in `src/`." An MCP *prompt* is templated text served over a protocol
method - the same category as a tool's description, which nobody reads as a
model call. Nothing here constructs a client, holds a key, scores an answer or
sends a request anywhere: every function below returns a ``str`` that the
protocol hands to the caller, and the caller's model reads it. Ruling R-76 says
to state that here, because the word "prompt" reads like an inference and this
module would otherwise look like the one place the ground rule was bent.

What each prompt owns, and what it deliberately does not say
------------------------------------------------------------

R-76 names duplication as this milestone's real risk: "if a prompt restates the
README rather than replacing it, this milestone has added a second place to
drift and fixed nothing". The same hazard exists one layer in, against the tool
surface, so the division is explicit:

``author-a-blueprint``
    **The long one, and it has to be.** There is no ``blueprint_skeleton``, so
    nothing in the tool surface tells a caller the blueprint shape before they
    write one; ``blueprint_validate`` is a good feedback loop and only after a
    guess. So this prompt carries the node/edge/entity/label contract in full.
    That text used to be `README.md` step 2 and has **moved** here rather than
    been copied - the README now points at the prompt.

``fill-a-dataset``
    **Deliberately short.** ``dataset_skeleton`` already returns an
    ``instructions`` field, derived from the manifest, that states the fill
    order and its rule id (SK-002), that a re-fill is how a rejection is
    repaired, the content shape each section takes, and that
    ``label_vocabulary`` is the pre-flight for the one input a re-fill cannot
    repair. Restating any of that here would be R-76's duplication one layer
    in. This prompt supplies **intent only**: which agent, which version, which
    scenarios, and what to report. The one sequencing fact it does carry is the
    orientation step, because that happens *before* a caller has a skeleton to
    read ``instructions`` from.

``cover-the-label-space``
    **Worth more than its README equivalent**, because it can only be written
    against live store contents: it names the label values that currently have
    **zero** datasets, and the label combinations already present. A README
    cannot know either.

``wire-an-agent``
    The one about editing the *caller's* code rather than authoring a document.
    R-76's scope names three prompts "at minimum"; this is the fourth because
    `README.md` step 4 carried a prompt body too, and leaving that one behind
    would have left exactly the second place to drift the milestone is about.
    Store-aware in one respect the README could not be: the ``run_start``
    selector it shows is a real dataset's own label set.

Why "combination" means a ``dimension=value`` pair here
-------------------------------------------------------

The obvious reading of "name the combinations with zero datasets" is the
cross-product of every dimension. That is not a list worth serving: the golden
blueprint declares five dimensions, and a realistic vocabulary makes the product
run to thousands - so a prompt enumerating it would hand a caller a task nobody
can finish, which is its own kind of dishonesty. It is also not what the
measurement supports: ``label_vocabulary`` returns counts **per value**, and the
PRD's exit criterion is twenty datasets *spanning* the declared labels, not the
whole product.

So :func:`cover_the_label_space` reports two complete, bounded things - every
``dimension=value`` with no dataset, and every label tuple already present - and
states the size of the cross-product as a number without listing it. Both lists
are complete rather than truncated, which is what lets a caller act on them.

Nothing here promises what the store does not hold
--------------------------------------------------

Every function resolves what it is about to name **before** naming it. An agent
with no published blueprint, a version that does not exist, a store with nothing
in it at all: each produces text that says so and says what to do next, rather
than a prompt telling a caller to imitate ``location-onboarding`` in a store
that never heard of it. That specific defect is the one R-76 cites, and
`service/examples.py` holds the one decision site both this module and
``resources/read`` pick an example with.

These are not envelopes (ruling R-43(b))
----------------------------------------

``prompts/get`` has its own protocol shape. Every function here returns a plain
``str``, not a ``Reply``: there is no ``SuccessEnvelope``, no ``data`` key and no
``errors`` list on this surface, and R-76 says so explicitly.
"""

from __future__ import annotations

from collections.abc import Mapping
from math import prod
from typing import Final

from agentprops.models import Blueprint, DatasetQuery, DatasetSummary
from agentprops.service import examples
from agentprops.service.admin import value_counts
from agentprops.service.context import ServiceContext
from agentprops.storage import STATUS_PUBLISHED
from agentprops.validation import RULE_REGISTRY

__all__ = [
    "BLUEPRINT_RULE_PREFIX",
    "DEFAULT_HTTP_URL",
    "DEFAULT_SCENARIOS",
    "author_a_blueprint",
    "cover_the_label_space",
    "fill_a_dataset",
    "wire_an_agent",
]

#: The endpoint ``wire-an-agent`` shows when the caller names none. Built from
#: `server/app.py`'s own defaults would be the layering rule backwards -
#: `service/` may not import `server/` - so the three parts are spelled here and
#: ``test_prompt_and_resource_surface.py`` asserts they still match what the
#: server would serve on.
DEFAULT_HTTP_URL: Final = "http://127.0.0.1:8000/mcp"

#: What ``fill-a-dataset`` asks for when the caller names no scenarios. These
#: are *intent*, which is what that prompt owns: the three shapes a suite needs
#: before it needs anything else. They moved out of `README.md` step 3.
DEFAULT_SCENARIOS: tuple[str, ...] = (
    "the happy path, no edge cases",
    "the retry loop firing exactly once",
    "the escalation or failure branch",
)

#: The blueprint shape contract. Moved from `README.md` step 2, not copied from
#: it: nothing in the tool surface carries this, so if it is not here it is
#: nowhere a caller can reach over MCP.
_BLUEPRINT_CONTRACT = """\
Then model *this* repo's agent:

- **One node per step the agent actually takes.** Set `tool_name` to the tool it really
  calls, verbatim. Set `kind` to `tool_call`, `decision`, `loop` or `terminal`.
- **`entry_node`** is the step that starts a run, and it must have no inbound edges.
- **Edges** carry a JSONLogic `condition` for every branch out of a `decision`. Every
  `{"var": "..."}` path must name a property the source node's `output_schema` actually
  declares - that is checked, and it is the rule most blueprints fail first.
- **Retry loops** get `kind: loop`, an integer `max_iterations`, and `pool: true`. Every
  cycle in the graph must pass through a loop node.
- **At least one `kind: terminal` node**, with no outbound edges.
- **`entities`**: one per domain noun that persists across steps - the thing the agent
  reads at step 2 and reads again at step 5. Give each a Draft 2020-12 JSON Schema, and
  reference them from node schemas as `{"$ref": "entity:<id>"}`.
- **`input_schema` and `output_schema`** per node, Draft 2020-12.
- **`outcome_schema`**: the shape of a finished run's result.
- **`label_schema`**: the dimensions you would filter datasets by - persona, scenario,
  tier, outcome, plus an `edge_case` dimension whose vocabulary includes `none`. Every
  dimension needs at least one value, and a dataset must later carry a value for *every*
  dimension - so keep the set small and give each an explicit not-applicable value.
- **`notes`** on each node: what the step does and what a realistic fixture looks like.
  Absent notes are a warning, and they make generated data worse."""

#: The prefix :func:`_validate_loop` counts. A prompt that states a rule count
#: as a literal is a claim that goes stale the milestone a rule is added, which
#: is the class of false claim this build keeps finding, so the number is
#: derived and ``test_prompt_and_resource_surface.py`` re-derives it.
BLUEPRINT_RULE_PREFIX: Final = "BP-"


def _validate_loop() -> str:
    """The validate loop, with the ``BP-*`` count read off the live registry."""
    count = sum(1 for rule in RULE_REGISTRY if rule.startswith(BLUEPRINT_RULE_PREFIX))
    return (
        "Then loop: call `blueprint_validate` and fix **every** rule id it reports. Do not "
        "call `blueprint_upsert` until `blueprint_validate` returns no `error`-severity "
        "findings. Then `blueprint_upsert` with `publish: true`.\n\n"
        f"{count} `BP-*` rules are waiting, and the one worth knowing about up front is "
        "**BP-014**: two nodes sharing a `tool_name` where position cannot disambiguate "
        "them. That is the mistake that makes an agent untestable, because `fetch_step` "
        "then cannot tell which step you mean.\n\n"
        "Report the final node and edge count, and any `BP-019` warnings you chose to leave."
    )


def author_a_blueprint(context: ServiceContext, agent_id: str = "") -> str:
    """Author a blueprint for the repository the caller is sitting in.

    ``agent_id`` names a published blueprint to imitate. Omitted, the store
    picks one; if the store has none, the text says so instead of inventing an
    id - which is the concrete defect ruling R-76 cites in the README's own
    step-2 prompt.
    """
    return (
        "Read this repository's agent and author an agent-props blueprint for it, using "
        "the `agent-props` MCP tools.\n\n"
        f"{_orientation(context, agent_id)}\n\n"
        f"{_BLUEPRINT_CONTRACT}\n\n"
        f"{_validate_loop()}"
    )


def fill_a_dataset(
    context: ServiceContext, agent_id: str, version: str = "", scenarios: str = ""
) -> str:
    """Author one dataset per scenario, for one blueprint version.

    Short by design: ``dataset_skeleton``'s ``instructions`` field carries the
    mechanics, so this carries intent. See the module docstring.

    Resolved through :func:`~agentprops.service.examples.published_blueprint`
    rather than through ``Store.get_blueprint``, and that is a fix rather than a
    preference. ``get_blueprint`` returns an exact version "whatever its status",
    so an explicit **draft** version resolved and this prompt told a caller to
    author datasets against it - which ``dataset_skeleton`` refuses (it goes
    through ``get_published_blueprint``) and DS-001 rejects regardless, since it
    requires "an existing *published* blueprint at that exact version". Worse,
    the not-found branch of the same call already said "no **published**
    blueprint", so the two branches contradicted each other about what the
    prompt was even about. One call site, and the status check lives where the
    resource surface already made it.
    """
    blueprint = examples.published_blueprint(context, agent_id, version)
    if blueprint is None:
        return _no_such_blueprint(agent_id, version, published=True)
    listed = "\n".join(
        f"{index}. {scenario}" for index, scenario in enumerate(_scenarios(scenarios), start=1)
    )
    return (
        f"Author agent-props datasets for `{blueprint.agent_id}` at `{blueprint.version}`, "
        "one per scenario below.\n\n"
        f"Orient first: call `label_vocabulary` with those two arguments for the dimensions "
        f"and values this blueprint declares, and read {examples.EXAMPLE_DATASET_URI} for a "
        "filled dataset to imitate.\n\n"
        "Then, per scenario: call `dataset_skeleton` with a complete label set and a seed, "
        "**read the `instructions` field it returns and follow it**, and submit. Those "
        "instructions are the mechanics - fill order, how to repair a rejection, what each "
        "section's content looks like - and this prompt deliberately does not repeat them.\n\n"
        f"Scenarios to cover:\n{listed}\n\n"
        "Report each dataset's title, its labels, and any warnings the submit returned."
    )


def cover_the_label_space(context: ServiceContext, agent_id: str, version: str = "") -> str:
    """Close the gaps in one blueprint version's declared label space.

    The one prompt that could not be written as a README paragraph: the values
    with no dataset and the tuples already present are both facts about this
    store at this moment.

    The raw ``get_blueprint`` here is deliberate and is **not** the bug
    :func:`fill_a_dataset` had. This prompt mirrors ``label_vocabulary``
    (`service/admin.py`), which reports a *draft* version's vocabulary quite
    correctly - it is a read, and there is a real answer. What a draft cannot do
    is receive a dataset (DS-001 needs a published version at that exact
    version), so the coverage advice would be unactionable without saying why:
    :func:`_draft_caveat` says it, and the not-found branch no longer claims
    "published" on a path that does not require it.
    """
    blueprint = context.store.get_blueprint(agent_id, version or None)
    if blueprint is None:
        return _no_such_blueprint(agent_id, version, published=False)
    dimensions = blueprint.label_schema.dimensions
    rows = context.store.find_datasets(
        DatasetQuery(agent_id=blueprint.agent_id, blueprint_version=blueprint.version)
    )
    return (
        f"Add agent-props datasets for `{blueprint.agent_id}` at `{blueprint.version}` until "
        "its declared label space is covered.\n\n"
        f"{_draft_caveat(blueprint)}"
        f"This store holds {len(rows)} dataset(s) for that version.\n\n"
        f"{_empty_values(dimensions, rows)}\n\n"
        f"{_present_tuples(dimensions, rows)}\n\n"
        f"{_space_size(dimensions)} Twenty datasets that span the declared labels is the bar "
        "PRD 6 sets, not one dataset per combination - so prefer a dataset that closes an "
        "empty value above and whose whole label tuple is not already present.\n\n"
        "For each one, follow the `fill-a-dataset` prompt for this agent and version. Report "
        "the labels you chose per dataset, and which of the empty values above are still empty "
        "when you stop."
    )


def wire_an_agent(context: ServiceContext, agent_id: str = "", url: str = "") -> str:
    """Put a fixture mode into the caller's own agent, behind one seam.

    The fourth prompt, and the only one about editing the *caller's* code rather
    than authoring a document. It exists because `README.md` step 4 carried a
    prompt too, and leaving that one body behind in the README would have left
    exactly the second place to drift that ruling R-76 is about.

    Store-aware in one respect that the README version could not be: the
    ``run_start`` selector it shows is a real dataset's own label set from this
    store, so the snippet a caller pastes selects something that exists.
    """
    endpoint = url or DEFAULT_HTTP_URL
    return (
        "Add an agent-props fixture mode to this agent, for tests.\n\n"
        f"{_seam()}\n\n"
        f"{_client_sequence(context, agent_id, endpoint)}\n\n"
        f"{_CLIENT_GOTCHAS}"
    )


def _seam() -> str:
    """Where the seam goes. The half of step 4 that is about code shape."""
    return (
        "Read this agent's entry point and find every outbound tool call. Introduce a "
        "**single seam** - one injected client, or one module-level indirection - so that in "
        "fixture mode each of those calls is replaced by `props.fetch_step(...)` and nothing "
        "else changes. Do not scatter conditionals through the agent's logic. Keep the real "
        "tool path as the default and make fixture mode opt-in via one environment variable."
    )


def _client_sequence(context: ServiceContext, agent_id: str, endpoint: str) -> str:
    """The call sequence, with a selector this store can actually serve.

    The README's version hard-coded ``{"scenario": "missing-documents"}``, which
    is wrong in any store that has not seeded the demo fixtures - the same
    defect ruling R-76 cites one step earlier. Here the labels come off a real
    dataset, and when there is none the text says so rather than inventing one.
    """
    dataset = examples.example_dataset(context, agent_id)
    if dataset is None:
        return (
            "Use `agentprops_client`: `connect(url, agent_id=...)`, then `run_start` once with "
            "a selector, then `fetch_step(node_id=...)` - or `fetch_step(tool_name=...)` where "
            "the agent only knows which tool it is calling. Pass `iteration=` for a loop node, "
            "counting from 0.\n\n"
            "This store holds no dataset yet, so there is no selector to show you. Run the "
            '`fill-a-dataset` prompt first; then a `run_start({"labels": {...}})` naming any '
            "stored dataset's labels will pin one."
        )
    labels = ", ".join(f'"{name}": "{value}"' for name, value in dataset.labels.items())
    return (
        f"Use `agentprops_client`, pointed at `{endpoint}`. The call order, once per run:\n\n"
        "```python\n"
        f'props = connect("{endpoint}", agent_id="{dataset.blueprint.agent_id}")\n'
        f'start = props.run_start({{"labels": {{{labels}}}}})\n'
        "served = props.fetch_step(node_id=...)   # or tool_name=..., iteration=N\n"
        "props.record_step(actual, node_id=...)\n"
        "props.run_finish(outcome)\n"
        "```\n\n"
        "Afterwards, read the run back over the tool surface rather than out of the agent: "
        "`run_find` lists runs, `run_get` returns one whole with its pin, its path and every "
        "step, and `run_evidence` hands you expectation and result together with the mode to "
        "judge them by. Grading stays yours - none of the three computes a verdict.\n\n"
        "`connect` is also a context manager, so a real test should hold it in a `with` block "
        "rather than leaking the session.\n\n"
        f"Those labels are a real dataset in this store - `{dataset.provenance.title}` - so the "
        "selector pins something rather than reporting no match. A subset of them also selects: "
        "several matches assign the first row and warn."
    )


#: The three things step 4 said would bite otherwise, plus where grading lives.
#: Each one is a fact about the client's contract that no tool description
#: carries, which is why this is a prompt rather than a docstring somewhere.
#:
#: Worded so it does **not** share a nine-word run with `README.md` - the
#: gotchas are named in the "Rough edges" list there too, and ruling R-78's
#: guard is what now holds the two apart.
_CLIENT_GOTCHAS = """\
Three mistakes cost the most time on the way in. A `record_step` for a step you never
served is `AP-004`, so fetch first. A reply's warnings are objects rather than dicts,
so reach for `w.code`. And the run id belongs to the client, which generates one at
construction - do not mint your own.

Grade in the **test**, never in the agent: `compare.grade(mode, expected, actual)`, with
`mode` taken from the dataset's own `expected.comparison`. This service holds the
expectation and returns evidence; the comparison is yours.

Report the seam you introduced, the environment variable that switches it on, and any
tool call you could not route through `fetch_step`."""


def _orientation(context: ServiceContext, agent_id: str) -> str:
    """The "imitate this" paragraph, or an honest statement that there is nothing to.

    One decision site, in `service/examples.py`, shared with ``resources/read``
    - so the prompt cannot name an example the resource does not serve.
    """
    blueprint = examples.example_blueprint(context, agent_id, "")
    if blueprint is None:
        return (
            "This store holds no published blueprint"
            + (f" for `{agent_id}`" if agent_id else "")
            + ", so there is no example to imitate and the contract below is the whole "
            "shape. Nothing needs seeding first: publish yours and it becomes the example "
            "the next caller reads. `agent_list` shows what the store does hold."
        )
    return (
        "First orient yourself on a known-good example rather than guessing the shape. This "
        f"store has one: `{blueprint.agent_id}` at `{blueprint.version}`. Read it as the "
        f"resource {examples.blueprint_uri(blueprint.agent_id, blueprint.version)}, or with "
        f"`blueprint_get`. Copy its structure, not its content - `resources/list` names "
        "everything else this store can show you, so no id has to be guessed."
    )


def _scenarios(scenarios: str) -> list[str]:
    """The caller's scenarios, one per line, or :data:`DEFAULT_SCENARIOS`.

    Split on newlines because a scenario description contains commas far more
    often than it contains a newline, and a prompt argument is a single string
    on the wire (``prompts/get`` takes ``dict[str, str]``). Leading list markers
    are stripped so a pasted numbered list does not come back double-numbered.
    """
    lines = [line.strip().lstrip("-*0123456789. \t") for line in scenarios.splitlines()]
    given = [line for line in lines if line]
    return given or list(DEFAULT_SCENARIOS)


def _empty_values(dimensions: Mapping[str, list[str]], rows: list[DatasetSummary]) -> str:
    """Every ``dimension=value`` with no dataset. Complete, never truncated."""
    counts = value_counts(dimensions, rows)
    empty = [
        f"- `{name}={value}`"
        for name, values in counts.items()
        for value, count in values.items()
        if count == 0
    ]
    if not empty:
        return "Every declared label value already carries at least one dataset."
    return "These declared label values carry **no** dataset at all:\n" + "\n".join(empty)


def _present_tuples(dimensions: Mapping[str, list[str]], rows: list[DatasetSummary]) -> str:
    """The label tuples already stored, in the blueprint's dimension order.

    Bounded by the dataset count rather than by the vocabulary, which is what
    makes listing them safe where listing the cross-product is not.
    """
    if not rows:
        return "No label combination is present yet."
    seen: dict[str, int] = {}
    for row in rows:
        key = ", ".join(f"{name}={row.labels.get(name, '?')}" for name in dimensions)
        seen[key] = seen.get(key, 0) + 1
    listed = "\n".join(f"- `{key}` ({count})" for key, count in seen.items())
    return "Label combinations already present, with how many datasets carry each:\n" + listed


def _space_size(dimensions: Mapping[str, list[str]]) -> str:
    """The cross-product as a number, deliberately not as a list."""
    total = prod(len(values) for values in dimensions.values()) if dimensions else 0
    return (
        f"The vocabulary declares {len(dimensions)} dimension(s), a cross-product of "
        f"{total} combination(s)."
    )


def _draft_caveat(blueprint: Blueprint) -> str:
    """One line when the resolved version is a draft, and nothing when it is not.

    ``cover-the-label-space`` reports a draft version's vocabulary because
    ``label_vocabulary`` does, and that is a real answer. But DS-001 requires a
    dataset to name "an existing *published* blueprint at that exact version",
    so a caller who acted on the coverage list would get every submit rejected.
    Naming that is the difference between mirroring the tool and repeating a
    trap.
    """
    if blueprint.status == STATUS_PUBLISHED:
        return ""
    return (
        f"**`{blueprint.version}` is a draft.** `dataset_skeleton` serves published versions "
        "only and DS-001 requires a published version at that exact version, so publish it "
        "with `blueprint_upsert(publish: true)` before authoring against it - the coverage "
        "list below is still the right one to close.\n\n"
    )


def _no_such_blueprint(agent_id: str, version: str, *, published: bool) -> str:
    """What to say when the thing the prompt is about does not exist.

    A prompt has no error envelope to return - ``prompts/get`` answers with
    messages - so the honest answer is text that names the miss and the next
    call, rather than a template interpolating an id nothing resolves.

    ``published`` picks one word, and it has to be picked rather than fixed. The
    two callers resolve differently on purpose: ``fill-a-dataset`` goes through
    ``examples.published_blueprint``, so its miss really is "no *published*
    version"; ``cover-the-label-space`` mirrors ``label_vocabulary``'s raw
    lookup, so a draft is a hit there and claiming "published" would contradict
    the branch beside it. One sentence saying two different true things was the
    defect this parameter removes.
    """
    at = f" at version `{version}`" if version else " (no version given, so the latest published)"
    qualifier = "published " if published else ""
    return (
        f"There is no {qualifier}agent-props blueprint for `{agent_id}`{at} in this store, so "
        "there is nothing to author datasets against yet.\n\n"
        "Call `agent_list` to see which agents and versions this store holds, or run the "
        "`author-a-blueprint` prompt to add one. `resources/list` shows the same thing as "
        "documents you can read."
    )
