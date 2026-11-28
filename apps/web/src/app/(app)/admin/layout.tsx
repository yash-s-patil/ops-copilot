import { onlyFor } from "@/server/agent";

export default async function AdminLayout({ children }: LayoutProps<"/admin">) {
  await onlyFor("admin");
  return children;
}
