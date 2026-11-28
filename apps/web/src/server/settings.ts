/** Every setting of the web application, read once and checked before anything is served. */
import "server-only";
import { z } from "zod";

const LOCAL = "local";

const schema = z
  .object({
    ENVIRONMENT: z.enum(["local", "demo", "prod"]),
    AUTH_MODE: z.enum(["entra", "dev"]),
    AUTH_SECRET: z.string().min(32),
    AGENT_URL: z.url(),
    AGENT_SCOPE: z.string().min(1),
    LIMITS_FILE: z.string().min(1),
    AUTH_MICROSOFT_ENTRA_ID_ID: z.string().optional(),
    AUTH_MICROSOFT_ENTRA_ID_SECRET: z.string().optional(),
    AUTH_MICROSOFT_ENTRA_ID_ISSUER: z.url().optional(),
    DEV_TOKEN_URL: z.url().optional(),
    WEB_CLIENT_ID: z.string().optional(),
    WEB_CLIENT_SECRET: z.string().min(32).optional(),
  })
  .superRefine((env, issues) => {
    const need = (names: readonly (keyof typeof env)[], why: string) => {
      for (const name of names) {
        if (!env[name]) issues.addIssue({ code: "custom", message: `${why} needs ${name}` });
      }
    };
    if (env.AUTH_MODE === "dev") {
      if (env.ENVIRONMENT !== LOCAL) {
        issues.addIssue({
          code: "custom",
          message: "AUTH_MODE=dev is allowed only when ENVIRONMENT is local",
        });
      }
      need(["DEV_TOKEN_URL", "WEB_CLIENT_ID", "WEB_CLIENT_SECRET"], "AUTH_MODE=dev");
    } else {
      need(
        [
          "AUTH_MICROSOFT_ENTRA_ID_ID",
          "AUTH_MICROSOFT_ENTRA_ID_SECRET",
          "AUTH_MICROSOFT_ENTRA_ID_ISSUER",
        ],
        "AUTH_MODE=entra",
      );
    }
    if (env.ENVIRONMENT !== LOCAL && !env.AGENT_URL.startsWith("https://")) {
      issues.addIssue({ code: "custom", message: "AGENT_URL must be https outside local" });
    }
  });

export type Settings = z.infer<typeof schema>;

let read: Settings | null = null;

/** Names what is wrong and never what was given, since some of it is secret. */
export function settings(): Settings {
  if (read === null) {
    const parsed = schema.safeParse(process.env);
    if (!parsed.success) {
      const problems = parsed.error.issues.map(
        (issue) => `${issue.path.join(".") || "settings"}: ${issue.message}`,
      );
      throw new Error(`The web application cannot start. ${problems.join("; ")}`);
    }
    read = parsed.data;
  }
  return read;
}

export function isLocal(): boolean {
  return settings().ENVIRONMENT === LOCAL;
}
