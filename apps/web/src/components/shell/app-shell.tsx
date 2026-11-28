"use client";

import { MutationCache, QueryCache, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useCallback, useState, type ReactNode } from "react";
import { Rail } from "@/components/shell/rail";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { TooltipProvider } from "@/components/ui/tooltip";
import { ApiError } from "@/lib/api";
import type { Me } from "@/lib/schemas";
import { SessionProvider, type UiLimits } from "@/lib/session";

const SIGN_IN = "/sign-in";
const MS_PER_S = 1000;
const BUSY = 429;

function busy(error: unknown): error is ApiError {
  return error instanceof ApiError && error.status === BUSY;
}

function queryClient(limits: UiLimits, onSignInEnded: () => void): QueryClient {
  const noticed = (error: unknown) => {
    if (error instanceof ApiError && error.signedOut) onSignInEnded();
  };
  // One screen is several reads and the allowance refills one a second, so a read refused
  // as too many is asked again after a pause that grows, never sooner than the server says.
  const busyWaitMs = (error: ApiError, attempt: number) => {
    const seconds = error.details["retry_after_seconds"];
    const named = typeof seconds === "number" ? seconds * MS_PER_S : 0;
    return Math.max(named, limits.busyWaitMs) * (attempt + 1);
  };
  return new QueryClient({
    queryCache: new QueryCache({ onError: noticed }),
    mutationCache: new MutationCache({ onError: noticed }),
    defaultOptions: {
      queries: {
        // A refusal will be refused again; a failure that may pass is tried again.
        retry: (count, error) => {
          if (busy(error)) return count < limits.busyRetries;
          return count < limits.retries && !(error instanceof ApiError && error.status < 500);
        },
        retryDelay: (attempt, error) =>
          busy(error) ? busyWaitMs(error, attempt) : limits.retryWaitMs,
        refetchOnWindowFocus: false,
      },
    },
  });
}

type Props = { me: Me; limits: UiLimits; signOut: () => void; children: ReactNode };

/**
 * Shown when the sign-in has run out. Nothing is lost: an investigation that was under way
 * goes on from where it stopped once its owner is back.
 */
function SignInAgain({ open }: { open: boolean }) {
  const router = useRouter();
  return (
    <Dialog
      open={open}
      title="Sign in again"
      description="Your sign-in has ended. Nothing was lost: an investigation that was under way continues once you are back."
    >
      <Button variant="primary" onClick={() => router.push(SIGN_IN)}>
        Sign in
      </Button>
    </Dialog>
  );
}

/** One screen: the page never scrolls, each pane scrolls by itself. */
export function AppShell({ me, limits, signOut, children }: Props) {
  const [ended, setEnded] = useState(false);
  const onSignInEnded = useCallback(() => setEnded(true), []);
  const [client] = useState(() => queryClient(limits, onSignInEnded));
  return (
    <QueryClientProvider client={client}>
      <SessionProvider me={me} limits={limits} onSignInEnded={onSignInEnded}>
        <TooltipProvider delayDuration={300}>
          <div className="flex h-dvh min-h-0 bg-surface max-sm:flex-col">
            <Rail signOut={signOut} />
            <main className="flex min-h-0 min-w-0 flex-1">{children}</main>
          </div>
          <SignInAgain open={ended} />
        </TooltipProvider>
      </SessionProvider>
    </QueryClientProvider>
  );
}
