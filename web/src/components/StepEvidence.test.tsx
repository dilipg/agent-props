/**
 * One step's evidence: three payloads that must stay three.
 *
 * The mistake this view exists to prevent is conflating the fixture that went
 * *in* with the expectation about what comes *out*. They are different fields
 * on the bundle, they answer different questions, and a panel that showed
 * `served` under the heading "expected" would be quietly wrong in a way no
 * reviewer could detect from the screen. So the assertions are that each panel
 * carries its own field, and — the one that would catch the conflation — that
 * the fields are not equal in the fixture to begin with.
 */

import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { StepEvidence } from "./StepEvidence";
import { GOLDEN_EVIDENCE } from "@/test/fixtures";

const RECORDED = GOLDEN_EVIDENCE.nodes.find((node) => node.recorded && node.actual !== null);
const UNRECORDED = GOLDEN_EVIDENCE.nodes.find((node) => !node.recorded);

if (RECORDED === undefined || UNRECORDED === undefined) {
  throw new Error("the committed bundle no longer has both a recorded and an unrecorded step");
}

describe("one step's evidence", () => {
  it("keeps the served fixture and the expected output in separate panels", () => {
    render(<StepEvidence node={RECORDED} />);

    // The premise. Without this the test below could pass on a view that
    // rendered the same payload three times.
    expect(JSON.stringify(RECORDED.served)).not.toBe(JSON.stringify(RECORDED.expected));

    const served = screen.getByTestId("step-served");
    const expected = screen.getByTestId("step-expected");
    expect(served).toHaveTextContent("fixture served");
    expect(expected).toHaveTextContent("expected");
    expect(within(served).getByText(/"/)).toBeInTheDocument();
    expect(served.textContent).toContain(JSON.stringify(RECORDED.served, null, 2).slice(0, 40));
    expect(expected.textContent).toContain(
      JSON.stringify(RECORDED.expected, null, 2).slice(0, 40),
    );
  });

  it("says which direction each payload travelled, since the JSON cannot", () => {
    render(<StepEvidence node={RECORDED} />);
    expect(screen.getByTestId("step-served")).toHaveTextContent(
      "the fake data agent-props handed the agent",
    );
    expect(screen.getByTestId("step-actual")).toHaveTextContent(
      "what the agent reported for this step",
    );
  });

  it("renders the recorded output verbatim", () => {
    render(<StepEvidence node={RECORDED} />);
    expect(screen.getByTestId("step-actual").textContent).toContain(
      JSON.stringify(RECORDED.actual, null, 2).slice(0, 40),
    );
  });

  it("marks a step that was fetched and never recorded", () => {
    // A real state: the agent asked for the fixture and reported nothing back.
    // Rendering an empty "output" panel with no explanation would read as a
    // step that produced nothing, which is a different claim.
    render(<StepEvidence node={UNRECORDED} />);
    expect(screen.getByTestId("step-not-recorded")).toBeInTheDocument();
    expect(screen.getByTestId("step-actual")).toHaveTextContent("none");
  });

  it("computes no verdict, because the comparison mode decides what a difference means", () => {
    render(<StepEvidence node={RECORDED} />);
    const article = screen.getByTestId("step-evidence");
    expect(article.textContent).not.toMatch(/\b(pass|fail|failed|mismatch|differs)\b/i);
  });

  it("collapses so a long run stays readable", async () => {
    const user = userEvent.setup();
    render(<StepEvidence node={RECORDED} defaultOpen={false} />);
    expect(screen.queryByTestId("step-panels")).not.toBeInTheDocument();
    await user.click(screen.getByTestId("step-evidence-header"));
    expect(screen.getByTestId("step-panels")).toBeInTheDocument();
  });
});
