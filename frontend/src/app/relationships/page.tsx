import { redirect } from "next/navigation";

export default function RelationshipsPage() {
  redirect("/explore?tab=relationships");
}
