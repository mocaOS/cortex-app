"use client";

import {
  Bot,
  Search,
  Network,
  Layers,
  Zap,
  MessageSquare,
} from "lucide-react";

interface EmptyChatProps {
  mode?: "research" | "chat";
}

export default function EmptyChat({ mode = "chat" }: EmptyChatProps) {
  const isResearch = mode === "research";

  return (
    <div className="h-full min-h-[300px] flex flex-col items-center justify-center p-8">
      <div className="w-20 h-20 rounded-lg bg-accent/20 flex items-center justify-center mb-6">
        {isResearch ? (
          <Zap className="w-10 h-10 text-accent" />
        ) : (
          <MessageSquare className="w-10 h-10 text-accent" />
        )}
      </div>
      <h3 className="text-lg font-medium text-foreground mb-2">
        {isResearch ? "Deep Research" : "Chat"}
      </h3>
      <p className="text-muted-foreground text-center max-w-md mb-4">
        {isResearch
          ? "Ask complex questions that require analysis. I'll break down your question into sub-queries, explore multiple angles, and synthesize a comprehensive answer."
          : "Ask questions and get quick answers. I'll search your documents using hybrid search and knowledge graphs to find relevant information fast."}
      </p>
      <div className="flex items-center gap-3 text-xs text-muted-foreground">
        {isResearch ? (
          <>
            <span className="flex items-center gap-1 px-2 py-1 rounded-full bg-muted">
              <Zap className="w-3 h-3" />
              Multi-step Reasoning
            </span>
            <span className="flex items-center gap-1 px-2 py-1 rounded-full bg-muted">
              <Network className="w-3 h-3" />
              Knowledge Graph
            </span>
            <span className="flex items-center gap-1 px-2 py-1 rounded-full bg-muted">
              <Layers className="w-3 h-3" />
              Deep Analysis
            </span>
          </>
        ) : (
          <>
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
          </>
        )}
      </div>
    </div>
  );
}
