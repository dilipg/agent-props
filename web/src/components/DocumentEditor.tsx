/**
 * The edit surface: an editor, the two halves of validation, and one save.
 *
 * M9's first acceptance clause is "a deliberately broken edit shows inline
 * errors **before** save", and the two halves of `src/lib/validation.ts` are
 * how. Shape findings appear as the reviewer types, computed locally against
 * the emitted JSON Schema. Policy findings arrive from `blueprint_validate` or
 * `dataset_validate` — tools that store nothing — debounced so a keystroke is
 * not a request.
 *
 * What blocks save, and what only renders
 * ---------------------------------------
 *
 * Three rules, each fixed in the R-72 fix round after the review found the
 * first two wrong:
 *
 * 1. **Only `error`-severity findings block.** Ground rule 3: warnings never
 *    block anything, and a warned document is storable — DS-027 warns and the
 *    service will happily accept it. Rendering a warning and refusing to save
 *    would be this app inventing a gate the service does not have.
 * 2. **A rejected save does *not* block.** It renders, and the button stays
 *    live. TanStack Query clears `mutation.error` only on the next `mutate()`,
 *    so folding the rejection into the disabled condition made *the button that
 *    would clear the error the button the error had disabled* — a deadlock only
 *    a screen change or a reload escaped. Editing also clears it now, through
 *    `onDirty`.
 * 3. **A pending catalogue check blocks.** Without this a reviewer could press
 *    save inside the 450 ms debounce window, before the policy half had
 *    answered, and get a rejection the app already had the means to predict.
 *
 * Disabling save is a courtesy either way, and the distinction is worth
 * stating: the *service* is what refuses a broken document, at write time,
 * through the full catalogue (ground rule 7). A disabled button only declines
 * to waste the reviewer's round trip. When a rejection does arrive it renders
 * in the same inline list, so a reviewer never has to learn a second place to
 * look.
 *
 * Nothing here writes on its own. There is no autosave, no draft persistence
 * and no optimistic update: a save happens when a human presses save, and the
 * next read comes from the store.
 */

import { useEffect, useMemo, useRef, useState } from "react";

import { Findings, Warnings } from "./Findings";
import { JsonEditorPane } from "./JsonEditor";
import type { RuleError, ToolWarning } from "@/mcp/envelope";
import { blocking, findings as findingsOf } from "@/mcp/envelope";
import type { DocumentKind } from "@/lib/validation";
import { isDocument, mergeFindings, shapeFindings } from "@/lib/validation";
import { blueprintValidate, datasetValidate } from "@/mcp/tools";
import type { JsonDocument } from "@/mcp/types";

/** How long a reviewer has to stop typing before the policy half runs. */
const POLICY_DEBOUNCE_MS = 450;

const REMOTE: Readonly<
  Record<DocumentKind, (document: JsonDocument) => Promise<readonly RuleError[]>>
> = {
  blueprint: blueprintValidate,
  dataset: datasetValidate,
};

export function DocumentEditor({
  kind,
  loaded,
  onSave,
  onDirty,
  saveLabel,
  saveState,
  saveError,
  savedMessage,
  saveWarnings,
}: {
  readonly kind: DocumentKind;
  /** The stored document, as read. `undefined` while it loads. */
  readonly loaded: JsonDocument | undefined;
  readonly onSave: (document: JsonDocument) => void;
  /**
   * Called on the first edit after a save attempt, so the screen can
   * `reset()` the mutation. Without it a rejection outlives the document it
   * was about, and the reviewer reads findings against text they have changed.
   */
  readonly onDirty?: () => void;
  readonly saveLabel: string;
  readonly saveState: "idle" | "pending" | "error" | "success";
  readonly saveError: unknown;
  readonly savedMessage: string | null;
  /** Warnings the write returned. Ground rule 3's only channel. */
  readonly saveWarnings?: readonly ToolWarning[];
}): React.JSX.Element {
  const [draft, setDraft] = useState<unknown>(undefined);
  const [parseError, setParseError] = useState<string | null>(null);
  const [policy, setPolicy] = useState<readonly RuleError[]>([]);
  const [policyPending, setPolicyPending] = useState(false);

  const current = draft === undefined ? loaded : draft;

  /**
   * A successful save clears the draft, so `current` falls back to the
   * refetched document.
   *
   * Without this the editor showed the stored document while validating and
   * re-saving the **stale draft** until the reviewer typed again — the two
   * panes agreed on screen and disagreed in memory.
   */
  useEffect(() => {
    if (saveState === "success") {
      setDraft(undefined);
      setParseError(null);
    }
  }, [saveState]);

  /** So `onDirty` fires once per save attempt rather than per keystroke. */
  const dirty = useRef(false);
  useEffect(() => {
    if (saveState === "pending" || saveState === "idle") {
      dirty.current = false;
    }
  }, [saveState]);

  /** The local half: synchronous, every render, no network. */
  const shape = useMemo<readonly RuleError[]>(() => {
    if (parseError !== null) {
      return [
        {
          rule: "SHAPE",
          severity: "error",
          pointer: "",
          message: `not valid JSON: ${parseError}`,
          section: null,
        },
      ];
    }
    return current === undefined ? [] : shapeFindings(kind, current);
  }, [kind, current, parseError]);

  /**
   * The remote half: the whole rule catalogue, debounced, and it stores nothing.
   *
   * Skipped while the document is not even an object — there is no point asking
   * the catalogue about something the shape half has already rejected as not a
   * document, and the tool would answer `AP-001` about the argument rather than
   * anything useful about the content.
   *
   * A thrown error becomes the finding list via `findingsOf`, which is right
   * for a `ToolError` and empty for anything else. That emptiness is what hid
   * ruling R-72's bug for a whole milestone, so the transport now throws a
   * *named* error for an unexpected envelope shape and this branch surfaces it
   * as a finding rather than as silence.
   */
  useEffect(() => {
    if (current === undefined || parseError !== null || !isDocument(current)) {
      setPolicy([]);
      setPolicyPending(false);
      return undefined;
    }
    let live = true;
    setPolicyPending(true);
    const timer = setTimeout(() => {
      REMOTE[kind](current)
        .then((result) => {
          if (live) setPolicy(result);
        })
        .catch((error: unknown) => {
          if (!live) return;
          const reported = findingsOf(error);
          setPolicy(
            reported.length > 0
              ? reported
              : [
                  {
                    rule: "AP-000",
                    severity: "error",
                    pointer: "",
                    message: `the catalogue could not be reached: ${
                      error instanceof Error ? error.message : String(error)
                    }`,
                    section: null,
                  },
                ],
          );
        })
        .finally(() => {
          if (live) setPolicyPending(false);
        });
    }, POLICY_DEBOUNCE_MS);
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [kind, current, parseError]);

  const rejection = findingsOf(saveError);
  const all = mergeFindings(shape, mergeFindings(policy, rejection));
  /**
   * Only the *predictable* errors block, and only while they are current.
   *
   * `rejection` is deliberately absent: see rule 2 in the module docstring.
   * `blocking()` drops warning severities: rule 1.
   */
  const errors = blocking(mergeFindings(shape, policy));
  const blocked =
    errors.length > 0 || policyPending || current === undefined || !isDocument(current);
  const warnings = all.filter((finding) => finding.severity === "warning");

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="min-h-0 flex-1 overflow-hidden border-b border-slate-200">
        <JsonEditorPane
          kind={kind}
          document={loaded}
          onChange={(value, error) => {
            setDraft(value);
            setParseError(error);
            if (!dirty.current && (saveState === "error" || saveState === "success")) {
              dirty.current = true;
              onDirty?.();
            }
          }}
        />
      </div>

      <div className="flex shrink-0 items-center gap-3 border-b border-slate-200 bg-slate-50 px-3 py-2">
        <button
          type="button"
          data-testid="save"
          disabled={blocked || saveState === "pending"}
          onClick={() => {
            if (isDocument(current)) onSave(current);
          }}
          className="rounded bg-sky-600 px-3 py-1.5 text-xs font-medium text-white disabled:cursor-not-allowed disabled:bg-slate-300"
        >
          {saveState === "pending" ? "Saving…" : saveLabel}
        </button>
        <span data-testid="validation-state" className="text-[11px] text-slate-500">
          {describe({
            errors: errors.length,
            warnings: warnings.length,
            rejected: rejection.length,
            pending: policyPending,
          })}
        </span>
        {savedMessage !== null && saveState === "success" && (
          <span data-testid="saved" className="text-[11px] text-emerald-700">
            {savedMessage}
          </span>
        )}
      </div>

      <div className="max-h-56 shrink-0 overflow-y-auto bg-white">
        <Findings findings={all} emptyLabel="No shape or catalogue findings." />
        {saveState === "success" && <Warnings warnings={saveWarnings ?? []} />}
        {saveError !== null && saveError !== undefined && rejection.length === 0 && (
          <p data-testid="save-error" className="px-3 py-2 text-xs text-red-700">
            {saveError instanceof Error ? saveError.message : JSON.stringify(saveError)}
          </p>
        )}
      </div>
    </div>
  );
}

/**
 * The status line, as a function of four counts.
 *
 * Its own function because it has five outcomes and the previous nested
 * ternary had three — one of which was the "shape and catalogue clean" that
 * ruling R-72's bug displayed over a document the catalogue had warned about.
 * A warned document must not read as clean, and a blocked one must say which
 * kind of finding is blocking it.
 */
function describe(counts: {
  errors: number;
  warnings: number;
  rejected: number;
  pending: boolean;
}): string {
  const plural = (n: number, word: string) => `${String(n)} ${word}${n === 1 ? "" : "s"}`;
  if (counts.errors > 0) {
    return `${plural(counts.errors, "error")} — fix before saving`;
  }
  if (counts.rejected > 0) {
    return `the service rejected this: ${plural(counts.rejected, "finding")}`;
  }
  if (counts.pending) {
    return "checking the rule catalogue…";
  }
  if (counts.warnings > 0) {
    return `${plural(counts.warnings, "warning")} — savable, but read them`;
  }
  return "shape and catalogue clean";
}
