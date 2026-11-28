import { bodyBytesMax } from "@/server/limits";
import { settings } from "@/server/settings";

const REFUSED = 1;

/**
 * Runs once when the server starts, before it answers anything. A server that is set up
 * wrongly, for example with the local sign-in outside a laptop, stops here. The framework
 * would otherwise log the failure and stay up, answering every page with an error.
 */
export function register(): void {
  if (process.env.NEXT_RUNTIME !== "nodejs") return;
  try {
    settings();
    bodyBytesMax();
  } catch (error) {
    process.stderr.write(`${error instanceof Error ? error.message : "The settings are wrong."}\n`);
    process.exit(REFUSED);
  }
}
