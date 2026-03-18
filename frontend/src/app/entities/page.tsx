import { redirect } from "next/navigation";

export default function EntitiesPage() {
  redirect("/explore?tab=entities");
}
