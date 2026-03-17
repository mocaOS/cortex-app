"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { PageTransition } from "@/components/layout";
import AskPanel from "@/components/AskPanel";

type AskMode = "research" | "chat";

function getValidMode(param: string | null): AskMode {
  if (param === "research" || param === "chat") {
    return param;
  }
  return "research"; // Default to deep research mode
}

function AskPageContent() {
  const searchParams = useSearchParams();
  const mode = getValidMode(searchParams.get("mode"));

  return (
    <PageTransition>
      <AskPanel initialMode={mode} />
    </PageTransition>
  );
}

export default function AskPage() {
  return (
    <Suspense fallback={<div className="py-6">Loading...</div>}>
      <AskPageContent />
    </Suspense>
  );
}
