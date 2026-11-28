import type { Metadata } from "next";
import { AdminRunDetail } from "@/components/admin/runs";

export const metadata: Metadata = { title: "Run" };

export default function Run() {
  return <AdminRunDetail />;
}
