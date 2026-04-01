"use client";

import { Suspense } from "react";
import DeduplicationView from "@/components/explore/DeduplicationView";

export default function DeduplicatePage() {
  return (
    <div className="py-6">
      <Suspense>
        <DeduplicationView />
      </Suspense>
    </div>
  );
}
