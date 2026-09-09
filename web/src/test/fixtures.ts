/**
 * The committed golden fixtures, read from `tests/fixtures/`.
 *
 * Read from disk rather than copied into this directory, deliberately. Ruling
 * R-44 added a drift guard pinning `tests/fixtures/` byte-exact to
 * `docs/worked-example.md`, and a second copy inside `web/` would be a third
 * thing to keep in step with no guard on it at all. Vitest runs in Node, so the
 * files are just there.
 *
 * The whole point of using them is that clause 2 of the M9 gate is an assertion
 * about **real data**: "a reviewer can find the Priya dataset by searching
 * 'repeat operator' and by filtering `author=pnair`". Both of those are facts
 * about `priya-missing-docs.json`, verified here rather than assumed:
 * `test_web_review_surface.py` checks the same two strings on the Python side.
 */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import type { BlueprintView, DatasetSummary, DatasetView, LabelVocabulary } from "@/mcp/types";

/**
 * Resolved from the Vitest root (`web/`) rather than from `import.meta.url`,
 * which the jsdom transform rewrites — the first attempt produced a path
 * containing the literal string `undefined`, which is a good reminder that a
 * fixture loader silently reading the wrong file is worse than one that throws.
 */
const FIXTURES = resolve(process.cwd(), "..", "tests", "fixtures");

function load<T>(relative: string): T {
  return JSON.parse(readFileSync(resolve(FIXTURES, relative), "utf8")) as T;
}

export const GOLDEN_BLUEPRINT = load<BlueprintView>(
  "blueprints/location-onboarding-1.0.0.json",
);
export const PRIYA = load<DatasetView>("datasets/priya-missing-docs.json");
export const ARUN = load<DatasetView>("datasets/arun-escalated.json");

/** `DatasetSummary` as `dataset_find` builds it, from a full dataset document. */
export function summaryOf(dataset: DatasetView): DatasetSummary {
  return {
    id: dataset.id,
    version: dataset.version,
    title: dataset.provenance.title,
    intent: dataset.provenance.intent,
    labels: dataset.labels,
    author: dataset.provenance.author,
    blueprint: dataset.blueprint,
    narrative_excerpt: dataset.narrative.slice(0, 200),
    archived: dataset.archived,
    created_at: dataset.provenance.created_at,
  };
}

export const PRIYA_SUMMARY = summaryOf(PRIYA);
export const ARUN_SUMMARY = summaryOf(ARUN);

export const VOCABULARY: LabelVocabulary = {
  agent_id: GOLDEN_BLUEPRINT.agent_id,
  version: GOLDEN_BLUEPRINT.version,
  label_schema: (GOLDEN_BLUEPRINT as unknown as { label_schema: LabelVocabulary["label_schema"] })
    .label_schema,
  counts: {},
  dataset_count: 2,
};
