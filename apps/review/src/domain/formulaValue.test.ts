import { describe, expect, it } from "vitest";

import { displayedNumberTolerance, parseFormulaNumber } from "./formulaValue";

describe("formatted formula values", () => {
  it("parses the percentage and numeric shapes returned by the widget SDK", () => {
    expect(parseFormulaNumber([{ type: "text", text: "28.21%" }])).toBeCloseTo(0.2821);
    expect(parseFormulaNumber(["1.68"])).toBe(1.68);
    expect(parseFormulaNumber([0.1875])).toBe(0.1875);
    expect(parseFormulaNumber("CAD 10.26")).toBe(10.26);
    expect(parseFormulaNumber("AUD 9.91")).toBe(9.91);
    expect(parseFormulaNumber([{ type: "text", text: "A$ 22.73" }])).toBe(22.73);
    expect(parseFormulaNumber("MXN 42.50")).toBe(42.5);
    expect(parseFormulaNumber("£ 1.76")).toBe(1.76);
    expect(parseFormulaNumber("USD 4.55")).toBe(4.55);
    expect(parseFormulaNumber("未计算")).toBeNull();
  });

  it("uses half the displayed unit when a formula is rounded", () => {
    expect(displayedNumberTolerance("28.21%", 0.00010001)).toBeCloseTo(0.00010001);
    expect(displayedNumberTolerance(1.68, 0.00010001)).toBeGreaterThan(0.0049);
    expect(displayedNumberTolerance(1.68, 0.00010001)).toBeLessThan(0.0051);
    expect(displayedNumberTolerance(1.68465187, 0.00010001)).toBe(0.00010001);
  });
});
