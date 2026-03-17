"use client";

import { useEffect, Suspense } from "react";
import { useSearchParams, useRouter } from "next/navigation";

function AskPageRedirect() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const mode = searchParams.get("mode");

  useEffect(() => {
    // Redirect to explore page with appropriate tab
    const tab = mode === "chat" ? "chat" : "research";
    router.replace(`/explore?tab=${tab}`);
  }, [router, mode]);

  return (
    <div className="py-6 flex items-center justify-center h-96">
      <p className="text-muted-foreground">Redirecting...</p>
    </div>
  );
}

export default function AskPage() {
  return (
    <Suspense fallback={<div className="py-6">Redirecting...</div>}>
      <AskPageRedirect />
    </Suspense>
  );
}
