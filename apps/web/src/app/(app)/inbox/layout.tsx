import { Suspense } from "react";
import { InboxFrame } from "@/components/inbox/inbox-frame";
import { onlyFor } from "@/server/agent";

export default async function InboxLayout({ children }: LayoutProps<"/inbox">) {
  await onlyFor("engineer");
  return (
    <Suspense>
      <InboxFrame>{children}</InboxFrame>
    </Suspense>
  );
}
