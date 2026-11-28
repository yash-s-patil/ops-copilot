import axe from "axe-core";

// jsdom draws nothing, so contrast cannot be measured here, and a component on its own is
// not a page with landmarks. Both are checked on the real pages by the browser tests.
const NOT_IN_JSDOM = { "color-contrast": { enabled: false }, region: { enabled: false } };

/** The accessibility rules a piece of the page breaks, as "rule: what it asks for". */
export async function violations(node: Element): Promise<string[]> {
  const results = await axe.run(node, { rules: NOT_IN_JSDOM });
  return results.violations.map((violation) => `${violation.id}: ${violation.help}`);
}

const ENGINEER_NEVER_READS = /\b(tools?|tokens?|mcp|pending actions?)\b/gi;
const READ_ALOUD = ["aria-label", "title", "placeholder", "alt"] as const;

/** The words an engineer must never be shown, found in what a piece of the page says. */
export function internalWords(node: Element): string[] {
  const said = [node.textContent ?? ""];
  for (const element of node.querySelectorAll("*")) {
    for (const name of READ_ALOUD) said.push(element.getAttribute(name) ?? "");
  }
  return said.flatMap((text) => text.match(ENGINEER_NEVER_READS) ?? []);
}
