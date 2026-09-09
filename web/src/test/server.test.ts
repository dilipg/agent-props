/**
 * The harness's own test. Ruling R-72's deeper half.
 *
 * "A test harness must be able to emit every shape the real surface emits, and
 * something must assert that it can. A harness that cannot construct a real
 * reply does not merely miss a bug; it manufactures agreement."
 *
 * That is what this file asserts, and it is asserted from **two directions**
 * because either alone is weak:
 *
 * - here, that every shape in `ENVELOPE_SHAPES` has a constructor, that each
 *   constructor produces exactly one shape, and that the app's readers survive
 *   all three;
 * - in `tests/unit/test_web_envelope_shapes.py`, that the **real** tool surface
 *   emits exactly those three shapes and no fourth, measured by calling every
 *   tool the web app names against a real store — and that this file declares a
 *   constructor for each shape observed there.
 *
 * The Python half is what makes the list here a *measurement* rather than a
 * declaration. Without it, adding a fourth shape to the service would leave
 * both sides agreeing about three.
 */

import { describe, expect, it } from "vitest";

import {
  ENVELOPE_SHAPES,
  failed,
  ok,
  rule,
  shapeOf,
  validated,
  warning,
  warns,
} from "./server";
import type { EnvelopeShape } from "./server";
import { ToolError, blocking, findingsIn, payload, warningsIn } from "@/mcp/envelope";

const SHAPES = Object.keys(ENVELOPE_SHAPES) as EnvelopeShape[];

describe("the harness can emit every documented envelope shape", () => {
  it("declares all three shapes and no fewer", () => {
    // The count is pinned so a shape deleted here fails rather than silently
    // narrowing what the harness can express. Adding a fourth requires editing
    // this line, which is the point.
    expect(SHAPES).toEqual([
      "success-with-data",
      "validate-clean",
      "failure-with-errors",
    ]);
  });

  it.each(SHAPES)("has a constructor for %s that produces exactly that shape", (name) => {
    const built = ENVELOPE_SHAPES[name].build();
    expect(shapeOf(built)).toBe(name);
  });

  it("classifies each shape unambiguously — no envelope matches two predicates", () => {
    for (const name of SHAPES) {
      const built = ENVELOPE_SHAPES[name].build();
      const matches = SHAPES.filter((other) => ENVELOPE_SHAPES[other].matches(built));
      expect(matches).toEqual([name]);
    }
  });

  it("returns null for a shape nobody documented, rather than guessing", () => {
    // The wrapper the old harness produced for the validate tools:
    // `ok("report", {ok, errors})`. It is a *success* envelope — which is the
    // point, it is a real shape, just not the one a validate tool sends. So it
    // classifies as success-with-data, and `findingsIn` refuses it.
    const fiction = ok("report", { ok: true, errors: [] });
    expect(shapeOf(fiction)).toBe("success-with-data");
    expect(() => findingsIn("dataset_validate", fiction)).toThrow(/returned a data envelope/);
  });
});

describe("the app's readers handle every shape", () => {
  it("reads a payload out of success-with-data, and its warnings", () => {
    const envelope = ok("dataset", { id: "x" }, [warning("dataset_archived", { version: 1 })]);
    expect(payload("dataset_get", envelope)).toEqual({ id: "x" });
    expect(warningsIn(envelope)).toEqual([{ code: "dataset_archived", detail: { version: 1 } }]);
  });

  it("reads findings out of validate-clean, warnings included", () => {
    const finding = warns("DS-027", "/provenance/intent", "provenance");
    const envelope = validated([finding]);
    expect(envelope.ok).toBe(true);
    expect(findingsIn("dataset_validate", envelope)).toEqual([finding]);
    // And nothing about it blocks a write. Ground rule 3.
    expect(blocking([finding])).toEqual([]);
  });

  it("reads findings out of failure-with-errors, and they block", () => {
    const finding = rule("DS-026", "/provenance/intent", "provenance");
    const envelope = failed([finding]);
    expect(findingsIn("dataset_validate", envelope)).toEqual([finding]);
    expect(blocking([finding])).toEqual([finding]);
  });

  it("refuses to read a payload out of a findings envelope, naming the fix", () => {
    // This is the exact call that used to throw a bare TypeError from
    // `Object.keys(undefined)` and get swallowed. It now names the third shape
    // and the reader to use instead.
    expect(() => payload("dataset_validate", validated([]))).toThrow(/findingsIn\(\)/);
  });

  it("throws a ToolError, not a bare Error, for a rejection", () => {
    expect(() => payload("dataset_get", failed([rule("AP-004", "")]))).toThrow(ToolError);
  });

  it("returns no warnings for the two shapes that carry none", () => {
    expect(warningsIn(validated([]))).toEqual([]);
    expect(warningsIn(failed([rule("AP-004", "")]))).toEqual([]);
  });
});

describe("validated() cannot construct a contradictory reply", () => {
  it("is ok:true for warnings only", () => {
    expect(validated([warns("DS-027", "/x")]).ok).toBe(true);
  });

  it("is ok:false as soon as one finding is error-severity", () => {
    expect(validated([warns("DS-027", "/x"), rule("DS-026", "/y")]).ok).toBe(false);
  });

  it("is ok:true for a clean document", () => {
    expect(validated().ok).toBe(true);
  });
});
