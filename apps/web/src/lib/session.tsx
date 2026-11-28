"use client";

import { createContext, useContext, useMemo, useState, type ReactNode } from "react";
import type { Me } from "@/lib/schemas";
import { own } from "@/lib/own";

export type UiLimits = {
  inboxRefreshMs: number;
  refundPollMs: number;
  refundPollMaxMs: number;
  messageChars: number;
  reasonChars: number;
  runTokens: number;
  runToolCalls: number;
  retries: number;
  retryWaitMs: number;
  busyRetries: number;
  busyWaitMs: number;
  reconnectMaxMs: number;
};

type Session = {
  me: Me;
  limits: UiLimits;
  technical: boolean;
  setTechnical: (on: boolean) => void;
  can: (permission: string) => boolean;
  /** Told when a request was refused because the sign-in ran out. */
  signInEnded: () => void;
};

const Context = createContext<Session | null>(null);

type Props = { me: Me; limits: UiLimits; onSignInEnded?: () => void; children: ReactNode };

const UNWATCHED = () => undefined;

/** Who is signed in and what they may do, as the server said when the page was made. */
export function SessionProvider({ me, limits, onSignInEnded = UNWATCHED, children }: Props) {
  const [technical, setTechnical] = useState(false);
  const session = useMemo<Session>(
    () => ({
      me,
      limits,
      technical: technical && me.demo_tools,
      setTechnical,
      can: (permission) => me.permissions.includes(permission),
      signInEnded: onSignInEnded,
    }),
    [me, limits, technical, onSignInEnded],
  );
  return <Context value={session}>{children}</Context>;
}

export function useSession(): Session {
  const session = useContext(Context);
  if (session === null) throw new Error("useSession is used outside SessionProvider");
  return session;
}

const ROLE_LABELS: Record<string, string> = {
  "Support.L1": "L1",
  "Support.L2": "L2",
  "Support.Supervisor": "Supervisor",
  "Platform.Admin": "Admin",
};

const ROLE_ORDER = ["Platform.Admin", "Support.Supervisor", "Support.L2", "Support.L1"];

/** The person's role as one word: the highest they hold. */
export function roleLabel(me: Me): string {
  const highest = ROLE_ORDER.find((role) => me.roles.includes(role));
  return highest === undefined ? "No role" : (own(ROLE_LABELS, highest) ?? highest);
}

export function regionLabel(me: Me): string | null {
  if (me.region === null) return null;
  return me.region === "*" ? "all regions" : `${me.region} region`;
}
