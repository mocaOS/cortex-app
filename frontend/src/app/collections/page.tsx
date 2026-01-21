"use client";

import { PageTransition } from "@/components/layout";
import CollectionPanel from "@/components/CollectionPanel";

export default function CollectionsPage() {
  return (
    <PageTransition>
      <CollectionPanel onRefresh={() => {}} />
    </PageTransition>
  );
}
