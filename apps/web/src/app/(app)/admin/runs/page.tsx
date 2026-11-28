import type { Metadata } from "next";
import { AdminRuns } from "@/components/admin/runs";

export const metadata: Metadata = { title: "Runs" };

export default function Runs() {
  return <AdminRuns />;
}
