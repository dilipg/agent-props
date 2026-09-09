/**
 * The detail view: narrative and intent side by side, and not the same text.
 *
 * PRD 5.7 requires both halves. The layout assertion is that the two sections
 * are siblings in one two-column grid — the DOM fact behind "side by side" —
 * and the content assertion is that each carries its own field in full. The
 * second matters because the mistake PRD 5.7 names is an author filling both
 * with the same text, and a view that rendered the narrative twice would look
 * fine and hide exactly that.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { DatasetDetail } from "./DatasetDetail";
import { GOLDEN_BLUEPRINT, PRIYA } from "@/test/fixtures";

const DIMENSIONS = Object.keys(
  (GOLDEN_BLUEPRINT as unknown as { label_schema: { dimensions: Record<string, string[]> } })
    .label_schema.dimensions,
);

describe("the dataset detail view", () => {
  it("puts narrative and intent in the same two-column container", () => {
    render(<DatasetDetail dataset={PRIYA} dimensions={DIMENSIONS} />);
    const container = screen.getByTestId("narrative-and-intent");
    const narrative = screen.getByTestId("detail-narrative");
    const intent = screen.getByTestId("detail-intent");
    expect(narrative.parentElement).toBe(container);
    expect(intent.parentElement).toBe(container);
    expect(container.className).toContain("md:grid-cols-2");
  });

  it("renders each field in full, and they are different text", () => {
    render(<DatasetDetail dataset={PRIYA} dimensions={DIMENSIONS} />);
    expect(screen.getByTestId("detail-narrative")).toHaveTextContent(PRIYA.narrative);
    expect(screen.getByTestId("detail-intent")).toHaveTextContent(PRIYA.provenance.intent);
    // The fixture itself must satisfy the invariant, or this proves nothing.
    expect(PRIYA.narrative).not.toBe(PRIYA.provenance.intent);
  });

  it("labels each column with the question it answers", () => {
    // "They answer different questions" is the reason the brief gives for the
    // layout, so the headings say which question — otherwise the reader has two
    // paragraphs and no reason they are apart.
    render(<DatasetDetail dataset={PRIYA} dimensions={DIMENSIONS} />);
    expect(screen.getByTestId("detail-narrative")).toHaveTextContent("what happens in the world");
    expect(screen.getByTestId("detail-intent")).toHaveTextContent(
      "why this dataset exists in the suite",
    );
  });

  it("carries the author and the complete label set into the header", () => {
    render(<DatasetDetail dataset={PRIYA} dimensions={DIMENSIONS} />);
    expect(screen.getByTestId("detail-author")).toHaveAttribute("data-handle", "pnair");
    const shown = screen.getAllByTestId("labels")[0];
    expect(
      new Set([...(shown?.querySelectorAll("[data-dimension]") ?? [])].map((c) =>
        c.getAttribute("data-dimension"),
      )),
    ).toEqual(new Set(DIMENSIONS));
  });
});
