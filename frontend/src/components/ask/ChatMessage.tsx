"use client";

import { useState, useEffect, useCallback } from "react";
import { createPortal } from "react-dom";
import { motion, AnimatePresence } from "framer-motion";
import {
  FileText,
  Bot,
  User,
  Zap,
  Loader2,
  Network,
  Search,
  X,
} from "lucide-react";
import { cn } from "@/lib/utils";
import MarkdownRenderer from "@/components/MarkdownRenderer";
import { api } from "@/lib/api";
import type { GraphContext, DocumentContent } from "@/types";

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
  const [selectedSource, setSelectedSource] = useState<Source | null>(null);
  const [documentContent, setDocumentContent] = useState<DocumentContent | null>(null);
  const [isLoadingContent, setIsLoadingContent] = useState(false);
  const [contentError, setContentError] = useState<string | null>(null);

  // Fetch document content when a source is selected
  const handleSourceClick = async (source: Source) => {
    setSelectedSource(source);
    setDocumentContent(null);
    setContentError(null);
    setIsLoadingContent(true);

    try {
      const content = await api.getDocumentContent(source.document_id);
      setDocumentContent(content);
    } catch (error) {
      console.error("Failed to load document content:", error);
      setContentError("Failed to load document content");
    } finally {
      setIsLoadingContent(false);
    }
  };

  // Close modal and reset content state
  const handleCloseModal = () => {
    setSelectedSource(null);
    setDocumentContent(null);
    setContentError(null);
  };

  // Handle escape key to close modal
  const handleKeyDown = useCallback((e: KeyboardEvent) => {
    if (e.key === "Escape" && selectedSource) {
      handleCloseModal();
    }
  }, [selectedSource]);

  useEffect(() => {
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [handleKeyDown]);

  // Prevent body scroll when modal is open
  useEffect(() => {
    if (selectedSource) {
      document.body.style.overflow = "hidden";
    } else {
      document.body.style.overflow = "";
    }
    return () => {
      document.body.style.overflow = "";
    };
  }, [selectedSource]);
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
                  className="text-left p-3 rounded-lg bg-card border border-border cursor-pointer hover:border-accent/50 hover:bg-card/80 transition-colors group"
                  onClick={() => handleSourceClick(source)}
                  role="button"
                  tabIndex={0}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      handleSourceClick(source);
                    }
                  }}
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
                  <p className="text-xs text-accent mt-1 opacity-0 group-hover:opacity-100 transition-opacity">
                    Click to view full document
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

      {/* Document Content Modal - Rendered via Portal to escape overflow container */}
      {typeof document !== "undefined" &&
        createPortal(
          <AnimatePresence>
            {selectedSource && (
              <motion.div
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm"
                onClick={handleCloseModal}
              >
                <motion.div
                  initial={{ opacity: 0, scale: 0.95, y: 20 }}
                  animate={{ opacity: 1, scale: 1, y: 0 }}
                  exit={{ opacity: 0, scale: 0.95, y: 20 }}
                  transition={{ type: "spring", damping: 25, stiffness: 300 }}
                  className="relative w-full max-w-4xl max-h-[85vh] bg-card rounded-xl shadow-2xl border border-border overflow-hidden"
                  onClick={(e) => e.stopPropagation()}
                >
                  {/* Modal Header */}
                  <div className="flex items-center justify-between p-4 border-b border-border bg-muted/50">
                    <div className="flex items-center gap-3 min-w-0">
                      <div className="w-10 h-10 rounded-lg bg-accent/20 flex items-center justify-center shrink-0">
                        <FileText className="w-5 h-5 text-accent" />
                      </div>
                      <div className="min-w-0">
                        <h3 className="text-base font-medium text-foreground truncate">
                          {selectedSource.metadata.filename}
                        </h3>
                        <div className="flex items-center gap-2 text-xs text-muted-foreground">
                          {documentContent && (
                            <>
                              <span>{documentContent.chunk_count} chunks</span>
                              <span>•</span>
                            </>
                          )}
                          <span className="px-1.5 py-0.5 rounded bg-accent/20 text-accent">
                            {(selectedSource.score * 100).toFixed(1)}% relevance
                            {selectedSource.metadata.chunk_index !== undefined && (
                              <> on chunk #{selectedSource.metadata.chunk_index + 1}</>
                            )}
                          </span>
                        </div>
                      </div>
                    </div>
                    <button
                      onClick={handleCloseModal}
                      className="p-2 rounded-lg hover:bg-muted transition-colors"
                      aria-label="Close modal"
                    >
                      <X className="w-5 h-5 text-muted-foreground" />
                    </button>
                  </div>

                  {/* Modal Content */}
                  <div className="p-6 overflow-y-auto max-h-[calc(85vh-80px)]">
                    {isLoadingContent ? (
                      <div className="flex flex-col items-center justify-center py-12">
                        <Loader2 className="w-8 h-8 text-accent animate-spin mb-4" />
                        <p className="text-muted-foreground">Loading document content...</p>
                      </div>
                    ) : contentError ? (
                      <div className="flex flex-col items-center justify-center py-12">
                        <p className="text-destructive mb-2">{contentError}</p>
                        <p className="text-sm text-muted-foreground">
                          Showing matched chunk content instead:
                        </p>
                        <div className="mt-4 p-4 rounded-lg bg-muted/50 w-full">
                          <MarkdownRenderer content={selectedSource.content} />
                        </div>
                      </div>
                    ) : documentContent ? (
                      <MarkdownRenderer content={documentContent.full_content} />
                    ) : (
                      <MarkdownRenderer content={selectedSource.content} />
                    )}
                  </div>
                </motion.div>
              </motion.div>
            )}
          </AnimatePresence>,
          document.body
        )}
    </motion.div>
  );
}
