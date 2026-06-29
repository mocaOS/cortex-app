"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Loader2, Zap, Settings2, FolderOpen, Layers, RotateCcw, ArrowUp, FlaskConical, Square } from "lucide-react";
import { api } from "@/lib/api";
import type { ConversationMessage, GraphContext } from "@/types";
import { ChatMessage, EmptyChat } from "./ask";
import CollectionSelector from "./CollectionSelector";
import { cn } from "@/lib/utils";

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
  /** Latest backend pipeline stage label (from the `status` SSE event). */
  statusMessage?: string;
}

// =========================================================================
// LocalStorage persistence for collection setting only
// =========================================================================
const ASK_SETTINGS_KEY = "cortex-ask-collection";

interface PersistedSettings {
  selectedCollectionId?: string;
  selectedCollectionName?: string;
  useStreaming?: boolean;
}

export type AskMode = "research" | "chat";

interface AskPanelProps {
  initialMode?: AskMode;
}

function loadSettings(): PersistedSettings {
  if (typeof window === "undefined") return {};
  try {
    const stored = localStorage.getItem(ASK_SETTINGS_KEY);
    if (!stored) return {};
    return JSON.parse(stored) as PersistedSettings;
  } catch {
    return {};
  }
}

function saveSettings(settings: PersistedSettings): void {
  if (typeof window === "undefined") return;
  try {
    localStorage.setItem(ASK_SETTINGS_KEY, JSON.stringify(settings));
  } catch {
    // Silently ignore storage errors
  }
}

export default function AskPanel({ initialMode = "chat" }: AskPanelProps) {
  const [question, setQuestion] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [expandedSources, setExpandedSources] = useState<Set<number>>(new Set());
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const settingsRef = useRef<HTMLDivElement>(null);
  // Holds the in-flight stream so the user can Stop it (and so we can abort on
  // unmount — otherwise navigating away leaves the backend generating).
  const abortRef = useRef<AbortController | null>(null);

  // Abort any in-flight stream when the panel unmounts.
  useEffect(() => {
    return () => abortRef.current?.abort();
  }, []);

  // Chat is the default surface; Deep Research is an in-session toggle (flask
  // icon) that can be flipped at any time mid-conversation. Initialized from the
  // entry point so the legacy ?tab=research deep-link still opens in research.
  const [useAgentic, setUseAgentic] = useState(initialMode === "research");

  // Persisted settings
  const [selectedCollectionId, setSelectedCollectionId] = useState<string | undefined>(
    () => loadSettings().selectedCollectionId
  );
  const [selectedCollectionName, setSelectedCollectionName] = useState<string | undefined>(
    () => loadSettings().selectedCollectionName
  );
  const [useStreaming, setUseStreaming] = useState<boolean>(
    () => loadSettings().useStreaming ?? true
  );

  // Persist settings
  useEffect(() => {
    saveSettings({ selectedCollectionId, selectedCollectionName, useStreaming });
  }, [selectedCollectionId, selectedCollectionName, useStreaming]);

  // Resolve collection name on mount
  useEffect(() => {
    if (selectedCollectionId) {
      api.getCollection(selectedCollectionId).then((col) => {
        setSelectedCollectionName(col.name);
      }).catch(() => {
        setSelectedCollectionId(undefined);
        setSelectedCollectionName(undefined);
      });
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Close settings when clicking outside
  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (settingsRef.current && !settingsRef.current.contains(event.target as Node)) {
        setShowSettings(false);
      }
    }
    if (showSettings) {
      document.addEventListener("mousedown", handleClickOutside);
      return () => document.removeEventListener("mousedown", handleClickOutside);
    }
  }, [showSettings]);

  const handleCollectionChange = useCallback((collectionId: string | undefined) => {
    setSelectedCollectionId(collectionId);
    if (!collectionId) {
      setSelectedCollectionName(undefined);
    } else {
      api.getCollection(collectionId).then((col) => {
        setSelectedCollectionName(col.name);
      }).catch(() => {
        setSelectedCollectionName(collectionId);
      });
    }
  }, []);

  useEffect(() => {
    // Only auto-scroll if the user is already near the bottom. Otherwise a user
    // who scrolled up to read sources/earlier answers gets yanked back down on
    // every streamed chunk.
    const container = scrollContainerRef.current;
    if (container) {
      const distanceFromBottom =
        container.scrollHeight - container.scrollTop - container.clientHeight;
      if (distanceFromBottom > 120) return;
    }
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

    const controller = new AbortController();
    abortRef.current = controller;

    const conversationHistory = getConversationHistory();

    if (useStreaming) {
      // Streaming mode
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
        let finalized = false;

        for await (const event of api.askStream(question, {
          conversationHistory,
          useReranking: true,
          useGraph: true,
          useAgentic,
          useFastSearch: false,
          collectionId: selectedCollectionId,
          signal: controller.signal,
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
          if (event.status?.message) {
            const statusMessage = event.status.message;
            setMessages((prev) => {
              const updated = [...prev];
              const lastIdx = updated.length - 1;
              updated[lastIdx] = { ...updated[lastIdx], statusMessage };
              return updated;
            });
          }
          if (event.skill_tool) {
            const prefix = event.is_error ? "[SkillError] " : "[Skill] ";
            thinkingSteps = [...thinkingSteps, `${prefix}${event.skill_tool as string}`];
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
            finalized = true;
          }
          if (event.error) {
            setMessages((prev) => {
              const updated = [...prev];
              const lastIdx = updated.length - 1;
              updated[lastIdx] = {
                ...updated[lastIdx],
                content: content
                  ? `${content}\n\n_${event.error}_`
                  : String(event.error),
                isStreaming: false,
              };
              return updated;
            });
            finalized = true;
            break;
          }
        }

        // The stream can end without a terminal `done`/`error` frame: a dropped
        // connection, a proxy idle-timeout, or a graceful server restart (routine
        // in per-tenant container deploys). Finalize the message so it doesn't
        // blink with a streaming cursor forever.
        if (!finalized) {
          setMessages((prev) => {
            const updated = [...prev];
            const lastIdx = updated.length - 1;
            updated[lastIdx] = {
              ...updated[lastIdx],
              content:
                content ||
                "The connection was interrupted before the answer finished. Please try again.",
              sources,
              graphContext,
              thinkingSteps: thinkingSteps.length > 0 ? thinkingSteps : undefined,
              subQuestions: subQuestions.length > 0 ? subQuestions : undefined,
              isStreaming: false,
            };
            return updated;
          });
        }
      } catch (error) {
        // User pressed Stop (or navigated away) — keep whatever streamed so far,
        // just stop the cursor. Not an error to report.
        const aborted = error instanceof DOMException && error.name === "AbortError";
        setMessages((prev) => {
          const updated = [...prev];
          const lastIdx = updated.length - 1;
          updated[lastIdx] = {
            ...updated[lastIdx],
            content: aborted
              ? updated[lastIdx].content || "_Stopped._"
              : error instanceof Error && error.message
                ? error.message
                : "Sorry, I encountered an error processing your question.",
            isStreaming: false,
          };
          return updated;
        });
      }
    } else {
      // Non-streaming mode
      try {
        const data = await api.ask(question, {
          conversationHistory,
          useReranking: true,
          useAgentic,
          useGraph: true,
          useFastSearch: false,
          collectionId: selectedCollectionId,
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
          content:
            error instanceof Error && error.message
              ? error.message
              : "Sorry, I encountered an error processing your question.",
        };
        setMessages((prev) => [...prev, errorMessage]);
      }
    }

    abortRef.current = null;
    setIsLoading(false);
  };

  const handleStop = () => {
    abortRef.current?.abort();
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

  const hasInput = question.trim().length > 0;

  return (
    <div className="flex flex-col h-[calc(100vh-340px)] min-h-[300px]">
      {/* Chat History */}
      <div ref={scrollContainerRef} className="glass rounded-lg flex-1 overflow-y-auto">
        {messages.length === 0 ? (
          <EmptyChat mode={useAgentic ? "research" : "chat"} />
        ) : (
          <div className="p-6 space-y-6">
            <AnimatePresence initial={false}>
              {messages.map((msg, index) => (
                <ChatMessage
                  key={index}
                  message={msg}
                  index={index}
                  isSourceExpanded={expandedSources.has(index)}
                  onToggleSourceExpand={() => toggleSourceExpand(index)}
                />
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
      <form onSubmit={handleAsk} className="mt-3 shrink-0">
        <div className="relative glass rounded-lg p-2 flex items-end gap-2">
          <textarea
            value={question}
            onChange={(e) => {
              setQuestion(e.target.value);
              // Auto-grow up to a few lines, then scroll inside the field.
              e.target.style.height = "auto";
              e.target.style.height = `${Math.min(e.target.scrollHeight, 160)}px`;
            }}
            onKeyDown={(e) => {
              // Enter sends; Shift+Enter inserts a newline.
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                handleAsk(e as unknown as React.FormEvent);
              }
            }}
            rows={1}
            placeholder={
              useAgentic
                ? "Ask a complex question for deep research..."
                : "Ask anything... (Shift+Enter for a new line)"
            }
            className="flex-1 bg-transparent border-none outline-none resize-none text-foreground placeholder:text-muted-foreground py-2 px-3 max-h-40 leading-relaxed"
          />

          <div className="flex items-center gap-1.5">
            {/* Deep Research toggle (Erlenmeyer flask) — flip between Chat and
                Deep Research at any time during the session. */}
            <button
              type="button"
              onClick={() => setUseAgentic((v) => !v)}
              aria-pressed={useAgentic}
              className={cn(
                "flex-shrink-0 h-8 px-2.5 rounded-lg flex items-center gap-1.5 transition-colors text-sm font-medium",
                useAgentic
                  ? "bg-accent text-accent-foreground"
                  : "text-muted-foreground hover:text-foreground hover:bg-muted"
              )}
              title={
                useAgentic
                  ? "Deep Research is ON — multi-step research (click for Chat)"
                  : "Chat mode — click to enable Deep Research (multi-step depth)"
              }
            >
              <FlaskConical className="w-4 h-4" />
              <span className="hidden sm:inline">Deep Research</span>
            </button>

            {/* Clear button - only show when there are messages */}
            {messages.length > 0 && (
              <button
                type="button"
                onClick={clearConversation}
                className="flex-shrink-0 w-8 h-8 rounded-lg flex items-center justify-center transition-colors text-muted-foreground hover:text-foreground hover:bg-muted"
                title="Clear conversation"
              >
                <RotateCcw className="w-4 h-4" />
              </button>
            )}

            {/* Settings button */}
            <div className="relative" ref={settingsRef}>
              <button
                type="button"
                onClick={() => setShowSettings(!showSettings)}
                className={cn(
                  "flex-shrink-0 w-8 h-8 rounded-lg flex items-center justify-center transition-colors",
                  showSettings
                    ? "bg-accent text-accent-foreground"
                    : "text-muted-foreground hover:text-foreground hover:bg-muted"
                )}
                title="Settings"
              >
                <Settings2 className="w-4 h-4" />
              </button>

              {/* Settings dropdown */}
              <AnimatePresence>
                {showSettings && (
                  <motion.div
                    initial={{ opacity: 0, y: 8, scale: 0.95 }}
                    animate={{ opacity: 1, y: 0, scale: 1 }}
                    exit={{ opacity: 0, y: 8, scale: 0.95 }}
                    transition={{ duration: 0.15 }}
                    className="absolute bottom-full right-0 mb-2 w-72 bg-popover border border-border rounded-lg shadow-lg p-4 z-50"
                  >
                    <div className="space-y-4">
                      {/* Stream responses toggle */}
                      <div className="flex items-center justify-between">
                        <label className="text-sm text-muted-foreground">Stream responses</label>
                        <button
                          type="button"
                          onClick={() => setUseStreaming(!useStreaming)}
                          className={cn(
                            "relative w-11 h-6 rounded-full transition-colors",
                            useStreaming ? "bg-accent" : "bg-muted"
                          )}
                        >
                          <span
                            className={cn(
                              "absolute top-1 left-1 w-4 h-4 rounded-full bg-white shadow transition-transform",
                              useStreaming && "translate-x-5"
                            )}
                          />
                        </button>
                      </div>

                      <div className="border-t border-border pt-4">
                        <label className="text-sm text-foreground flex items-center gap-1.5 mb-2">
                          <FolderOpen className="w-3.5 h-3.5 text-blue-500" />
                          Collection Scope
                        </label>
                        <CollectionSelector
                          value={selectedCollectionId}
                          onChange={handleCollectionChange}
                          allowCreate={false}
                          showAllOption={true}
                          placeholder="All Collections"
                        />
                        <p className="text-xs text-muted-foreground mt-1">
                          Limit search to a specific collection
                        </p>
                      </div>
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>
            </div>

            {/* Send / Stop button — turns into Stop while a response streams */}
            {isLoading ? (
              <button
                type="button"
                onClick={handleStop}
                title="Stop generating"
                aria-label="Stop generating"
                className="flex-shrink-0 w-8 h-8 rounded-lg flex items-center justify-center transition-colors bg-accent text-accent-foreground hover:opacity-90"
              >
                <Square className="w-3.5 h-3.5" fill="currentColor" strokeWidth={0} />
              </button>
            ) : (
              <button
                type="submit"
                disabled={!hasInput}
                className={cn(
                  "flex-shrink-0 w-8 h-8 rounded-lg flex items-center justify-center transition-colors",
                  hasInput
                    ? "bg-accent text-accent-foreground"
                    : "bg-border text-muted-foreground opacity-30"
                )}
              >
                <ArrowUp className="w-4 h-4" strokeWidth={2.5} />
              </button>
            )}
          </div>
        </div>

        {/* Mode + active collection indicator */}
        <div className="mt-2 flex items-center gap-1.5 text-xs text-muted-foreground">
          <span className="flex items-center gap-1">
            {useAgentic ? (
              <FlaskConical className="w-3 h-3 text-accent" />
            ) : (
              <Zap className="w-3 h-3" />
            )}
            <span className={useAgentic ? "text-accent" : undefined}>
              {useAgentic ? "Deep Research" : "Chat"}
            </span>
          </span>
          <span className="opacity-40">·</span>
          {selectedCollectionId && selectedCollectionName ? (
            <>
              <FolderOpen className="w-3 h-3 text-blue-500" />
              <span>Searching in: <span className="text-blue-500">{selectedCollectionName}</span></span>
            </>
          ) : (
            <>
              <Layers className="w-3 h-3" />
              <span>Searching across all collections</span>
            </>
          )}
        </div>
      </form>
    </div>
  );
}
