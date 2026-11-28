import { Suspense } from "react";
import { AskFrame } from "@/components/ask/ask-frame";
import { onlyFor } from "@/server/agent";

export default async function AskLayout({ children }: LayoutProps<"/ask">) {
  await onlyFor("engineer");
  return (
    <Suspense>
      <AskFrame>{children}</AskFrame>
    </Suspense>
  );
}
