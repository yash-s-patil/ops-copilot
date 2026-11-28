import type { Metadata } from "next";
import { Suspense } from "react";
import { AskFrame } from "@/components/ask/ask-frame";
import { onlyFor } from "@/server/agent";

export const metadata: Metadata = { title: "Ask Copilot" };

export default async function AskLayout({ children }: LayoutProps<"/ask">) {
  await onlyFor("engineer");
  return (
    <Suspense>
      <AskFrame>{children}</AskFrame>
    </Suspense>
  );
}
