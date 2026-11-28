import type { Metadata } from "next";
import { Layers } from "lucide-react";
import { AuthError } from "next-auth";
import { redirect } from "next/navigation";
import { signIn, signInProvider } from "@/auth";
import { DEMO_USERS, isDemoUser } from "@/server/demo-users";
import { settings } from "@/server/settings";

export const metadata: Metadata = { title: "Sign in" };

const FAILED = "/sign-in?failed=1";
const BUTTON =
  "flex h-[30px] items-center justify-center rounded-control border border-accent bg-accent px-[11px] font-medium text-on-accent hover:border-accent-ink hover:bg-accent-ink pointer-coarse:min-h-11";

async function signInWithMicrosoft() {
  "use server";
  await signIn(signInProvider(), { redirectTo: "/" });
}

async function signInAs(form: FormData) {
  "use server";
  const user = form.get("user");
  if (!isDemoUser(user)) redirect(FAILED);
  try {
    await signIn(signInProvider(), { user, redirectTo: "/" });
  } catch (error) {
    // A successful sign-in leaves by throwing a redirect, which must go on its way.
    if (error instanceof AuthError) redirect(FAILED);
    throw error;
  }
}

function DemoUsers() {
  return (
    <form action={signInAs} className="flex flex-col gap-1">
      <p className="text-meta text-ink-3">
        Local development. Sign in as one of the five demo users.
      </p>
      {DEMO_USERS.map((user) => (
        <button
          key={user.id}
          type="submit"
          name="user"
          value={user.id}
          className="flex min-h-11 items-center justify-between gap-3 rounded-control border border-line-2 bg-surface px-3 text-left hover:bg-raised"
        >
          <span className="font-medium">{user.name}</span>
          <span className="text-meta text-ink-3">{user.role}</span>
        </button>
      ))}
    </form>
  );
}

export default async function SignIn(props: PageProps<"/sign-in">) {
  const failed = (await props.searchParams)["failed"] !== undefined;
  const dev = settings().AUTH_MODE === "dev";
  return (
    <main className="grid h-dvh place-items-center overflow-y-auto p-4">
      <div className="flex w-full max-w-[360px] flex-col gap-4 rounded-card border border-line bg-surface p-5">
        <div className="flex items-center gap-[9px]">
          <span
            aria-hidden="true"
            className="grid size-[26px] place-items-center rounded-[7px] bg-ink text-surface"
          >
            <Layers size={16} strokeWidth={1.75} />
          </span>
          <div>
            <h1 className="text-title font-semibold tracking-[-0.01em]">Ops Copilot</h1>
            <p className="text-fine text-ink-3">Meridian Retail</p>
          </div>
        </div>
        {failed && (
          <p role="alert" className="text-bad">
            Sign-in did not work. Try again.
          </p>
        )}
        {dev ? (
          <DemoUsers />
        ) : (
          <form action={signInWithMicrosoft} className="flex flex-col gap-2">
            <p className="text-ink-2">Sign in with your Meridian account to open your inbox.</p>
            <button type="submit" className={BUTTON}>
              Sign in with Microsoft
            </button>
          </form>
        )}
      </div>
    </main>
  );
}
