"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Loader2, Zap, Gauge } from "lucide-react";
import { api } from "@/lib/api";
import type { ConversationMessage, GraphContext } from "@/types";
import { ChatMessage, AskSettings, AskInput, EmptyChat } from "./ask";

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

// Strip <think>...</think> tags from content (used in fast mode)
function stripThinkingTags(content: string): string {
  // Remove complete <think>...</think> blocks (including multiline)
  return content.replace(/<think>[\s\S]*?<\/think>/gi, "").trim();
}

// =========================================================================
// LocalStorage persistence for Ask AI settings
// =========================================================================
const ASK_SETTINGS_KEY = "moca-ask-settings";

interface PersistedAskSettings {
  useStreaming: boolean;
  useAgentic: boolean;
  useFastSearch: boolean;
  selectedCollectionId?: string;
  selectedCollectionName?: string;
}

const DEFAULT_SETTINGS: PersistedAskSettings = {
  useStreaming: true,
  useAgentic: false,
  useFastSearch: false,
};

export type AskMode = "research" | "chat";

interface AskPanelProps {
  initialMode?: AskMode;
}

function loadSettings(): PersistedAskSettings {
  if (typeof window === "undefined") return DEFAULT_SETTINGS;
  try {
    const stored = localStorage.getItem(ASK_SETTINGS_KEY);
    if (!stored) return DEFAULT_SETTINGS;
    const parsed = JSON.parse(stored) as Partial<PersistedAskSettings>;
    return { ...DEFAULT_SETTINGS, ...parsed };
  } catch {
    return DEFAULT_SETTINGS;
  }
}

function saveSettings(settings: PersistedAskSettings): void {
  if (typeof window === "undefined") return;
  try {
    localStorage.setItem(ASK_SETTINGS_KEY, JSON.stringify(settings));
  } catch {
    // Silently ignore storage errors (quota exceeded, etc.)
  }
}

export default function AskPanel({ initialMode }: AskPanelProps) {
  const [question, setQuestion] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [expandedSources, setExpandedSources] = useState<Set<number>>(new Set());
  const messagesEndRef = useRef<HTMLDivElement>(null);

  // Determine initial mode settings
  // "research" mode = agentic ON, fast search OFF
  // "chat" mode = agentic OFF, fast search ON (for quick responses)
  const getInitialSettings = () => {
    const stored = loadSettings();
    if (initialMode === "research") {
      return {
        ...stored,
        useAgentic: true,
        useFastSearch: false,
      };
    } else if (initialMode === "chat") {
      return {
        ...stored,
        useAgentic: false,
        useFastSearch: false, // Standard chat, not fast search
      };
    }
    return stored;
  };

  // Initialize settings from localStorage, overridden by initialMode if provided
  const [useStreaming, setUseStreaming] = useState(() => getInitialSettings().useStreaming);
  const [useAgentic, setUseAgentic] = useState(() => getInitialSettings().useAgentic);
  const [useFastSearch, setUseFastSearch] = useState(() => getInitialSettings().useFastSearch);
  const [selectedCollectionId, setSelectedCollectionId] = useState<string | undefined>(
    () => getInitialSettings().selectedCollectionId
  );
  const [selectedCollectionName, setSelectedCollectionName] = useState<string | undefined>(
    () => getInitialSettings().selectedCollectionName
  );

  // Persist settings to localStorage whenever they change
  useEffect(() => {
    saveSettings({
      useStreaming,
      useAgentic,
      useFastSearch,
      selectedCollectionId,
      selectedCollectionName,
    });
  }, [useStreaming, useAgentic, useFastSearch, selectedCollectionId, selectedCollectionName]);

  // Resolve collection name on mount if a collection was persisted
  useEffect(() => {
    if (selectedCollectionId) {
      api.getCollection(selectedCollectionId).then((col) => {
        setSelectedCollectionName(col.name);
      }).catch(() => {
        // Collection may have been deleted - clear the stale selection
        setSelectedCollectionId(undefined);
        setSelectedCollectionName(undefined);
      });
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleCollectionChange = useCallback((collectionId: string | undefined) => {
    setSelectedCollectionId(collectionId);
    if (!collectionId) {
      setSelectedCollectionName(undefined);
    } else {
      // Fetch collection name for display
      api.getCollection(collectionId).then((col) => {
        setSelectedCollectionName(col.name);
      }).catch(() => {
        setSelectedCollectionName(collectionId);
      });
    }
  }, []);

  // When fast search is enabled, disable agentic mode
  const handleFastSearchChange = (value: boolean) => {
    setUseFastSearch(value);
    if (value) {
      setUseAgentic(false);
    }
  };

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
        thinkingSteps: useAgentic && !useFastSearch ? [] : undefined,
        subQuestions: useAgentic && !useFastSearch ? [] : undefined,
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
          useReranking: !useFastSearch,
          useGraph: !useFastSearch,
          useAgentic: useAgentic && !useFastSearch,
          useFastSearch,
          collectionId: selectedCollectionId,
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
            // In fast mode, strip <think>...</think> tags from the response
            const displayContent = useFastSearch ? stripThinkingTags(content) : content;
            setMessages((prev) => {
              const updated = [...prev];
              const lastIdx = updated.length - 1;
              updated[lastIdx] = { ...updated[lastIdx], content: displayContent };
              return updated;
            });
          }
          if (event.done) {
            // In fast mode, strip <think>...</think> tags from the final response
            const finalContent = useFastSearch ? stripThinkingTags(content) : content;
            setMessages((prev) => {
              const updated = [...prev];
              const lastIdx = updated.length - 1;
              updated[lastIdx] = {
                ...updated[lastIdx],
                content: finalContent,
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
          useReranking: !useFastSearch,
          useAgentic: useAgentic && !useFastSearch,
          useGraph: !useFastSearch,
          useFastSearch,
          collectionId: selectedCollectionId,
        });

        // In fast mode, strip <think>...</think> tags from the response
        const answerContent = useFastSearch ? stripThinkingTags(data.answer) : data.answer;

        const assistantMessage: Message = {
          role: "assistant",
          content: answerContent,
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
      <AskSettings
        showSettings={showSettings}
        onToggleSettings={() => setShowSettings(!showSettings)}
        hasMessages={messages.length > 0}
        onClearConversation={clearConversation}
        useStreaming={useStreaming}
        onStreamingChange={setUseStreaming}
        useAgentic={useAgentic}
        onAgenticChange={setUseAgentic}
        useFastSearch={useFastSearch}
        onFastSearchChange={handleFastSearchChange}
        selectedCollectionId={selectedCollectionId}
        onCollectionChange={handleCollectionChange}
        selectedCollectionName={selectedCollectionName}
      />

      {/* Chat History */}
      <div className="glass rounded-lg min-h-[400px] max-h-[600px] overflow-y-auto">
        {messages.length === 0 ? (
          <EmptyChat />
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
                      {useFastSearch ? (
                        <>
                          <Gauge className="w-4 h-4 text-amber-500 animate-pulse" />
                          <span className="text-sm text-muted-foreground">
                            Fast search...
                          </span>
                        </>
                      ) : useAgentic ? (
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
      <AskInput
        question={question}
        onQuestionChange={setQuestion}
        onSubmit={handleAsk}
        isLoading={isLoading}
        useAgentic={useAgentic}
      />
    </div>
  );
}
