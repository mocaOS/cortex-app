"use client";

import { useState, useRef, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  MessageSquare,
  Send,
  Loader2,
  FileText,
  Bot,
  User,
  Zap,
  RotateCcw,
  Settings2,
  ChevronDown,
  ChevronUp,
  Network,
  Search,
  Layers,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { api } from "@/lib/api";
import type { ConversationMessage, SearchResult, GraphContext } from "@/types";
import MarkdownRenderer from "./MarkdownRenderer";

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

export default function AskPanel() {
  const [question, setQuestion] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [useStreaming, setUseStreaming] = useState(true);
  const [useAgentic, setUseAgentic] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [expandedSources, setExpandedSources] = useState<Set<number>>(new Set());
  const messagesEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const getConversationHistory = (): ConversationMessage[] => {
    return messages
      .filter((m) => !m.isStreaming)
      .map((m) => ({
        role: m.role,
        content: m.content,
      }));
  };

  const handleAsk = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!question.trim() || isLoading) return;

    const userMessage: Message = { role: "user", content: question };
    setMessages((prev) => [...prev, userMessage]);
    setQuestion("");
    setIsLoading(true);

    const conversationHistory = getConversationHistory();

    if (useStreaming) {
      const assistantMessage: Message = {
        role: "assistant",
        content: "",
        isStreaming: true,
        thinkingSteps: useAgentic ? [] : undefined,
        subQuestions: useAgentic ? [] : undefined,
      };
      setMessages((prev) => [...prev, assistantMessage]);

      try {
        let sources: Source[] = [];
        let graphContext: GraphContext | undefined;
        let content = "";
        let thinkingSteps: string[] = [];
        let subQuestions: string[] = [];

        for await (const event of api.askStream(question, {
          conversationHistory,
          useReranking: true,
          useGraph: true,
          useAgentic,
        })) {
          if (event.thinking) {
            thinkingSteps = [...thinkingSteps, event.thinking as string];
            setMessages((prev) => {
              const updated = [...prev];
              const lastIdx = updated.length - 1;
              updated[lastIdx] = { ...updated[lastIdx], thinkingSteps };
              return updated;
            });
          }
          if (event.sub_questions) {
            subQuestions = event.sub_questions as string[];
            setMessages((prev) => {
              const updated = [...prev];
              const lastIdx = updated.length - 1;
              updated[lastIdx] = { ...updated[lastIdx], subQuestions };
              return updated;
            });
          }
          if (event.retrieval) {
            thinkingSteps = [...thinkingSteps, event.retrieval as string];
            setMessages((prev) => {
              const updated = [...prev];
              const lastIdx = updated.length - 1;
              updated[lastIdx] = { ...updated[lastIdx], thinkingSteps };
              return updated;
            });
          }
          if (event.sources) {
            sources = event.sources as Source[];
          }
          if (event.graph_context) {
            graphContext = event.graph_context;
          }
          if (event.content) {
            content += event.content;
            setMessages((prev) => {
              const updated = [...prev];
              const lastIdx = updated.length - 1;
              updated[lastIdx] = { ...updated[lastIdx], content };
              return updated;
            });
          }
          if (event.done) {
            setMessages((prev) => {
              const updated = [...prev];
              const lastIdx = updated.length - 1;
              updated[lastIdx] = {
                ...updated[lastIdx],
                content,
                sources,
                graphContext,
                thinkingSteps: thinkingSteps.length > 0 ? thinkingSteps : undefined,
                subQuestions: subQuestions.length > 0 ? subQuestions : undefined,
                isStreaming: false,
                reranked: true,
              };
              return updated;
            });
          }
          if (event.error) {
            setMessages((prev) => {
              const updated = [...prev];
              const lastIdx = updated.length - 1;
              updated[lastIdx] = {
                ...updated[lastIdx],
                content: `Error: ${event.error}`,
                isStreaming: false,
              };
              return updated;
            });
          }
        }
      } catch (error) {
        setMessages((prev) => {
          const updated = [...prev];
          const lastIdx = updated.length - 1;
          updated[lastIdx] = {
            ...updated[lastIdx],
            content: "Sorry, I encountered an error processing your question.",
            isStreaming: false,
          };
          return updated;
        });
      }
    } else {
      try {
        const data = await api.ask(question, {
          conversationHistory,
          useReranking: true,
          useAgentic,
          useGraph: true,
        });

        const assistantMessage: Message = {
          role: "assistant",
          content: data.answer,
          sources: data.sources as Source[],
          graphContext: data.graph_context,
          reasoningSteps: data.reasoning_steps,
          reranked: data.reranked,
        };
        setMessages((prev) => [...prev, assistantMessage]);
      } catch (error) {
        const errorMessage: Message = {
          role: "assistant",
          content: "Sorry, I encountered an error processing your question.",
        };
        setMessages((prev) => [...prev, errorMessage]);
      }
    }

    setIsLoading(false);
  };

  const clearConversation = () => {
    setMessages([]);
  };

  const toggleSourceExpand = (index: number) => {
    setExpandedSources((prev) => {
      const next = new Set(prev);
      if (next.has(index)) {
        next.delete(index);
      } else {
        next.add(index);
      }
      return next;
    });
  };

  return (
    <div className="space-y-6">
      {/* Settings Bar */}
      <div className="glass rounded-lg p-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-4">
            <button
              onClick={() => setShowSettings(!showSettings)}
              className={cn(
                "flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm transition-colors",
                showSettings
                  ? "bg-accent text-accent-foreground"
                  : "text-muted-foreground hover:text-foreground hover:bg-muted"
              )}
            >
              <Settings2 className="w-4 h-4" />
              Settings
              {showSettings ? (
                <ChevronUp className="w-4 h-4" />
              ) : (
                <ChevronDown className="w-4 h-4" />
              )}
            </button>

            {messages.length > 0 && (
              <button
                onClick={clearConversation}
                className="flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
              >
                <RotateCcw className="w-4 h-4" />
                Clear
              </button>
            )}
          </div>

          <div className="flex items-center gap-3">
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <span className="flex items-center gap-1">
                <Search className="w-3 h-3" />
                Hybrid
              </span>
              <span className="flex items-center gap-1">
                <Layers className="w-3 h-3" />
                Reranking
              </span>
              {useAgentic && (
                <span className="flex items-center gap-1 text-foreground">
                  <Zap className="w-3 h-3" />
                  Agentic
                </span>
              )}
            </div>
          </div>
        </div>

        <AnimatePresence>
          {showSettings && (
            <motion.div
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: "auto", opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              className="overflow-hidden"
            >
              <div className="pt-4 mt-4 border-t border-border grid grid-cols-2 gap-4">
                <label className="flex items-center gap-3 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={useStreaming}
                    onChange={(e) => setUseStreaming(e.target.checked)}
                    className="w-4 h-4 rounded border-border bg-card text-foreground focus:ring-ring"
                  />
                  <div>
                    <span className="text-sm text-foreground">
                      Streaming Responses
                    </span>
                    <p className="text-xs text-muted-foreground">
                      See answers as they&apos;re generated
                    </p>
                  </div>
                </label>

                <label className="flex items-center gap-3 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={useAgentic}
                    onChange={(e) => setUseAgentic(e.target.checked)}
                    className="w-4 h-4 rounded border-border bg-card text-foreground focus:ring-ring"
                  />
                  <div>
                    <span className="text-sm text-foreground">
                      Deep Research Mode
                    </span>
                    <p className="text-xs text-muted-foreground">
                      Multi-step reasoning for complex questions
                    </p>
                  </div>
                </label>
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>

      {/* Chat History */}
      <div className="glass rounded-lg min-h-[400px] max-h-[600px] overflow-y-auto">
        {messages.length === 0 ? (
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
        ) : (
          <div className="p-6 space-y-6">
            <AnimatePresence initial={false}>
              {messages.map((msg, index) => (
                <motion.div
                  key={index}
                  initial={{ opacity: 0, y: 20 }}
                  animate={{ opacity: 1, y: 0 }}
                  className={cn(
                    "flex gap-4",
                    msg.role === "user" ? "flex-row-reverse" : ""
                  )}
                >
                  <div
                    className={cn(
                      "w-10 h-10 rounded-lg flex items-center justify-center shrink-0",
                      msg.role === "user" ? "bg-primary" : "bg-accent/20"
                    )}
                  >
                    {msg.role === "user" ? (
                      <User className="w-5 h-5 text-primary-foreground" />
                    ) : (
                      <Bot className="w-5 h-5 text-accent" />
                    )}
                  </div>

                  <div
                    className={cn(
                      "flex-1 max-w-[80%]",
                      msg.role === "user" ? "text-right" : ""
                    )}
                  >
                    <div
                      className={cn(
                        "inline-block rounded-lg p-4",
                        msg.role === "user"
                          ? "bg-primary text-primary-foreground"
                          : "bg-muted text-foreground"
                      )}
                    >
                      {msg.role === "user" ? (
                        <p className="text-sm leading-relaxed whitespace-pre-wrap text-left">
                          {msg.content}
                        </p>
                      ) : (
                        <div className="text-sm text-left">
                          <MarkdownRenderer content={msg.content} />
                          {msg.isStreaming && (
                            <span className="inline-block w-2 h-4 bg-foreground animate-pulse ml-1" />
                          )}
                        </div>
                      )}
                    </div>

                    {/* Sub-Questions */}
                    {msg.subQuestions && msg.subQuestions.length > 0 && (
                      <div className="mt-3 p-3 rounded-lg bg-muted border border-border">
                        <div className="flex items-center gap-2 mb-2">
                          <Search className="w-3 h-3 text-foreground" />
                          <span className="text-xs text-foreground font-medium">
                            Research Questions
                          </span>
                        </div>
                        <div className="space-y-1">
                          {msg.subQuestions.map((q, idx) => (
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
                    {msg.thinkingSteps && msg.thinkingSteps.length > 0 && (
                      <div className="mt-3 p-3 rounded-lg bg-muted border border-border">
                        <div className="flex items-center gap-2 mb-2">
                          <Zap className="w-3 h-3 text-foreground" />
                          <span className="text-xs text-foreground font-medium">
                            {msg.isStreaming ? "Thinking..." : "Research Process"}
                          </span>
                          {msg.isStreaming && (
                            <Loader2 className="w-3 h-3 text-foreground animate-spin" />
                          )}
                        </div>
                        <div className="space-y-1 max-h-32 overflow-y-auto">
                          {msg.thinkingSteps.map((step, idx) => (
                            <div
                              key={idx}
                              className={cn(
                                "flex items-start gap-2 text-xs",
                                idx === msg.thinkingSteps!.length - 1 && msg.isStreaming
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
                    {msg.reasoningSteps && msg.reasoningSteps.length > 0 && (
                      <div className="mt-3 p-3 rounded-lg bg-muted border border-border">
                        <div className="flex items-center gap-2 mb-2">
                          <Zap className="w-3 h-3 text-foreground" />
                          <span className="text-xs text-foreground font-medium">
                            Research Steps
                          </span>
                        </div>
                        <div className="space-y-1">
                          {msg.reasoningSteps.map((step, idx) => (
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
                    {msg.graphContext &&
                      (msg.graphContext.entities.length > 0 ||
                        msg.graphContext.relationships.length > 0) && (
                        <div className="mt-3 p-3 rounded-lg bg-muted border border-border">
                          <div className="flex items-center gap-2 mb-2">
                            <Network className="w-3 h-3 text-foreground" />
                            <span className="text-xs text-foreground font-medium">
                              Knowledge Graph Context
                            </span>
                          </div>
                          <div className="flex flex-wrap gap-1">
                            {msg.graphContext.entities.slice(0, 5).map((entity, idx) => (
                              <span
                                key={idx}
                                className="px-2 py-0.5 rounded-full bg-border text-xs text-foreground"
                              >
                                {entity.name}
                              </span>
                            ))}
                            {msg.graphContext.entities.length > 5 && (
                              <span className="px-2 py-0.5 text-xs text-muted-foreground">
                                +{msg.graphContext.entities.length - 5} more
                              </span>
                            )}
                          </div>
                        </div>
                      )}

                    {/* Sources */}
                    {msg.sources && msg.sources.length > 0 && (
                      <div className="mt-3 space-y-2">
                        <div className="flex items-center justify-between">
                          <p className="text-xs text-muted-foreground">
                            Sources ({msg.sources.length})
                            {msg.reranked && (
                              <span className="ml-2 text-foreground">• Re-ranked</span>
                            )}
                          </p>
                          <button
                            onClick={() => toggleSourceExpand(index)}
                            className="text-xs text-muted-foreground hover:text-foreground"
                          >
                            {expandedSources.has(index) ? "Collapse" : "Expand"}
                          </button>
                        </div>
                        {msg.sources
                          .slice(0, expandedSources.has(index) ? undefined : 3)
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
                        {!expandedSources.has(index) && msg.sources.length > 3 && (
                          <button
                            onClick={() => toggleSourceExpand(index)}
                            className="text-xs text-foreground hover:text-muted-foreground"
                          >
                            Show {msg.sources.length - 3} more sources
                          </button>
                        )}
                      </div>
                    )}
                  </div>
                </motion.div>
              ))}
            </AnimatePresence>

            {isLoading && !messages[messages.length - 1]?.isStreaming && (
              <motion.div
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                className="flex gap-4"
              >
                <div className="w-10 h-10 rounded-lg bg-accent/20 flex items-center justify-center">
                  <Loader2 className="w-5 h-5 text-accent animate-spin" />
                </div>
                <div className="flex-1">
                  <div className="inline-block rounded-lg p-4 bg-muted">
                    <div className="flex items-center gap-2">
                      {useAgentic ? (
                        <>
                          <Zap className="w-4 h-4 text-foreground animate-pulse" />
                          <span className="text-sm text-muted-foreground">
                            Deep research in progress...
                          </span>
                        </>
                      ) : (
                        <>
                          <div className="w-2 h-2 rounded-full bg-foreground animate-pulse" />
                          <div className="w-2 h-2 rounded-full bg-foreground animate-pulse delay-100" />
                          <div className="w-2 h-2 rounded-full bg-foreground animate-pulse delay-200" />
                        </>
                      )}
                    </div>
                  </div>
                </div>
              </motion.div>
            )}

            <div ref={messagesEndRef} />
          </div>
        )}
      </div>

      {/* Input */}
      <form onSubmit={handleAsk}>
        <div className="relative group">
          <div className="relative glass rounded-lg p-2 flex items-center gap-3">
            <div className="pl-4">
              <MessageSquare className="w-5 h-5 text-muted-foreground" />
            </div>

            <input
              type="text"
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              placeholder={
                useAgentic
                  ? "Ask a complex question for deep research..."
                  : "Ask a question about your documents..."
              }
              className="flex-1 bg-transparent border-none outline-none text-foreground placeholder:text-muted-foreground py-3"
            />

            <button
              type="submit"
              disabled={isLoading || !question.trim()}
              className={cn(
                "px-6 py-3 rounded-lg font-medium transition-all duration-300",
                "bg-accent text-accent-foreground",
                "hover:bg-accent/90",
                "disabled:opacity-50 disabled:cursor-not-allowed",
                "flex items-center gap-2"
              )}
            >
              {isLoading ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : useAgentic ? (
                <Zap className="w-4 h-4" />
              ) : (
                <Send className="w-4 h-4" />
              )}
              <span>{useAgentic ? "Research" : "Ask"}</span>
            </button>
          </div>
        </div>
      </form>
    </div>
  );
}
