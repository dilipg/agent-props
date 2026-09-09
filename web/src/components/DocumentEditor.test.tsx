/**
 * M9's first acceptance clause: a deliberately broken edit shows inline errors
 * **before** save.
 *
 * Two breaks, one per half of `src/lib/validation.ts`, because the clause is
 * only satisfied if both halves surface:
 *
 * 1. A **shape** break — a required field removed — must show a finding with no
 *    tool call at all. Ajv, locally, per keystroke.
 * 2. A **policy** break — BP-005, a node unreachable from `entry_node` — must
 *    show a finding carrying the catalogue's rule id, obtained from
 *    `blueprint_validate`, which stores nothing.
 *
 * And in both cases: **no write tool is called**. That is the "before save"
 * half of the clause, and it is asserted as an absence at the wire — the
 * recorder's tool list must contain neither `blueprint_upsert` nor
 * `dataset_import`. An assertion that the button is disabled would be weaker:
 * a disabled button is a claim about the DOM, and the clause is a claim about
 * what reached the service.
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

import { installServer, failed, ok, rule } from "@/test/server";
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

describe("clause 1: a broken edit shows inline errors before save", () => {
  it("shows a shape finding for a missing required field, with no tool call", async () => {
    server = installServer({
      blueprint_validate: () => ok("report", { ok: true, errors: [] }),
    });
    const user = userEvent.setup();
    renderWithQuery(
      <DocumentEditor
        kind="blueprint"
        loaded={GOLDEN_BLUEPRINT}
        onSave={vi.fn()}
        saveLabel="Save blueprint"
        saveState="idle"
        saveError={null}
        savedMessage={null}
      />,
    );

    const broken = { ...GOLDEN_BLUEPRINT } as Record<string, unknown>;
    delete broken["entry_node"];
    await retype(user, broken);

    await waitFor(() => {
      expect(screen.getByTestId("findings")).toBeInTheDocument();
    });
    const findings = screen.getAllByTestId("finding");
    expect(findings.map((node) => node.getAttribute("data-rule"))).toContain("SHAPE");
    // Pointed at the missing property, not at the root.
    expect(findings.some((node) => node.getAttribute("data-pointer") === "/entry_node")).toBe(true);
    expect(screen.getByTestId("save")).toBeDisabled();
    noWriteHappened(server);
  });

  it("shows a policy finding — BP-005 — from the validate tool, and still no write", async () => {
    // The break Ajv cannot see: the document's shape is perfect, one edge is
    // gone, and three nodes are now unreachable from `entry_node`. Ruling R-04
    // put that in the catalogue on purpose.
    server = installServer({
      blueprint_validate: () =>
        ok("report", {
          ok: false,
          errors: [rule("BP-005", "/nodes/5"), rule("BP-005", "/nodes/6")],
        }),
    });
    const user = userEvent.setup();
    renderWithQuery(
      <DocumentEditor
        kind="blueprint"
        loaded={GOLDEN_BLUEPRINT}
        onSave={vi.fn()}
        saveLabel="Save blueprint"
        saveState="idle"
        saveError={null}
        savedMessage={null}
      />,
    );

    await retype(user, {
      ...GOLDEN_BLUEPRINT,
      edges: GOLDEN_BLUEPRINT.edges.filter((edge) => edge.to !== "assign_training"),
    });

    await waitFor(() => {
      expect(
        screen.getAllByTestId("finding").map((node) => node.getAttribute("data-rule")),
      ).toContain("BP-005");
    });
    // The shape half is silent about this document, which is the point.
    expect(
      screen.getAllByTestId("finding").map((node) => node.getAttribute("data-rule")),
    ).not.toContain("SHAPE");
    expect(server.toolNames()).toContain("blueprint_validate");
    noWriteHappened(server);
  });

  it("shows a dataset policy finding — DS-026 — the same way", async () => {
    server = installServer({
      dataset_validate: () => ok("report", { ok: false, errors: [rule("DS-026", "/provenance/intent", "provenance")] }),
    });
    const user = userEvent.setup();
    renderWithQuery(
      <DocumentEditor
        kind="dataset"
        loaded={PRIYA}
        onSave={vi.fn()}
        saveLabel="Save as new version"
        saveState="idle"
        saveError={null}
        savedMessage={null}
      />,
    );

    await retype(user, {
      ...PRIYA,
      provenance: { ...PRIYA.provenance, intent: "x" },
    });

    await waitFor(() => {
      expect(
        screen.getAllByTestId("finding").map((node) => node.getAttribute("data-rule")),
      ).toContain("DS-026");
    });
    const finding = screen
      .getAllByTestId("finding")
      .find((node) => node.getAttribute("data-rule") === "DS-026");
    expect(finding).toHaveAttribute("data-pointer", "/provenance/intent");
    noWriteHappened(server);
  });

  it("reports unparseable JSON as a shape finding rather than crashing", async () => {
    server = installServer({ dataset_validate: () => ok("report", { ok: true, errors: [] }) });
    const user = userEvent.setup();
    renderWithQuery(
      <DocumentEditor
        kind="dataset"
        loaded={PRIYA}
        onSave={vi.fn()}
        saveLabel="Save"
        saveState="idle"
        saveError={null}
        savedMessage={null}
      />,
    );
    await user.clear(screen.getByRole("textbox"));
    await user.paste("{ this is not json");

    await waitFor(() => {
      expect(
        screen.getAllByTestId("finding").map((node) => node.getAttribute("data-rule")),
      ).toContain("SHAPE");
    });
    expect(screen.getByTestId("save")).toBeDisabled();
    noWriteHappened(server);
  });

  it("enables save and calls it once for a clean document — so the guard is not vacuous", async () => {
    // Without this, every assertion above would pass on an editor whose save
    // button is permanently disabled.
    server = installServer({ blueprint_validate: () => ok("report", { ok: true, errors: [] }) });
    const onSave = vi.fn();
    const user = userEvent.setup();
    renderWithQuery(
      <DocumentEditor
        kind="blueprint"
        loaded={GOLDEN_BLUEPRINT}
        onSave={onSave}
        saveLabel="Save blueprint"
        saveState="idle"
        saveError={null}
        savedMessage={null}
      />,
    );

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
    server = installServer({ dataset_validate: () => ok("report", { ok: true, errors: [] }) });
    const { ToolError } = await import("@/mcp/envelope");
    renderWithQuery(
      <DocumentEditor
        kind="dataset"
        loaded={PRIYA}
        onSave={vi.fn()}
        saveLabel="Save"
        saveState="error"
        saveError={new ToolError("dataset_import", [rule("DS-024", "/datasets/0/labels")])}
        savedMessage={null}
      />,
    );
    await waitFor(() => {
      expect(
        screen.getAllByTestId("finding").map((node) => node.getAttribute("data-rule")),
      ).toContain("DS-024");
    });
  });
});

describe("the harness's own honesty", () => {
  it("would have caught a validate tool that failed outright, not just one that found errors", async () => {
    // An error *envelope* from the validate tool — AP-001, say — must also
    // reach the finding list. Answering `ok: false` is how the boundary reports
    // a malformed argument, and swallowing it would hide a real problem.
    server = installServer({ dataset_validate: () => failed([rule("AP-001", "/dataset")]) });
    const user = userEvent.setup();
    renderWithQuery(
      <DocumentEditor
        kind="dataset"
        loaded={PRIYA}
        onSave={vi.fn()}
        saveLabel="Save"
        saveState="idle"
        saveError={null}
        savedMessage={null}
      />,
    );
    await retype(user, { ...PRIYA, narrative: "changed" });
    await waitFor(() => {
      expect(
        screen.getAllByTestId("finding").map((node) => node.getAttribute("data-rule")),
      ).toContain("AP-001");
    });
  });
});
