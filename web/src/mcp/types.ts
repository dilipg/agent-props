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
