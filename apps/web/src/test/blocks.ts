import type { Block, CallStep, Step, Turn } from "@/lib/conversation";

type BlockOf<K extends Block["kind"]> = Extract<Block, { kind: K }>;

export function kinds(turn: Turn): string[] {
  return turn.blocks.map((block) => block.kind);
}

export function blocksOf<K extends Block["kind"]>(turn: Turn, kind: K): BlockOf<K>[] {
  return turn.blocks.filter((block): block is BlockOf<K> => block.kind === kind);
}

/** The one block of a kind. A test that expects one fails here when there are none or two. */
export function blockOf<K extends Block["kind"]>(turn: Turn, kind: K): BlockOf<K> {
  const [first, ...rest] = blocksOf(turn, kind);
  if (first === undefined || rest.length > 0) {
    throw new Error(`expected one ${kind} block, found ${rest.length + (first ? 1 : 0)}`);
  }
  return first;
}

export function callsIn(steps: readonly Step[]): CallStep[] {
  return steps.filter((step): step is CallStep => step.kind === "call");
}

/** Each call of a steps block as "tool:phase:state". */
export function callLines(steps: readonly Step[]): string[] {
  return callsIn(steps).map((step) => `${step.tool}:${step.phase}:${step.state}`);
}
