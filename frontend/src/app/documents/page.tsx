"use client";

import { Suspense } from "react";
import { PageTransition } from "@/components/layout";
import DocumentList from "@/components/DocumentList";
import { Loader2 } from "lucide-react";

function DocumentsContent() {
  return (
    <DocumentList onDelete={() => {}} />
  );
}

export default function DocumentsPage() {
  return (
    <PageTransition>
      <Suspense
        fallback={
          <div className="glass rounded-lg p-12 text-center">
            <Loader2 className="w-8 h-8 text-foreground animate-spin mx-auto mb-4" />
            <p className="text-muted-foreground">Loading documents...</p>
          </div>
        }
      >
        <DocumentsContent />
      </Suspense>
    </PageTransition>
  );
}
