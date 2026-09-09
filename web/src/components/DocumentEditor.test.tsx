/**
 * M9's first acceptance clause: a deliberately broken edit shows inline errors
 * **before** save. Plus the three things ruling R-72's fix round added.
 *
 * Three breaks, one per half of `src/lib/validation.ts` plus the severity
 * distinction, because the clause is only satisfied if all of them surface:
 *
 * 1. A **shape** break — a required field removed — shows a finding with no
 *    tool call at all. Ajv, locally, per keystroke.
 * 2. An **error**-severity policy break — BP-005, a node unreachable from
 *    `entry_node` — shows the catalogue's rule id, from `blueprint_validate`,
 *    which stores nothing. Save is blocked.
 * 3. A **warning**-severity policy break — DS-027, intent identical to
 *    narrative — shows the rule id and **does not block save**. Ground rule 3:
 *    warnings never block, and the service will store a warned document.
 *
 * The third is the bug ruling R-72 names. It was invisible because the harness
 * could only build a success-with-`data` envelope, so every case here stubbed
 * the validators with `ok("report", {...})` — **a wrapper no validate tool
 * produces**. Six tests agreed about a shape the service never sends. They now
 * use `validated([...])`, which builds the real `{ok, errors}` reply and
 * computes `ok` from the severities so a contradictory stub cannot be written.
 *
 * And in every case: **no write tool is called**. That is the "before save"
 * half of the clause, asserted as an absence at the wire — the recorder's tool
 * list contains neither `blueprint_upsert` nor `dataset_import`. An assertion
 * that the button is disabled would be weaker: a disabled button is a claim
 * about the DOM, and the clause is a claim about what reached the service.
 *
 * The editor itself is stubbed. `vanilla-jsoneditor` mounts CodeMirror, which
 * needs layout jsdom does not provide, and driving a code editor through
 * synthetic keystrokes would test CodeMirror rather than this app. The stub is
 * a `<textarea>` that calls the same `onChange` with the same two arguments —
 * so the component under test is the real one and only the text input is fake.
 * `src/components/JsonEditor.tsx` is covered instead by the `vite build`, which
 * fails if its imports or types are wrong, and by the live-service walk in the
 * M9 report.
 */

import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { failed, installServer, rule, validated, warning, warns } from "@/test/server";
import type { Recorder } from "@/test/server";
import { GOLDEN_BLUEPRINT, PRIYA } from "@/test/fixtures";
import { renderWithQuery } from "@/test/render";

/**
 * The editor, as a textarea. Same props, same `onChange` contract: the parsed
 * value and a parse-error string, exactly what the real pane emits.
 */
vi.mock("@/components/JsonEditor", () => ({
  JsonEditorPane: ({
    kind,
    document,
    onChange,
  }: {
    kind: string;
    document: unknown;
    onChange: (value: unknown, parseError: string | null) => void;
  }) => (
    <textarea
      data-testid={`json-editor-${kind}`}
      defaultValue={JSON.stringify(document, null, 2)}
      onChange={(event) => {
        try {
          onChange(JSON.parse(event.target.value), null);
        } catch (error) {
          onChange(undefined, error instanceof Error ? error.message : "invalid JSON");
        }
      }}
    />
  ),
}));

const { DocumentEditor } = await import("./DocumentEditor");

let server: Recorder | undefined;

afterEach(() => {
  server?.restore();
  server = undefined;
});

/** Replace the whole editor text with `document`. */
async function retype(user: ReturnType<typeof userEvent.setup>, document: unknown): Promise<void> {
  const area = screen.getByRole("textbox");
  await user.clear(area);
  await user.paste(JSON.stringify(document));
}

const WRITE_TOOLS = ["blueprint_upsert", "dataset_import"];

function noWriteHappened(recorder: Recorder): void {
  for (const tool of WRITE_TOOLS) {
    expect(recorder.toolNames()).not.toContain(tool);
  }
}

function rules(): (string | null)[] {
  return screen.getAllByTestId("finding").map((node) => node.getAttribute("data-rule"));
}

/** A blueprint editor with the given props; only the ones a test varies. */
function blueprintEditor(
  props: Partial<React.ComponentProps<typeof DocumentEditor>> = {},
): React.JSX.Element {
  return (
    <DocumentEditor
      kind="blueprint"
      loaded={GOLDEN_BLUEPRINT}
      onSave={vi.fn()}
      saveLabel="Save blueprint"
      saveState="idle"
      saveError={null}
      savedMessage={null}
      {...props}
    />
  );
}

function datasetEditor(
  props: Partial<React.ComponentProps<typeof DocumentEditor>> = {},
): React.JSX.Element {
  return (
    <DocumentEditor
      kind="dataset"
      loaded={PRIYA}
      onSave={vi.fn()}
      saveLabel="Save as new version"
      saveState="idle"
      saveError={null}
      savedMessage={null}
      {...props}
    />
  );
}

describe("clause 1: a broken edit shows inline errors before save", () => {
  it("shows a shape finding for a missing required field, with no tool call", async () => {
    server = installServer({ blueprint_validate: () => validated() });
    const user = userEvent.setup();
    renderWithQuery(blueprintEditor());

    const broken = { ...GOLDEN_BLUEPRINT } as Record<string, unknown>;
    delete broken["entry_node"];
    await retype(user, broken);

    await waitFor(() => {
      expect(screen.getByTestId("findings")).toBeInTheDocument();
    });
    expect(rules()).toContain("SHAPE");
    // Pointed at the missing property, not at the root.
    expect(
      screen
        .getAllByTestId("finding")
        .some((node) => node.getAttribute("data-pointer") === "/entry_node"),
    ).toBe(true);
    expect(screen.getByTestId("save")).toBeDisabled();
    noWriteHappened(server);
  });

  it("shows a policy finding — BP-005 — from the validate tool, and still no write", async () => {
    // The break Ajv cannot see: the document's shape is perfect, one edge is
    // gone, and three nodes are now unreachable from `entry_node`. Ruling R-04
    // put that in the catalogue on purpose.
    server = installServer({
      blueprint_validate: () => validated([rule("BP-005", "/nodes/5"), rule("BP-005", "/nodes/6")]),
    });
    const user = userEvent.setup();
    renderWithQuery(blueprintEditor());

    await retype(user, {
      ...GOLDEN_BLUEPRINT,
      edges: GOLDEN_BLUEPRINT.edges.filter((edge) => edge.to !== "assign_training"),
    });

    await waitFor(() => {
      expect(rules()).toContain("BP-005");
    });
    // The shape half is silent about this document, which is the point.
    expect(rules()).not.toContain("SHAPE");
    expect(server.toolNames()).toContain("blueprint_validate");
    expect(screen.getByTestId("save")).toBeDisabled();
    noWriteHappened(server);
  });

  it("shows a dataset policy finding — DS-026 — the same way", async () => {
    server = installServer({
      dataset_validate: () => validated([rule("DS-026", "/provenance/intent", "provenance")]),
    });
    const user = userEvent.setup();
    renderWithQuery(datasetEditor());

    await retype(user, { ...PRIYA, provenance: { ...PRIYA.provenance, intent: "x" } });

    await waitFor(() => {
      expect(rules()).toContain("DS-026");
    });
    const finding = screen
      .getAllByTestId("finding")
      .find((node) => node.getAttribute("data-rule") === "DS-026");
    expect(finding).toHaveAttribute("data-pointer", "/provenance/intent");
    expect(finding).toHaveAttribute("data-severity", "error");
    noWriteHappened(server);
  });

  it("reports unparseable JSON as a shape finding rather than crashing", async () => {
    server = installServer({ dataset_validate: () => validated() });
    const user = userEvent.setup();
    renderWithQuery(datasetEditor({ saveLabel: "Save" }));

    await user.clear(screen.getByRole("textbox"));
    await user.paste("{ this is not json");

    await waitFor(() => {
      expect(rules()).toContain("SHAPE");
    });
    expect(screen.getByTestId("save")).toBeDisabled();
    noWriteHappened(server);
  });

  it("enables save and calls it once for a clean document — so the guard is not vacuous", async () => {
    // Without this, every assertion above would pass on an editor whose save
    // button is permanently disabled.
    server = installServer({ blueprint_validate: () => validated() });
    const onSave = vi.fn();
    const user = userEvent.setup();
    renderWithQuery(blueprintEditor({ onSave }));

    await retype(user, { ...GOLDEN_BLUEPRINT, description: "edited" });

    await waitFor(() => {
      expect(screen.getByTestId("save")).toBeEnabled();
    });
    await user.click(screen.getByTestId("save"));
    expect(onSave).toHaveBeenCalledTimes(1);
    expect(onSave.mock.calls[0]?.[0]).toMatchObject({ description: "edited" });
  });

  it("renders a rejected save's rule ids in the same inline list", async () => {
    // Ground rule 7: the *service* is what refuses a broken write. When it
    // does, the rejection must look like every other finding — a reviewer
    // should not have to learn a second place to look.
    server = installServer({ dataset_validate: () => validated() });
    const { ToolError } = await import("@/mcp/envelope");
    renderWithQuery(
      datasetEditor({
        saveLabel: "Save",
        saveState: "error",
        saveError: new ToolError("dataset_import", [rule("DS-024", "/datasets/0/labels")]),
      }),
    );
    await waitFor(() => {
      expect(rules()).toContain("DS-024");
    });
  });
});

describe("ruling R-72: a warning-severity finding renders and does not block", () => {
  it("shows DS-027 — the rule the detail screen exists to catch", async () => {
    // The bug this fix round is about. A warned-clean reply is `ok: true` with
    // warning items in `errors`, and the old reader lost the whole list to a
    // swallowed TypeError while the status line said "clean".
    server = installServer({
      dataset_validate: () => validated([warns("DS-027", "/provenance/intent", "provenance")]),
    });
    const user = userEvent.setup();
    renderWithQuery(datasetEditor());

    await retype(user, { ...PRIYA, provenance: { ...PRIYA.provenance, intent: PRIYA.narrative } });

    await waitFor(() => {
      expect(rules()).toContain("DS-027");
    });
    const finding = screen
      .getAllByTestId("finding")
      .find((node) => node.getAttribute("data-rule") === "DS-027");
    expect(finding).toHaveAttribute("data-severity", "warning");
    expect(finding).toHaveAttribute("data-pointer", "/provenance/intent");
  });

  it("does not say the document is clean when the catalogue warned about it", async () => {
    // The precise symptom: the finding list was empty *and* the status line
    // read "shape and catalogue clean". Both halves are asserted, because the
    // status line is what a reviewer glances at.
    server = installServer({
      dataset_validate: () => validated([warns("DS-027", "/provenance/intent", "provenance")]),
    });
    const user = userEvent.setup();
    renderWithQuery(datasetEditor());

    await retype(user, { ...PRIYA, provenance: { ...PRIYA.provenance, intent: PRIYA.narrative } });

    await waitFor(() => {
      expect(screen.getByTestId("validation-state")).toHaveTextContent("1 warning");
    });
    expect(screen.getByTestId("validation-state")).not.toHaveTextContent("clean");
  });

  it("leaves save enabled for a warning — ground rule 3, warnings never block", async () => {
    server = installServer({
      dataset_validate: () => validated([warns("DS-027", "/provenance/intent", "provenance")]),
    });
    const onSave = vi.fn();
    const user = userEvent.setup();
    renderWithQuery(datasetEditor({ onSave }));

    await retype(user, { ...PRIYA, provenance: { ...PRIYA.provenance, intent: PRIYA.narrative } });

    await waitFor(() => {
      expect(rules()).toContain("DS-027");
    });
    expect(screen.getByTestId("save")).toBeEnabled();
    await user.click(screen.getByTestId("save"));
    expect(onSave).toHaveBeenCalledTimes(1);
  });

  it("still blocks when a warning and an error arrive together", async () => {
    // The discriminating case: `blocking()` must filter by severity rather than
    // by "are there any findings", and must not be defeated by a warning
    // sitting first in the list.
    server = installServer({
      dataset_validate: () =>
        validated([warns("DS-027", "/provenance/intent"), rule("DS-026", "/provenance/intent")]),
    });
    const user = userEvent.setup();
    renderWithQuery(datasetEditor());

    await retype(user, { ...PRIYA, narrative: "changed" });

    await waitFor(() => {
      expect(rules()).toContain("DS-026");
    });
    expect(screen.getByTestId("save")).toBeDisabled();
    expect(screen.getByTestId("validation-state")).toHaveTextContent("1 error");
  });

  it("surfaces an unexpected envelope shape as a finding rather than as silence", async () => {
    // The failure mode that hid the bug: the reader threw, the throw was
    // swallowed, and the list came back empty. A `TypeError` from the boundary
    // must now be visible.
    server = installServer({ dataset_validate: () => ({ ok: true, data: {}, warnings: [] }) });
    const user = userEvent.setup();
    renderWithQuery(datasetEditor());

    await retype(user, { ...PRIYA, narrative: "changed" });

    await waitFor(() => {
      expect(rules()).toContain("AP-000");
    });
    expect(screen.getByTestId("save")).toBeDisabled();
  });
});

describe("a rejected save does not deadlock the button", () => {
  it("leaves save enabled after a rejection, so it can be retried", async () => {
    // TanStack Query clears `mutation.error` only on the next `mutate()`. When
    // the rejection blocked the button, the button that would clear the error
    // was the button the error had disabled — recoverable only by changing
    // screen or reloading.
    server = installServer({ dataset_validate: () => validated() });
    const { ToolError } = await import("@/mcp/envelope");
    const onSave = vi.fn();
    renderWithQuery(
      datasetEditor({
        onSave,
        saveLabel: "Save",
        saveState: "error",
        saveError: new ToolError("dataset_import", [rule("DS-024", "/datasets/0/labels")]),
      }),
    );

    await waitFor(() => {
      expect(rules()).toContain("DS-024");
    });
    // Waited for, not asserted immediately: `policyPending` blocks until the
    // catalogue has answered, which is the *other* fix in this round and is
    // correct. The claim is that once it has answered clean, the rejection
    // alone leaves the button live.
    await waitFor(() => {
      expect(screen.getByTestId("save")).toBeEnabled();
    });

    const user = userEvent.setup();
    await user.click(screen.getByTestId("save"));
    expect(onSave).toHaveBeenCalledTimes(1);
  });

  it("says the service rejected it, rather than counting it as a local error", async () => {
    server = installServer({ dataset_validate: () => validated() });
    const { ToolError } = await import("@/mcp/envelope");
    renderWithQuery(
      datasetEditor({
        saveLabel: "Save",
        saveState: "error",
        saveError: new ToolError("dataset_import", [rule("DS-024", "/datasets/0/labels")]),
      }),
    );
    await waitFor(() => {
      expect(screen.getByTestId("validation-state")).toHaveTextContent("the service rejected this");
    });
  });

  it("asks the screen to clear the rejection on the first edit after it", async () => {
    server = installServer({ dataset_validate: () => validated() });
    const { ToolError } = await import("@/mcp/envelope");
    const onDirty = vi.fn();
    const user = userEvent.setup();
    renderWithQuery(
      datasetEditor({
        onDirty,
        saveLabel: "Save",
        saveState: "error",
        saveError: new ToolError("dataset_import", [rule("DS-024", "/datasets/0/labels")]),
      }),
    );

    await retype(user, { ...PRIYA, narrative: "edited once" });
    expect(onDirty).toHaveBeenCalledTimes(1);

    // Once per save attempt, not once per keystroke.
    await retype(user, { ...PRIYA, narrative: "edited twice" });
    expect(onDirty).toHaveBeenCalledTimes(1);
  });
});

describe("save is blocked while the catalogue has not answered", () => {
  it("is disabled inside the debounce window and enabled after it", async () => {
    // Without this a reviewer can save inside the 450 ms window, before the
    // policy half has run — and get a rejection the app had the means to
    // predict.
    server = installServer({ blueprint_validate: () => validated() });
    const user = userEvent.setup();
    renderWithQuery(blueprintEditor());

    await retype(user, { ...GOLDEN_BLUEPRINT, description: "edited" });

    expect(screen.getByTestId("save")).toBeDisabled();
    expect(screen.getByTestId("validation-state")).toHaveTextContent("checking the rule catalogue");

    await waitFor(() => {
      expect(screen.getByTestId("save")).toBeEnabled();
    });
    expect(server.toolNames()).toContain("blueprint_validate");
  });
});

describe("a successful save clears the draft", () => {
  it("renders the refetched document rather than the stale draft", async () => {
    // The editor kept validating and re-saving the draft after a successful
    // save, so the pane showed the stored document and the component held the
    // pre-save text.
    server = installServer({ dataset_validate: () => validated() });
    const user = userEvent.setup();
    const view = renderWithQuery(datasetEditor({ saveLabel: "Save" }));

    await retype(user, { ...PRIYA, narrative: "a draft" });
    await waitFor(() => {
      expect(screen.getByTestId("save")).toBeEnabled();
    });

    // The save succeeds and the query refetches, so `loaded` is the stored
    // document again.
    view.rerender(
      <>{datasetEditor({ saveLabel: "Save", saveState: "success", savedMessage: "saved as v2" })}</>,
    );
    await waitFor(() => {
      expect(screen.getByTestId("saved")).toHaveTextContent("saved as v2");
    });
    // Nothing is left blocking, and no finding is being reported against text
    // that no longer exists.
    expect(screen.queryAllByTestId("finding")).toHaveLength(0);
  });

  it("renders the warnings a successful write returned", async () => {
    // Ground rule 3's channel, reachable at last: the `Warnings` component was
    // exported and imported by nothing until this fix round.
    server = installServer({ dataset_validate: () => validated() });
    renderWithQuery(
      datasetEditor({
        saveLabel: "Save",
        saveState: "success",
        savedMessage: "saved as v2",
        saveWarnings: [warning("dataset_archived", { dataset_id: PRIYA.id, version: 2 })],
      }),
    );
    await waitFor(() => {
      expect(screen.getByTestId("warnings")).toBeInTheDocument();
    });
    expect(screen.getByTestId("warning")).toHaveAttribute("data-warning", "dataset_archived");
  });
});

describe("the harness's own honesty", () => {
  it("would have caught a validate tool that failed outright, not just one that found errors", async () => {
    // An error *envelope* from the validate tool — AP-001, say — must also
    // reach the finding list. Answering `ok: false` is how the boundary reports
    // a malformed argument, and swallowing it would hide a real problem.
    server = installServer({ dataset_validate: () => failed([rule("AP-001", "/dataset")]) });
    const user = userEvent.setup();
    renderWithQuery(datasetEditor({ saveLabel: "Save" }));

    await retype(user, { ...PRIYA, narrative: "changed" });
    await waitFor(() => {
      expect(rules()).toContain("AP-001");
    });
  });
});
