/**
 * Sign-in. The token for the agent is kept inside the encrypted, httpOnly session cookie.
 * It is never put on the session object, which is what a page script could ask for, so no
 * script in the browser can read it.
 */
import NextAuth, { type NextAuthConfig } from "next-auth";
import type { JWT } from "next-auth/jwt";
import Credentials from "next-auth/providers/credentials";
import MicrosoftEntraID from "next-auth/providers/microsoft-entra-id";
import { z } from "zod";
import { isDemoUser } from "@/server/demo-users";
import { settings } from "@/server/settings";

declare module "next-auth" {
  interface User {
    accessToken?: string;
    expiresAt?: number;
  }
  interface Session {
    error?: "RefreshTokenError";
  }
}

declare module "next-auth/jwt" {
  interface JWT {
    accessToken?: string;
    expiresAt?: number;
    refreshToken?: string;
    error?: "RefreshTokenError";
  }
}

const MS_PER_S = 1000;
/** Renewed this long before it runs out, so a request already on its way still carries a good one. */
const RENEW_BEFORE_S = 300;
const DEMO_GRANT = "urn:opscp:params:oauth:grant-type:demo-user";
const ENTRA = "microsoft-entra-id";

const issued = z.object({
  access_token: z.string().min(1),
  expires_in: z.number().positive(),
  refresh_token: z.string().optional(),
});

async function tokenFrom(url: string, form: Record<string, string>) {
  const response = await fetch(url, {
    method: "POST",
    body: new URLSearchParams(form),
    cache: "no-store",
  });
  if (!response.ok) return null;
  const parsed = issued.safeParse(await response.json().catch(() => null));
  return parsed.success ? parsed.data : null;
}

function nowS(): number {
  return Math.floor(Date.now() / MS_PER_S);
}

/** Local development only: the local issuer gives a token for a named demo user. */
function demoSignIn() {
  const env = settings();
  return Credentials({
    credentials: { user: {} },
    authorize: async (credentials) => {
      if (!isDemoUser(credentials.user)) return null;
      const token = await tokenFrom(env.DEV_TOKEN_URL ?? "", {
        grant_type: DEMO_GRANT,
        client_id: env.WEB_CLIENT_ID ?? "",
        client_secret: env.WEB_CLIENT_SECRET ?? "",
        scope: env.AGENT_SCOPE,
        user: credentials.user,
      });
      if (token === null) return null;
      return {
        id: credentials.user,
        accessToken: token.access_token,
        expiresAt: nowS() + token.expires_in,
      };
    },
  });
}

function entraSignIn() {
  const env = settings();
  return MicrosoftEntraID({
    clientId: env.AUTH_MICROSOFT_ENTRA_ID_ID,
    clientSecret: env.AUTH_MICROSOFT_ENTRA_ID_SECRET,
    issuer: env.AUTH_MICROSOFT_ENTRA_ID_ISSUER,
    authorization: { params: { scope: `openid profile offline_access ${env.AGENT_SCOPE}` } },
  });
}

async function renewed(token: JWT): Promise<JWT> {
  const env = settings();
  if (env.AUTH_MODE !== "entra" || !token.refreshToken) {
    return { ...token, error: "RefreshTokenError" };
  }
  const issuer = (env.AUTH_MICROSOFT_ENTRA_ID_ISSUER ?? "").replace(/\/v2\.0\/?$/, "");
  const fresh = await tokenFrom(`${issuer}/oauth2/v2.0/token`, {
    grant_type: "refresh_token",
    client_id: env.AUTH_MICROSOFT_ENTRA_ID_ID ?? "",
    client_secret: env.AUTH_MICROSOFT_ENTRA_ID_SECRET ?? "",
    refresh_token: token.refreshToken,
    scope: `openid profile offline_access ${env.AGENT_SCOPE}`,
  });
  if (fresh === null) return { ...token, error: "RefreshTokenError" };
  return {
    ...token,
    accessToken: fresh.access_token,
    expiresAt: nowS() + fresh.expires_in,
    refreshToken: fresh.refresh_token ?? token.refreshToken,
    error: undefined,
  };
}

/** Made on first use, so that importing this module, as a build does, reads no settings. */
const config = (): NextAuthConfig => ({
  providers: [settings().AUTH_MODE === "dev" ? demoSignIn() : entraSignIn()],
  session: { strategy: "jwt" },
  pages: { signIn: "/sign-in" },
  callbacks: {
    jwt: async ({ token, account, user }) => {
      if (account?.provider === ENTRA) {
        return {
          ...token,
          accessToken: account.access_token,
          expiresAt: account.expires_at,
          refreshToken: account.refresh_token,
        };
      }
      if (user?.accessToken !== undefined) {
        return { ...token, accessToken: user.accessToken, expiresAt: user.expiresAt };
      }
      if (token.expiresAt !== undefined && nowS() < token.expiresAt - RENEW_BEFORE_S) return token;
      return renewed(token);
    },
    // Only that the session is good or not. The token stays in the cookie.
    session: ({ session, token }) => ({ ...session, error: token.error }),
  },
});

export const { handlers, auth, signIn, signOut } = NextAuth(config);

export function signInProvider(): string {
  return settings().AUTH_MODE === "dev" ? "credentials" : ENTRA;
}
