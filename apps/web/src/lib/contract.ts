/**
 * Ties the schemas the browser parses with to the agent's published API document. The
 * types in `agent-api.gen.ts` are generated from that document; if a response the agent
 * documents no longer fits what a schema here accepts, this file stops compiling.
 */
import type { z } from "zod";
import type { components } from "@/lib/agent-api.gen";
import type * as s from "@/lib/schemas";

type Documented = components["schemas"];
type Fits<Parsed, Sent extends Parsed> = Sent;

export type AgentContract = [
  Fits<z.input<typeof s.me>, Documented["Me"]>,
  Fits<z.input<typeof s.runView>, Documented["RunView"]>,
  Fits<z.input<typeof s.runList>, Documented["RunList"]>,
  Fits<z.input<typeof s.started>, Documented["Started"]>,
  Fits<z.input<typeof s.decided>, Documented["Decided"]>,
  Fits<z.input<typeof s.executed>, Documented["Executed"]>,
  Fits<z.input<typeof s.resolved>, Documented["Resolved"]>,
  Fits<z.input<typeof s.callDetail>, Documented["CallDetail"]>,
  Fits<z.input<typeof s.adminRuns>, Documented["AdminRuns"]>,
  // The document describes a run's events only as objects; their shape is checked when read.
  Fits<z.input<typeof s.adminRun>, Documented["AdminRunDetail"]["run"]>,
];
