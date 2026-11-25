import { describe, expect, it } from "vitest";
import { formatMoney } from "@/lib/format";

const NBSP = " ";

describe("formatMoney", () => {
  it.each([
    [62_000, `INR${NBSP}620.00`],
    [1_240_000, `INR${NBSP}12,400.00`],
    [10_000_000, `INR${NBSP}1,00,000.00`],
    [0, `INR${NBSP}0.00`],
    [1, `INR${NBSP}0.01`],
    [-62_000, `-INR${NBSP}620.00`],
  ])("formats %d minor units as %s", (amountMinor, expected) => {
    expect(formatMoney(amountMinor, "INR")).toBe(expected);
  });

  it("uses the currency code it is given", () => {
    expect(formatMoney(1_050, "USD")).toBe(`USD${NBSP}10.50`);
  });
});
