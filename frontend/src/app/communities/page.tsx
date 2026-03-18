import { redirect } from "next/navigation";

export default function CommunitiesPage() {
  redirect("/explore?tab=communities");
}
