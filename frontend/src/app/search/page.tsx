"use client";

import { PageTransition } from "@/components/layout";
import SearchPanel from "@/components/SearchPanel";

export default function SearchPage() {
  return (
    <PageTransition>
      <SearchPanel />
    </PageTransition>
  );
}
