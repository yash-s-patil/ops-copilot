import { signOut } from "@/auth";
import { AppShell } from "@/components/shell/app-shell";
import { currentUser } from "@/server/agent";
import { uiLimits } from "@/server/limits";

async function leave() {
  "use server";
  await signOut({ redirectTo: "/sign-in" });
}

export default async function AppLayout({ children }: LayoutProps<"/">) {
  const me = await currentUser();
  return (
    <AppShell me={me} limits={uiLimits()} signOut={leave}>
      {children}
    </AppShell>
  );
}
