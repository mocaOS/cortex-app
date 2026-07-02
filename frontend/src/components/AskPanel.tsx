"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Loader2, Zap, Settings2, FolderOpen, Layers, RotateCcw, ArrowUp, FlaskConical, Square } from "lucide-react";
import { api } from "@/lib/api";
import type { AskMessage, AskSource, ConversationMessage, GraphContext } from "@/types";
import { ChatMessage, EmptyChat } from "./ask";
import CollectionSelector from "./CollectionSelector";
import { cn } from "@/lib/utils";

// Stable client-side message identity (React key + streaming update target).
function generateMessageId(): string {
  try {
    if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
      return crypto.randomUUID();
    }
  } catch {
    // Fall through to the non-crypto fallback.
  }
  return `msg-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
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

// =========================================================================
// SessionStorage persistence for the conversation itself (messages + the
// backend conversation-memory blob). Survives reloads within the tab; a new
// tab starts a fresh conversation.
// =========================================================================
const ASK_CONVERSATION_KEY = "cortex-ask-conversation";
// Cap the persisted payload; sessionStorage quota is ~5MB and long research
// answers with sources add up fast. Oldest messages are dropped first.
const MAX_PERSISTED_CHARS = 2 * 1024 * 1024;

interface PersistedConversation {
  messages: AskMessage[];
  memory?: unknown;
}

function loadConversation(): PersistedConversation | null {
  if (typeof window === "undefined") return null;
  try {
    const stored = sessionStorage.getItem(ASK_CONVERSATION_KEY);
    if (!stored) return null;
    const parsed = JSON.parse(stored) as PersistedConversation;
    if (!Array.isArray(parsed.messages)) return null;
    return {
      // Backfill ids for payloads persisted before ids existed.
      messages: parsed.messages.map((m) => ({
        ...m,
        id: m.id || generateMessageId(),
      })),
      memory: parsed.memory,
    };
  } catch {
    return null;
  }
}

function saveConversation(messages: AskMessage[], memory: unknown): void {
  if (typeof window === "undefined") return;
  try {
    // Never delete on empty here — the explicit Clear action removes the key.
    // (Guards against StrictMode's double effect pass wiping a stored
    // conversation with the initial empty state before rehydration lands.)
    if (messages.length === 0) return;
    // Strip volatile fields — a rehydrated message is never mid-stream.
    let toStore = messages.map((m) => {
      const copy = { ...m };
      delete copy.isStreaming;
      delete copy.statusMessage;
      return copy;
    });
    let payload = JSON.stringify({ messages: toStore, memory });
    while (payload.length > MAX_PERSISTED_CHARS && toStore.length > 1) {
      toStore = toStore.slice(1);
      payload = JSON.stringify({ messages: toStore, memory });
    }
    if (payload.length > MAX_PERSISTED_CHARS) {
      sessionStorage.removeItem(ASK_CONVERSATION_KEY);
      return;
    }
    sessionStorage.setItem(ASK_CONVERSATION_KEY, payload);
  } catch {
    // Silently ignore storage errors (quota, private mode, ...)
  }
}

export default function AskPanel({ initialMode = "chat" }: AskPanelProps) {
  const [question, setQuestion] = useState("");
  const [messages, setMessages] = useState<AskMessage[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [expandedSources, setExpandedSources] = useState<Set<string>>(new Set());
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const settingsRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  // Holds the in-flight stream so the user can Stop it (and so we can abort on
  // unmount — otherwise navigating away leaves the backend generating).
  const abortRef = useRef<AbortController | null>(null);
  // Latest conversation-memory blob from the backend (`memory_update` events);
  // echoed back as `conversation_memory` on the next request.
  const memoryRef = useRef<unknown>(undefined);
  // Mirror of `messages` so stream handlers can persist without stale closures.
  const messagesRef = useRef<AskMessage[]>([]);
  // Blocks persistence until the mount-time rehydrate has run, so the initial
  // empty state can't clobber a stored conversation.
  const hydratedRef = useRef(false);

  // Abort any in-flight stream when the panel unmounts.
  useEffect(() => {
    return () => abortRef.current?.abort();
  }, []);

  // Persist conversation + memory blob on every change. Declared BEFORE the
  // rehydrate effect so the first run (empty messages, not yet hydrated) is a
  // no-op instead of wiping the stored conversation.
  useEffect(() => {
    messagesRef.current = messages;
    if (!hydratedRef.current) return;
    saveConversation(messages, memoryRef.current);
  }, [messages]);

  // Rehydrate the persisted conversation on mount.
  useEffect(() => {
    const persisted = loadConversation();
    if (persisted) {
      memoryRef.current = persisted.memory;
      if (persisted.messages.length > 0) {
        setMessages(persisted.messages);
      }
    }
    hydratedRef.current = true;
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
    // While tokens stream in, "smooth" scrolls pile up and lag badly — jump
    // instantly instead and save the smooth glide for completed messages.
    const streaming = messages.length > 0 && messages[messages.length - 1].isStreaming;
    messagesEndRef.current?.scrollIntoView({ behavior: streaming ? "auto" : "smooth" });
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

    const userMessage: AskMessage = {
      id: generateMessageId(),
      role: "user",
      content: question,
    };
    setMessages((prev) => [...prev, userMessage]);
    setQuestion("");
    // Collapse the auto-grown composer back to one line and keep the caret
    // there so the user can type a follow-up immediately.
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
      textareaRef.current.focus();
    }
    setIsLoading(true);

    const controller = new AbortController();
    abortRef.current = controller;

    const conversationHistory = getConversationHistory();

    if (useStreaming) {
      // Streaming mode
      const assistantId = generateMessageId();
      const assistantMessage: AskMessage = {
        id: assistantId,
        role: "assistant",
        content: "",
        isStreaming: true,
        thinkingSteps: useAgentic ? [] : undefined,
        subQuestions: useAgentic ? [] : undefined,
      };
      setMessages((prev) => [...prev, assistantMessage]);

      // Always patch the assistant message by its id — "last index" breaks the
      // moment the list changes underneath (e.g. Clear during streaming).
      const updateAssistant = (patch: Partial<AskMessage>) => {
        setMessages((prev) =>
          prev.map((m) => (m.id === assistantId ? { ...m, ...patch } : m))
        );
      };

      let sources: AskSource[] = [];
      let graphContext: GraphContext | undefined;
      let content = "";
      let thinkingSteps: string[] = [];
      let subQuestions: string[] = [];

      // Token deltas arrive far faster than React should re-render. Accumulate
      // them and flush at most once per animation frame.
      let rafId: number | null = null;
      const flushContent = () => {
        rafId = null;
        const snapshot = content;
        updateAssistant({ content: snapshot });
      };
      const scheduleFlush = () => {
        if (rafId !== null) return;
        if (typeof requestAnimationFrame === "function") {
          rafId = requestAnimationFrame(flushContent);
        } else {
          flushContent();
        }
      };
      const cancelFlush = () => {
        if (rafId !== null) {
          cancelAnimationFrame(rafId);
          rafId = null;
        }
      };

      try {
        let finalized = false;

        for await (const event of api.askStream(question, {
          conversationHistory,
          conversationMemory: memoryRef.current,
          useReranking: true,
          useGraph: true,
          useAgentic,
          useFastSearch: false,
          collectionId: selectedCollectionId,
          signal: controller.signal,
        })) {
          if (event.thinking) {
            thinkingSteps = [...thinkingSteps, event.thinking as string];
            updateAssistant({ thinkingSteps });
          }
          if (event.sub_questions) {
            subQuestions = event.sub_questions as string[];
            updateAssistant({ subQuestions });
          }
          if (event.retrieval) {
            thinkingSteps = [...thinkingSteps, event.retrieval as string];
            updateAssistant({ thinkingSteps });
          }
          if (event.status?.message) {
            updateAssistant({ statusMessage: event.status.message });
          }
          if (event.skill_tool) {
            const prefix = event.is_error ? "[SkillError] " : "[Skill] ";
            thinkingSteps = [...thinkingSteps, `${prefix}${event.skill_tool as string}`];
            updateAssistant({ thinkingSteps });
          }
          if (event.sources) {
            // Commit immediately so [src_N] citations resolve while the answer
            // is still streaming (and survive a Stop).
            sources = event.sources;
            updateAssistant({ sources });
          }
          if (event.graph_context) {
            graphContext = event.graph_context;
            updateAssistant({ graphContext });
          }
          if (event.content) {
            content += event.content;
            scheduleFlush();
          }
          if (event.memory_update !== undefined) {
            memoryRef.current = event.memory_update;
            // May arrive after `done` (i.e. after the last setMessages), so
            // persist explicitly instead of relying on the messages effect.
            if (hydratedRef.current) {
              saveConversation(messagesRef.current, memoryRef.current);
            }
          }
          if (event.done) {
            cancelFlush();
            updateAssistant({
              content,
              sources,
              graphContext,
              thinkingSteps: thinkingSteps.length > 0 ? thinkingSteps : undefined,
              subQuestions: subQuestions.length > 0 ? subQuestions : undefined,
              isStreaming: false,
              reranked: true,
            });
            finalized = true;
            // Do NOT break: the backend may still send `memory_update` after
            // `done` — keep consuming until the stream actually ends.
          }
          if (event.error) {
            cancelFlush();
            updateAssistant({
              content: content
                ? `${content}\n\n_${event.error}_`
                : String(event.error),
              isStreaming: false,
            });
            finalized = true;
            break;
          }
        }

        cancelFlush();
        // The stream can end without a terminal `done`/`error` frame: a dropped
        // connection, a proxy idle-timeout, or a graceful server restart (routine
        // in per-tenant container deploys). Finalize the message so it doesn't
        // blink with a streaming cursor forever.
        if (!finalized) {
          updateAssistant({
            content:
              content ||
              "The connection was interrupted before the answer finished. Please try again.",
            sources,
            graphContext,
            thinkingSteps: thinkingSteps.length > 0 ? thinkingSteps : undefined,
            subQuestions: subQuestions.length > 0 ? subQuestions : undefined,
            isStreaming: false,
          });
        }
      } catch (error) {
        cancelFlush();
        // User pressed Stop (or navigated away) — keep whatever streamed so far
        // (including sources, so citations stay clickable), just stop the
        // cursor. Not an error to report.
        const aborted = error instanceof DOMException && error.name === "AbortError";
        setMessages((prev) =>
          prev.map((m) =>
            m.id === assistantId
              ? {
                  ...m,
                  content: aborted
                    ? content || m.content || "_Stopped._"
                    : error instanceof Error && error.message
                      ? error.message
                      : "Sorry, I encountered an error processing your question.",
                  sources: sources.length > 0 ? sources : m.sources,
                  graphContext: graphContext ?? m.graphContext,
                  thinkingSteps:
                    thinkingSteps.length > 0 ? thinkingSteps : m.thinkingSteps,
                  subQuestions:
                    subQuestions.length > 0 ? subQuestions : m.subQuestions,
                  isStreaming: false,
                }
              : m
          )
        );
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

        const assistantMessage: AskMessage = {
          id: generateMessageId(),
          role: "assistant",
          content: data.answer,
          sources: data.sources,
          graphContext: data.graph_context,
          reasoningSteps: data.reasoning_steps,
          reranked: data.reranked,
        };
        setMessages((prev) => [...prev, assistantMessage]);
      } catch (error) {
        const errorMessage: AskMessage = {
          id: generateMessageId(),
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

  const clearConversation = useCallback(() => {
    // Stop any in-flight stream first — its updates target message ids that
    // are about to disappear, so they become harmless no-ops.
    abortRef.current?.abort();
    setMessages([]);
    setExpandedSources(new Set());
    memoryRef.current = undefined;
    try {
      sessionStorage.removeItem(ASK_CONVERSATION_KEY);
    } catch {
      // Silently ignore storage errors
    }
  }, []);

  const toggleSourceExpand = useCallback((id: string) => {
    setExpandedSources((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  }, []);

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
                  key={msg.id}
                  message={msg}
                  index={index}
                  isSourceExpanded={expandedSources.has(msg.id)}
                  onToggleSourceExpand={toggleSourceExpand}
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
            ref={textareaRef}
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
