/**
 * The tool surface, as the only interface this app has to the service.
 *
 * Every function here is one `callTool` with a **literal** tool name, and this
 * is the only module that calls it. Together with `transport.ts` being the only
 * module that touches the network, that makes M9's fifth acceptance clause —
 * "no mutation happens outside the tool surface" — a property a test can check
 * by reading the source: `tests/unit/test_web_writes_through_tools.py`
 * enumerates the server's registered tools, derives which of them write by
 * walking the call graph to the `Store` Protocol's mutating methods, and
 * asserts that the only writing tool names in `web/src` are the two below.
 *
 * The two writes, and why they are these two
 * ------------------------------------------
 *
 * `blueprint_upsert` is the blueprint write; there is no other.
 *
 * `dataset_import` is the **dataset edit**, and that needs saying because it is
 * not the obvious choice. The tool surface has no `dataset_upsert`: the writes
 * are `dataset_submit` (from a skeleton), `dataset_import` (from a bundle),
 * `dataset_expand`, and the two archive flags. Ruling R-17 says the web app
 * writes "producing a new dataset version by copy-on-write", and only
 * `dataset_import` does that — `put_dataset` allocates `max(version) + 1` for
 * the lineage keyed on the document's own `id`, so a bundle carrying an edited
 * dataset that keeps its id becomes version *n+1* of that lineage and leaves
 * every earlier version intact. `dataset_submit` would mint a **new lineage**
 * with a new id, which is a different dataset rather than an edit of this one.
 *
 * `blueprints: []` in the bundle is correct and deliberate: import validates
 * against a resolver that answers from the receiving store *plus the bundle*,
 * so a dataset whose blueprint is already published here satisfies DS-001
 * without the bundle re-carrying it. Sending it would re-publish an immutable
 * version for no reason.
 *
 * Neither `dataset_archive` nor `dataset_restore` is here. They are writes M9's
 * brief does not ask for, and the smallest write surface that satisfies the
 * milestone is the one worth having.
 */

import type { Envelope, RuleError } from "./envelope";
import { payload } from "./envelope";
import { callTool } from "./transport";
import type {
  AgentRow,
  BlueprintListRow,
  BlueprintView,
  DatasetFilter,
  DatasetSummary,
  DatasetView,
  JsonDocument,
  LabelVocabulary,
  StoreStatus,
} from "./types";

/** The bundle format `dataset_import` accepts. Contracts section 4. */
const BUNDLE_FORMAT = "agentprops.bundle";
const BUNDLE_FORMAT_VERSION = 1;

// ---------------------------------------------------------------------- reads

export async function agentList(): Promise<readonly AgentRow[]> {
  return payload("agent_list", await callTool("agent_list", {}));
}

export async function storeStatus(): Promise<StoreStatus> {
  return payload("store_status", await callTool("store_status", {}));
}

export async function blueprintList(): Promise<readonly BlueprintListRow[]> {
  return payload("blueprint_list", await callTool("blueprint_list", {}));
}

export async function blueprintGet(agentId: string, version?: string): Promise<BlueprintView> {
  const args = version === undefined ? { agent_id: agentId } : { agent_id: agentId, version };
  return payload("blueprint_get", await callTool("blueprint_get", args));
}

export async function labelVocabulary(agentId: string): Promise<LabelVocabulary> {
  return payload("label_vocabulary", await callTool("label_vocabulary", { agent_id: agentId }));
}

/**
 * One fetch, one full review surface.
 *
 * `DatasetSummary` carries title, intent, the complete label set, the author
 * and a narrative excerpt (contracts 2.2.1), so the list needs no per-row
 * detail call to be judgeable — which is M9's third acceptance clause. One row
 * per lineage at its latest version (ruling R-38), ordered `(created_at, id)`,
 * archived excluded.
 */
export async function datasetFind(filter: DatasetFilter): Promise<readonly DatasetSummary[]> {
  const args: Record<string, unknown> = {};
  if (filter.agent_id !== undefined && filter.agent_id !== "") args["agent_id"] = filter.agent_id;
  if (filter.q !== undefined && filter.q !== "") args["q"] = filter.q;
  if (filter.author !== undefined && filter.author !== "") args["author"] = filter.author;
  if (filter.labels !== undefined && Object.keys(filter.labels).length > 0) {
    args["labels"] = filter.labels;
  }
  if (filter.limit !== undefined) args["limit"] = filter.limit;
  if (filter.offset !== undefined) args["offset"] = filter.offset;
  return payload("dataset_find", await callTool("dataset_find", args));
}

export async function datasetGet(datasetId: string, version?: number): Promise<DatasetView> {
  const args =
    version === undefined ? { dataset_id: datasetId } : { dataset_id: datasetId, version };
  return payload("dataset_get", await callTool("dataset_get", args));
}

/**
 * The remote half of validation: the whole rule catalogue, and no write.
 *
 * Ajv against the emitted JSON Schema catches **shape** and nothing else —
 * ruling R-04 put policy in the catalogue on purpose. BP-005 (reachability),
 * BP-018 (a cycle not through a loop node) and the rest need the graph, so they
 * come from here. `blueprint_validate` does not store, which is what lets this
 * run *before* save rather than as part of it.
 */
export async function blueprintValidate(document: JsonDocument): Promise<readonly RuleError[]> {
  return errorsOf(await callTool("blueprint_validate", { blueprint: document }));
}

/**
 * The dataset half of the same: DS-008 entity drift, DS-010 revision ordering,
 * DS-024/025/026 provenance, DS-001 blueprint existence. Stores nothing.
 */
export async function datasetValidate(document: JsonDocument): Promise<readonly RuleError[]> {
  return errorsOf(await callTool("dataset_validate", { dataset: document }));
}

/**
 * `{ok, errors}` from a validate tool, as a finding list.
 *
 * The validate tools answer with the errors as their payload rather than by
 * failing, so a clean document is an empty list and neither case is an
 * exception. A genuinely *broken* call — an unparseable argument, `AP-001` —
 * still arrives as an error envelope, and its findings are the answer too:
 * a reviewer wants to see `AP-001` inline exactly as they want to see DS-026.
 */
function errorsOf(envelope: Envelope): readonly RuleError[] {
  if (!envelope.ok) {
    return envelope.errors;
  }
  const report = payload<{ ok: boolean; errors?: readonly RuleError[] }>("validate", envelope);
  return report.errors ?? [];
}

// --------------------------------------------------------------------- writes

/**
 * Save a blueprint. `publish: false`, always.
 *
 * A published version is immutable (BP-016), so an editor that published on
 * every save would make the second save an error. Publishing is a deliberate
 * act on a finished blueprint and phase 1 has a tool for it; it is not what
 * pressing save in a JSON editor should mean.
 */
export async function blueprintSave(document: JsonDocument): Promise<BlueprintView> {
  return payload(
    "blueprint_upsert",
    await callTool("blueprint_upsert", { blueprint: document, publish: false }),
  );
}

/** What `dataset_import` reports: the ids and versions it wrote. */
export interface ImportedDatasets {
  readonly blueprints: readonly { readonly agent_id: string; readonly version: string }[];
  readonly datasets: readonly { readonly id: string; readonly version: number }[];
}

/**
 * Save an edited dataset as a new version of its own lineage.
 *
 * Copy-on-write, through the tool surface, re-validated in full on arrival —
 * ruling R-17 and ground rule 7. The document keeps its `id`; the receiving
 * store allocates the version and ignores whatever the document claims.
 */
export async function datasetSave(
  agentId: string,
  document: JsonDocument,
): Promise<ImportedDatasets> {
  const bundle = {
    format: BUNDLE_FORMAT,
    format_version: BUNDLE_FORMAT_VERSION,
    agent_id: agentId,
    blueprints: [],
    datasets: [document],
  };
  return payload("dataset_import", await callTool("dataset_import", { bundle }));
}
