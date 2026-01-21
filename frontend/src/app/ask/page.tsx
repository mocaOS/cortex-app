"use client";

import { PageTransition } from "@/components/layout";
import AskPanel from "@/components/AskPanel";

export default function AskPage() {
  return (
    <PageTransition>
      <AskPanel />
    </PageTransition>
  );
}
