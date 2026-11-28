import type { Metadata } from "next";
import { LiveTrace } from "@/components/admin/live-trace";

export const metadata: Metadata = { title: "Live trace" };

export default function Live() {
  return <LiveTrace />;
}
