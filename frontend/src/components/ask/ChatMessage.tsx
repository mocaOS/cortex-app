"use client";

import { motion } from "framer-motion";
import {
  FileText,
  Bot,
  User,
  Zap,
  Loader2,
  Network,
  Search,
} from "lucide-react";
import { cn } from "@/lib/utils";
import MarkdownRenderer from "@/components/MarkdownRenderer";
import type { GraphContext } from "@/types";

interface Source {
  document_id: string;
  chunk_id: string;
  content: string;
  score: number;
  metadata: {
    filename: string;
    chunk_index?: number;
    rerank_score?: number;
  };
}

interface Message {
  role: "user" | "assistant";
  content: string;
  sources?: Source[];
  graphContext?: GraphContext;
  reasoningSteps?: string[];
  thinkingSteps?: string[];
  subQuestions?: string[];
  isStreaming?: boolean;
  reranked?: boolean;
}

interface ChatMessageProps {
  message: Message;
  index: number;
  isSourceExpanded: boolean;
  onToggleSourceExpand: () => void;
}

export default function ChatMessage({
  message,
  index,
  isSourceExpanded,
  onToggleSourceExpand,
}: ChatMessageProps) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      className={cn(
        "flex gap-4",
        message.role === "user" ? "flex-row-reverse" : ""
      )}
    >
      <div
        className={cn(
          "w-10 h-10 rounded-lg flex items-center justify-center shrink-0",
          message.role === "user" ? "bg-primary" : "bg-accent/20"
        )}
      >
        {message.role === "user" ? (
          <User className="w-5 h-5 text-primary-foreground" />
        ) : (
          <Bot className="w-5 h-5 text-accent" />
        )}
      </div>

      <div
        className={cn(
          "flex-1 max-w-[80%]",
          message.role === "user" ? "text-right" : ""
        )}
      >
        <div
          className={cn(
            "inline-block rounded-lg p-4",
            message.role === "user"
              ? "bg-primary text-primary-foreground"
              : "bg-muted text-foreground"
          )}
        >
          {message.role === "user" ? (
            <p className="text-sm leading-relaxed whitespace-pre-wrap text-left">
              {message.content}
            </p>
          ) : (
            <div className="text-sm text-left">
              <MarkdownRenderer content={message.content} />
              {message.isStreaming && (
                <span className="inline-block w-2 h-4 bg-foreground animate-pulse ml-1" />
              )}
            </div>
          )}
        </div>

        {/* Sub-Questions */}
        {message.subQuestions && message.subQuestions.length > 0 && (
          <div className="mt-3 p-3 rounded-lg bg-muted border border-border">
            <div className="flex items-center gap-2 mb-2">
              <Search className="w-3 h-3 text-foreground" />
              <span className="text-xs text-foreground font-medium">
                Research Questions
              </span>
            </div>
            <div className="space-y-1">
              {message.subQuestions.map((q, idx) => (
                <div
                  key={idx}
                  className="flex items-start gap-2 text-xs text-muted-foreground"
                >
                  <span className="w-4 h-4 rounded-full bg-border flex items-center justify-center text-[10px] text-foreground shrink-0 mt-0.5">
                    {idx + 1}
                  </span>
                  <span>{q}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Thinking Steps */}
        {message.thinkingSteps && message.thinkingSteps.length > 0 && (
          <div className="mt-3 p-3 rounded-lg bg-muted border border-border">
            <div className="flex items-center gap-2 mb-2">
              <Zap className="w-3 h-3 text-foreground" />
              <span className="text-xs text-foreground font-medium">
                {message.isStreaming ? "Thinking..." : "Research Process"}
              </span>
              {message.isStreaming && (
                <Loader2 className="w-3 h-3 text-foreground animate-spin" />
              )}
            </div>
            <div className="space-y-1 max-h-32 overflow-y-auto">
              {message.thinkingSteps.map((step, idx) => (
                <div
                  key={idx}
                  className={cn(
                    "flex items-start gap-2 text-xs",
                    idx === message.thinkingSteps!.length - 1 && message.isStreaming
                      ? "text-foreground"
                      : "text-muted-foreground"
                  )}
                >
                  <span className="w-4 h-4 rounded-full bg-border flex items-center justify-center text-[10px] text-foreground shrink-0 mt-0.5">
                    {idx + 1}
                  </span>
                  <span>{step}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Reasoning Steps */}
        {message.reasoningSteps && message.reasoningSteps.length > 0 && (
          <div className="mt-3 p-3 rounded-lg bg-muted border border-border">
            <div className="flex items-center gap-2 mb-2">
              <Zap className="w-3 h-3 text-foreground" />
              <span className="text-xs text-foreground font-medium">
                Research Steps
              </span>
            </div>
            <div className="space-y-1">
              {message.reasoningSteps.map((step, idx) => (
                <div
                  key={idx}
                  className="flex items-center gap-2 text-xs text-muted-foreground"
                >
                  <span className="w-4 h-4 rounded-full bg-border flex items-center justify-center text-[10px] text-foreground">
                    {idx + 1}
                  </span>
                  {step}
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Graph Context */}
        {message.graphContext &&
          (message.graphContext.entities.length > 0 ||
            message.graphContext.relationships.length > 0) && (
            <div className="mt-3 p-3 rounded-lg bg-muted border border-border">
              <div className="flex items-center gap-2 mb-2">
                <Network className="w-3 h-3 text-foreground" />
                <span className="text-xs text-foreground font-medium">
                  Knowledge Graph Context
                </span>
              </div>
              <div className="flex flex-wrap gap-1">
                {message.graphContext.entities.slice(0, 5).map((entity, idx) => (
                  <span
                    key={idx}
                    className="px-2 py-0.5 rounded-full bg-border text-xs text-foreground"
                  >
                    {entity.name}
                  </span>
                ))}
                {message.graphContext.entities.length > 5 && (
                  <span className="px-2 py-0.5 text-xs text-muted-foreground">
                    +{message.graphContext.entities.length - 5} more
                  </span>
                )}
              </div>
            </div>
          )}

        {/* Sources */}
        {message.sources && message.sources.length > 0 && (
          <div className="mt-3 space-y-2">
            <div className="flex items-center justify-between">
              <p className="text-xs text-muted-foreground">
                Sources ({message.sources.length})
                {message.reranked && (
                  <span className="ml-2 text-foreground">• Re-ranked</span>
                )}
              </p>
              <button
                onClick={onToggleSourceExpand}
                className="text-xs text-muted-foreground hover:text-foreground"
              >
                {isSourceExpanded ? "Collapse" : "Expand"}
              </button>
            </div>
            {message.sources
              .slice(0, isSourceExpanded ? undefined : 3)
              .map((source, idx) => (
                <div
                  key={idx}
                  className="text-left p-3 rounded-lg bg-card border border-border"
                >
                  <div className="flex items-center gap-2 mb-1">
                    <FileText className="w-3 h-3 text-foreground" />
                    <span className="text-xs text-foreground">
                      {source.metadata.filename}
                    </span>
                    <span className="text-xs text-muted-foreground">
                      ({(source.score * 100).toFixed(0)}% relevance)
                    </span>
                  </div>
                  <p className="text-xs text-muted-foreground line-clamp-2">
                    {source.content}
                  </p>
                </div>
              ))}
            {!isSourceExpanded && message.sources.length > 3 && (
              <button
                onClick={onToggleSourceExpand}
                className="text-xs text-foreground hover:text-muted-foreground"
              >
                Show {message.sources.length - 3} more sources
              </button>
            )}
          </div>
        )}
      </div>
    </motion.div>
  );
}
