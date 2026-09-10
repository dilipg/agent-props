/**
 * The payload shapes this app reads, from `docs/contracts.md` section 2.
 *
 * Hand-written rather than generated: ruling R-68 descoped the TypeScript
 * client, so there is no generated client to take these from, and a generator
 * for two documents would be more machinery than the documents. They are
 * `readonly` throughout because everything here comes off the wire and the app
 * never edits a summary in place — an edit goes through the editor, which works
 * on `unknown` JSON and hands it back to a tool.
 *
 * `Blueprint` and `Dataset` are deliberately **not** typed field-by-field. The
 * emitted JSON Schemas in `schemas/` are the authority on their shape (M1
 * generates them from the Pydantic models), the editor validates against those
 * at runtime, and a second hand-maintained description of the same shape in
 * TypeScript would be a thing to drift. What the app needs statically is the
 * handful of fields it renders, and those are declared as narrow views.
 */

/** An arbitrary JSON document, as the editor and the tools exchange it. */
export type JsonDocument = Readonly<Record<string, unknown>>;

/** `DatasetSummary` — one row of `dataset_find`. Contracts section 2.2.1. */
export interface DatasetSummary {
  readonly id: string;
  readonly version: number;
  readonly title: string;
  readonly intent: string;
  readonly labels: Readonly<Record<string, string>>;
  readonly author: Author;
  readonly blueprint: BlueprintRef;
  /** The first 200 characters of the narrative. Never the whole thing. */
  readonly narrative_excerpt: string;
  readonly archived: boolean;
  readonly created_at: string;
}

export interface Author {
  readonly name: string;
  readonly handle: string;
  readonly agent: string;
}

export interface BlueprintRef {
  readonly agent_id: string;
  readonly version: string;
}

/** One row of `blueprint_list`. */
export interface BlueprintListRow {
  readonly agent_id: string;
  readonly version: string;
  readonly status: "draft" | "published";
  readonly description: string;
}

/** One row of `agent_list`. */
export interface AgentRow {
  readonly agent_id: string;
  readonly versions: readonly string[];
  readonly dataset_count: number;
}

/** `label_vocabulary`'s payload: the declared dimensions plus per-value counts. */
export interface LabelVocabulary {
  readonly agent_id: string;
  readonly version: string;
  readonly label_schema: { readonly dimensions: Readonly<Record<string, readonly string[]>> };
  readonly counts: Readonly<Record<string, Readonly<Record<string, number>>>>;
  readonly dataset_count: number;
}

/** `store_status`'s payload. */
export interface StoreStatus {
  readonly backend: string;
  readonly healthy: boolean;
  readonly counts: { readonly blueprints: number; readonly datasets: number; readonly runs: number };
}

/** The blueprint fields the graph and the header read. The rest stays JSON. */
export interface BlueprintView extends JsonDocument {
  readonly agent_id: string;
  readonly version: string;
  readonly description: string;
  readonly status: "draft" | "published";
  readonly entry_node: string;
  readonly nodes: readonly BlueprintNode[];
  readonly edges: readonly BlueprintEdge[];
}

export interface BlueprintNode {
  readonly id: string;
  readonly kind: "tool_call" | "llm" | "decision" | "loop" | "terminal";
  readonly tool_name?: string | null;
  readonly pool?: boolean;
  readonly max_iterations?: number | null;
  readonly notes?: string;
}

export interface BlueprintEdge {
  readonly from: string;
  readonly to: string;
  readonly condition?: JsonDocument | null;
}

/** The dataset fields the detail view reads. The rest stays JSON. */
export interface DatasetView extends JsonDocument {
  readonly id: string;
  readonly version: number;
  readonly archived: boolean;
  readonly blueprint: BlueprintRef;
  readonly narrative: string;
  readonly labels: Readonly<Record<string, string>>;
  readonly provenance: {
    readonly title: string;
    readonly intent: string;
    readonly author: Author;
    readonly created_at: string;
  };
}

/** The filter a reviewer builds. Maps onto `dataset_find`'s parameters. */
export interface DatasetFilter {
  readonly agent_id?: string;
  /** Free text over `title` and `intent`. Substring, case-folded (R-36). */
  readonly q?: string;
  /** `provenance.author.handle`. Attribution, never authentication. */
  readonly author?: string;
  readonly labels?: Readonly<Record<string, string>>;
  readonly limit?: number;
  readonly offset?: number;
}

// ------------------------------------------------------------------ runs

/**
 * One row of `run_find`. What the run picker needs and nothing more.
 *
 * There is no step count here, deliberately: `run_find` does not return one and
 * deriving it would mean a `run_get` per row — the same per-row detail fetch
 * M9's third acceptance clause rules out for the dataset list.
 */
export interface RunSummary {
  readonly id: string;
  readonly agent_id: string;
  /** `running` or `finished` today. Widened to `string` because the service
   * owns this vocabulary and a client that narrowed it would break on the
   * first status added. `RunList` styles the known ones and falls back. */
  readonly status: string;
  readonly dataset_id: string;
  readonly dataset_ver: number;
  readonly bp_version: string;
  readonly run_class: string;
  readonly model: string | null;
  readonly started_at: string;
  readonly finished_at: string | null;
  readonly external_refs: Readonly<Record<string, string>>;
  readonly warnings: readonly string[];
}

/**
 * One node's whole story, out of `run_evidence`'s `nodes` list.
 *
 * The three payloads answer three different questions and the run view keeps
 * them apart on purpose:
 *
 * - `served` — the fixture agent-props handed over. The *fake data used*.
 * - `actual` — what the agent reported through `record_step`. The *output*.
 * - `expected` — what the dataset author said this step should produce.
 *
 * `served` is not `expected`. A `tool_call` node's fixture is the tool's reply
 * going *in* to the agent; `expected` is the author's claim about what comes
 * *out*. Collapsing them would make the view lie about the direction of data.
 */
export interface EvidenceNode {
  readonly node_id: string;
  readonly iteration: number;
  readonly seq: number;
  /** The blueprint node kind. Displayed, never switched on, so `string`. */
  readonly kind: string;
  readonly tool_name: string | null;
  /** False when the agent fetched the step and never reported back. */
  readonly recorded: boolean;
  readonly served: JsonDocument | null;
  readonly actual: JsonDocument | null;
  readonly expected: JsonDocument | null;
  readonly node_expectation: JsonDocument | null;
  /** The fault the dataset author injected at this step, if any. */
  readonly fault: JsonDocument | null;
  readonly fetched_at: string | null;
  readonly recorded_at: string | null;
}

/**
 * The `run_evidence` bundle.
 *
 * Everything a grader needs in one call, and no verdict — the tool's own
 * contract. `comparison` is the mode the dataset declared, which is why the run
 * view shows it rather than computing a pass: under `subset`, an `actual` that
 * differs textually from `expected` can still be correct, so a "differs" badge
 * would be actively misleading.
 */
export interface EvidenceBundle {
  readonly run: {
    readonly id: string;
    readonly agent_id: string;
    readonly status: string;
    readonly run_class: string;
    readonly model: string | null;
    readonly started_at: string;
    readonly finished_at: string | null;
    readonly declared_blueprint_version: string | null;
    readonly external_refs: Readonly<Record<string, string>>;
  };
  readonly pin: {
    readonly dataset_id: string;
    readonly dataset_version: number;
    readonly blueprint_version: string;
  };
  readonly dataset: JsonDocument & {
    readonly id: string;
    readonly intent?: string;
    readonly archived?: boolean;
  };
  readonly comparison: string;
  readonly expected: JsonDocument & {
    readonly final?: JsonDocument;
    readonly rationale?: string;
    readonly expected_path?: readonly string[];
  };
  readonly actual: JsonDocument | null;
  readonly outcome_schema: JsonDocument | null;
  readonly nodes: readonly EvidenceNode[];
  readonly path: {
    readonly expected: readonly string[];
    readonly actual: readonly string[];
    readonly traversed: readonly string[];
  };
  readonly warnings: readonly string[];
}
