"use client";

import { useState, useRef, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Loader2, Zap } from "lucide-react";
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
      <AskSettings
        showSettings={showSettings}
        onToggleSettings={() => setShowSettings(!showSettings)}
        hasMessages={messages.length > 0}
        onClearConversation={clearConversation}
        useStreaming={useStreaming}
        onStreamingChange={setUseStreaming}
        useAgentic={useAgentic}
        onAgenticChange={setUseAgentic}
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
