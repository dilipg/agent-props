/**
 * TanStack Query hooks over the tool surface.
 *
 * Every hook reads; the two mutations are here too, and both call a function
 * from `src/mcp/tools.ts` rather than the transport. `retry: false` throughout:
 * a tool answering `ok: false` is a *structured* answer, not a transient
 * failure, and retrying it three times would show the reviewer their rule
 * errors a second later than necessary. The service never gates, so an error
 * envelope means the document is wrong, and a document does not become right by
 * being asked again.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { UseMutationResult, UseQueryResult } from "@tanstack/react-query";

import type { JsonDocument } from "@/mcp/types";
import type {
  AgentRow,
  BlueprintListRow,
  BlueprintView,
  DatasetFilter,
  DatasetSummary,
  DatasetView,
  LabelVocabulary,
  StoreStatus,
} from "@/mcp/types";
import {
  agentList,
  blueprintGet,
  blueprintList,
  blueprintSave,
  datasetFind,
  datasetGet,
  datasetSave,
  labelVocabulary,
  storeStatus,
} from "@/mcp/tools";
import type { ImportedDatasets, WithWarnings } from "@/mcp/tools";

const NO_RETRY = { retry: false } as const;

export function useStoreStatus(): UseQueryResult<StoreStatus> {
  return useQuery({ queryKey: ["store_status"], queryFn: storeStatus, ...NO_RETRY });
}

export function useAgents(): UseQueryResult<readonly AgentRow[]> {
  return useQuery({ queryKey: ["agent_list"], queryFn: agentList, ...NO_RETRY });
}

export function useBlueprints(): UseQueryResult<readonly BlueprintListRow[]> {
  return useQuery({ queryKey: ["blueprint_list"], queryFn: blueprintList, ...NO_RETRY });
}

export function useBlueprint(
  agentId: string | undefined,
  version?: string,
): UseQueryResult<BlueprintView> {
  return useQuery({
    queryKey: ["blueprint_get", agentId, version ?? null],
    queryFn: () => blueprintGet(agentId as string, version),
    enabled: agentId !== undefined,
    ...NO_RETRY,
  });
}

export function useLabelVocabulary(agentId: string | undefined): UseQueryResult<LabelVocabulary> {
  return useQuery({
    queryKey: ["label_vocabulary", agentId],
    queryFn: () => labelVocabulary(agentId as string),
    enabled: agentId !== undefined,
    ...NO_RETRY,
  });
}

/**
 * The review surface's one fetch.
 *
 * The filter is the whole query key, so a label, an author handle and a text
 * query each produce their own cache entry and going back to a previous filter
 * is instant. `placeholderData` keeps the previous rows visible while a new
 * filter is in flight, which matters for a list a reviewer is scanning: rows
 * blanking on every keystroke makes the list unreadable while typing.
 */
export function useDatasets(filter: DatasetFilter): UseQueryResult<readonly DatasetSummary[]> {
  return useQuery({
    queryKey: ["dataset_find", filter],
    queryFn: () => datasetFind(filter),
    placeholderData: (previous) => previous,
    ...NO_RETRY,
  });
}

export function useDataset(
  datasetId: string | undefined,
  version?: number,
): UseQueryResult<WithWarnings<DatasetView>> {
  return useQuery({
    queryKey: ["dataset_get", datasetId, version ?? null],
    queryFn: () => datasetGet(datasetId as string, version),
    enabled: datasetId !== undefined,
    ...NO_RETRY,
  });
}

/**
 * Save a blueprint through `blueprint_upsert`.
 *
 * Invalidates the blueprint reads on success, so the graph view redraws from
 * what the store actually holds rather than from what the editor had. That is
 * the point of a round trip: the stored document is the truth.
 */
export function useSaveBlueprint(): UseMutationResult<
  WithWarnings<BlueprintView>,
  Error,
  JsonDocument
> {
  const client = useQueryClient();
  return useMutation({
    mutationFn: blueprintSave,
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["blueprint_get"] });
      void client.invalidateQueries({ queryKey: ["blueprint_list"] });
    },
  });
}

/**
 * Save an edited dataset as a new version of its lineage, through
 * `dataset_import`. See `src/mcp/tools.ts` for why that is the tool.
 */
export function useSaveDataset(
  agentId: string,
): UseMutationResult<WithWarnings<ImportedDatasets>, Error, JsonDocument> {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (document: JsonDocument) => datasetSave(agentId, document),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["dataset_find"] });
      void client.invalidateQueries({ queryKey: ["dataset_get"] });
      void client.invalidateQueries({ queryKey: ["label_vocabulary"] });
    },
  });
}
