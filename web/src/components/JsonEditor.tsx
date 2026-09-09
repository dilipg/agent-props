/**
 * `vanilla-jsoneditor` in React, with the emitted JSON Schema wired in.
 *
 * The library is framework-agnostic — `createJSONEditor` takes a DOM element —
 * so this is the whole React binding: a ref, an effect that creates the editor
 * once, and an effect that pushes new props into it. The editor owns its own
 * document state after mount; pushing `content` on every keystroke would fight
 * the editor for the cursor.
 *
 * Ajv, and the one thing it needs told
 * ------------------------------------
 *
 * The emitted schemas declare `$schema: draft/2020-12`, and Ajv's default
 * export is draft-07 — it throws on an unknown meta-schema. `onCreateAjv`
 * exists for exactly this: it returns our own `Ajv2020`, configured in
 * `src/lib/validation.ts`, so the editor's live squiggles and the finding list
 * below it come from **the same validator instance** rather than from two that
 * could disagree.
 *
 * `errorSeverity: error` rather than the default `warning`, because a document
 * that does not match its schema is not a style opinion.
 */

import { useEffect, useRef } from "react";
import {
  Mode,
  ValidationSeverity,
  createAjvValidator,
  createJSONEditor,
} from "vanilla-jsoneditor";
import type { Content, JsonEditor, JSONSchema, Validator } from "vanilla-jsoneditor";

import type { DocumentKind } from "@/lib/validation";
import { SCHEMAS, newAjv } from "@/lib/validation";

/** The validator the editor uses for its inline squiggles, one per kind. */
const validators = new Map<DocumentKind, Validator>();

function validatorFor(kind: DocumentKind): Validator {
  const existing = validators.get(kind);
  if (existing !== undefined) {
    return existing;
  }
  const created = createAjvValidator({
    schema: SCHEMAS[kind] as JSONSchema,
    // Draft 2020-12: hand the editor an Ajv that knows the dialect.
    onCreateAjv: () => newAjv(),
    errorSeverity: ValidationSeverity.error,
  });
  validators.set(kind, created);
  return created;
}

export function JsonEditorPane({
  kind,
  document,
  onChange,
}: {
  readonly kind: DocumentKind;
  /** The document to load. Changing it replaces the editor's content. */
  readonly document: unknown;
  readonly onChange: (value: unknown, parseError: string | null) => void;
}): React.JSX.Element {
  const host = useRef<HTMLDivElement | null>(null);
  const editor = useRef<JsonEditor | null>(null);
  const notify = useRef(onChange);
  notify.current = onChange;

  useEffect(() => {
    const element = host.current;
    if (element === null) {
      return undefined;
    }
    const instance = createJSONEditor({
      target: element,
      props: {
        mode: Mode.text,
        mainMenuBar: true,
        navigationBar: true,
        statusBar: true,
        validator: validatorFor(kind),
        onChange: (content: Content) => {
          if ("json" in content) {
            notify.current(content.json, null);
            return;
          }
          try {
            notify.current(JSON.parse(content.text), null);
          } catch (error) {
            notify.current(undefined, error instanceof Error ? error.message : "invalid JSON");
          }
        },
      },
    });
    editor.current = instance;
    return () => {
      void instance.destroy();
      editor.current = null;
    };
  }, [kind]);

  useEffect(() => {
    const instance = editor.current;
    if (instance === null || document === undefined) {
      return;
    }
    void instance.set({ json: document });
  }, [document]);

  return <div ref={host} data-testid={`json-editor-${kind}`} className="h-full" />;
}
