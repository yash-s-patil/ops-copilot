import { redirect } from "next/navigation";
import { currentUser, homeOf } from "@/server/agent";

export default async function Home() {
  redirect(homeOf(await currentUser()));
}
