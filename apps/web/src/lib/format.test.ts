import { describe, expect, it } from "vitest";
import {
  formatAge,
  formatBytes,
  formatClock,
  formatCount,
  formatMoney,
  formatSeconds,
  formatUtc,
  initials,
  plural,
  sentenceCase,
} from "@/lib/format";
import { NBSP } from "@/test/fixtures";

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

describe("formatCount", () => {
  it.each([
    [0, "0"],
    [999, "999"],
    [12_400, "12,400"],
    [1_234_567, "12,34,567"],
    [41.6, "42"],
  ])("writes %d as %s, grouped the Indian way and rounded", (value, expected) => {
    expect(formatCount(value)).toBe(expected);
  });
});

describe("formatAge", () => {
  const now = new Date("2026-11-28T10:00:00Z");
  const ago = (ms: number) => new Date(now.getTime() - ms);
  const MIN = 60_000;
  const HOUR = 60 * MIN;

  it.each([
    [0, "now"],
    [59_999, "now"],
    [MIN, "1 min"],
    [59 * MIN, "59 min"],
    [HOUR, "1 h"],
    [23 * HOUR + 59 * MIN, "23 h"],
    [24 * HOUR, "1 d"],
    [4 * 24 * HOUR + HOUR, "4 d"],
  ])("says something %d ms old is %s", (ms, expected) => {
    expect(formatAge(ago(ms), now)).toBe(expected);
  });

  it("says now for a time slightly ahead of this clock", () => {
    expect(formatAge(ago(-5_000), now)).toBe("now");
  });
});

describe("formatSeconds and formatBytes", () => {
  it.each([
    [0, "0.0 s"],
    [120, "0.1 s"],
    [4_100, "4.1 s"],
    [61_000, "61.0 s"],
  ])("writes %d ms as %s", (ms, expected) => {
    expect(formatSeconds(ms)).toBe(expected);
  });

  it.each([
    [0, "0 B"],
    [999, "999 B"],
    [1_000, "1.0 kB"],
    [16_384, "16.4 kB"],
  ])("writes %d bytes as %s", (bytes, expected) => {
    expect(formatBytes(bytes)).toBe(expected);
  });
});

describe("formatClock and formatUtc", () => {
  const two = (value: number) => String(value).padStart(2, "0");

  it.each(["2026-11-28T09:05:00Z", "2026-11-28T18:30:00Z", "2026-11-27T18:35:00Z"])(
    "writes %s as hours and minutes of a 24-hour clock in the viewer's zone",
    (iso) => {
      const at = new Date(iso);
      expect(formatClock(at)).toBe(`${two(at.getHours())}:${two(at.getMinutes())}`);
    },
  );

  it("writes the full time in UTC to the minute", () => {
    expect(formatUtc(new Date("2026-11-28T09:05:59.123Z"))).toBe("2026-11-28 09:05 UTC");
  });
});

describe("initials", () => {
  it.each([
    ["Priya Nair", "PN"],
    ["arjun mehta", "AM"],
    ["Kavita", "K"],
    ["Kavita  Devi Rao", "KD"],
    ["  Sam Admin ", "SA"],
    ["", ""],
  ])("takes %j to %j", (name, expected) => {
    expect(initials(name)).toBe(expected);
  });
});

describe("plural and sentenceCase", () => {
  it.each([
    [0, "0 steps"],
    [1, "1 step"],
    [2, "2 steps"],
  ])("counts %d as %s", (count, expected) => {
    expect(plural(count, "step")).toBe(expected);
  });

  it.each([
    ["lost_in_transit", "Lost in transit"],
    ["shipped", "Shipped"],
    ["", ""],
  ])("writes %j as %j", (text, expected) => {
    expect(sentenceCase(text)).toBe(expected);
  });
});
