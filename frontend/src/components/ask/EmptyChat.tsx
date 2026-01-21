"use client";

import {
  Bot,
  Search,
  Network,
  Layers,
} from "lucide-react";

export default function EmptyChat() {
  return (
    <div className="h-[400px] flex flex-col items-center justify-center p-8">
      <div className="w-20 h-20 rounded-lg bg-accent/20 flex items-center justify-center mb-6">
        <Bot className="w-10 h-10 text-accent" />
      </div>
      <h3 className="text-lg font-medium text-foreground mb-2">
        Ask Questions
      </h3>
      <p className="text-muted-foreground text-center max-w-md mb-4">
        Ask questions about your documents. I&apos;ll use AI with hybrid
        search, knowledge graphs, and re-ranking to find the best answers.
      </p>
      <div className="flex items-center gap-3 text-xs text-muted-foreground">
        <span className="flex items-center gap-1 px-2 py-1 rounded-full bg-muted">
          <Search className="w-3 h-3" />
          Hybrid Search
        </span>
        <span className="flex items-center gap-1 px-2 py-1 rounded-full bg-muted">
          <Network className="w-3 h-3" />
          Knowledge Graph
        </span>
        <span className="flex items-center gap-1 px-2 py-1 rounded-full bg-muted">
          <Layers className="w-3 h-3" />
          Re-ranking
        </span>
      </div>
    </div>
  );
}
