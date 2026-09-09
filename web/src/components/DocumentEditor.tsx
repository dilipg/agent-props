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
 * Save is disabled while findings stand. That is a courtesy rather than a
 * safety mechanism, and the distinction is worth stating: the *service* is what
 * refuses a broken document, at write time, through the full catalogue (ground
 * rule 7). A disabled button is the app declining to waste the reviewer's
 * round trip. If the button were enabled and the document were broken, the
 * write would still be rejected with rule ids — which the app renders in the
 * same list, so a rejection looks the same wherever it came from.
 *
 * Nothing here writes on its own. There is no autosave, no draft persistence
 * and no optimistic update: a save happens when a human presses save, and the
 * next read comes from the store.
 */

import { useEffect, useMemo, useState } from "react";

import { Findings } from "./Findings";
import { JsonEditorPane } from "./JsonEditor";
import type { RuleError } from "@/mcp/envelope";
import { findings as findingsOf } from "@/mcp/envelope";
import type { DocumentKind } from "@/lib/validation";
import { isDocument, mergeFindings, shapeFindings } from "@/lib/validation";
import { blueprintValidate, datasetValidate } from "@/mcp/tools";
import type { JsonDocument } from "@/mcp/types";

/** How long a reviewer has to stop typing before the policy half runs. */
const POLICY_DEBOUNCE_MS = 450;

const REMOTE: Readonly<Record<DocumentKind, (document: JsonDocument) => Promise<readonly RuleError[]>>> =
  {
    blueprint: blueprintValidate,
    dataset: datasetValidate,
  };

export function DocumentEditor({
  kind,
  loaded,
  onSave,
  saveLabel,
  saveState,
  saveError,
  savedMessage,
}: {
  readonly kind: DocumentKind;
  /** The stored document, as read. `undefined` while it loads. */
  readonly loaded: JsonDocument | undefined;
  readonly onSave: (document: JsonDocument) => void;
  readonly saveLabel: string;
  readonly saveState: "idle" | "pending" | "error" | "success";
  readonly saveError: unknown;
  readonly savedMessage: string | null;
}): React.JSX.Element {
  const [draft, setDraft] = useState<unknown>(undefined);
  const [parseError, setParseError] = useState<string | null>(null);
  const [policy, setPolicy] = useState<readonly RuleError[]>([]);
  const [policyPending, setPolicyPending] = useState(false);

  const current = draft === undefined ? loaded : draft;

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
   */
  useEffect(() => {
    if (current === undefined || parseError !== null || !isDocument(current)) {
      setPolicy([]);
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
          if (live) setPolicy(findingsOf(error));
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
  const blocked = all.length > 0 || current === undefined || !isDocument(current);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="min-h-0 flex-1 overflow-hidden border-b border-slate-200">
        <JsonEditorPane
          kind={kind}
          document={loaded}
          onChange={(value, error) => {
            setDraft(value);
            setParseError(error);
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
          {all.length > 0
            ? `${String(all.length)} finding${all.length === 1 ? "" : "s"} — fix before saving`
            : policyPending
              ? "checking the rule catalogue…"
              : "shape and catalogue clean"}
        </span>
        {savedMessage !== null && saveState === "success" && (
          <span data-testid="saved" className="text-[11px] text-emerald-700">
            {savedMessage}
          </span>
        )}
      </div>

      <div className="max-h-56 shrink-0 overflow-y-auto bg-white">
        <Findings findings={all} emptyLabel="No shape or catalogue findings." />
        {saveError !== null && saveError !== undefined && rejection.length === 0 && (
          <p data-testid="save-error" className="px-3 py-2 text-xs text-red-700">
            {saveError instanceof Error ? saveError.message : JSON.stringify(saveError)}
          </p>
        )}
      </div>
    </div>
  );
}
