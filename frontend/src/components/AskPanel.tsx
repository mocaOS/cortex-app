"use client";

import { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  MessageSquare,
  Send,
  Loader2,
  Sparkles,
  FileText,
  Bot,
  User,
} from "lucide-react";
import { cn } from "@/lib/utils";

interface Source {
  document_id: string;
  chunk_id: string;
  content: string;
  score: number;
  metadata: {
    filename: string;
    chunk_index: number;
  };
}

interface Message {
  role: "user" | "assistant";
  content: string;
  sources?: Source[];
}

export default function AskPanel() {
  const [question, setQuestion] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [isLoading, setIsLoading] = useState(false);

  const handleAsk = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!question.trim() || isLoading) return;

    const userMessage: Message = { role: "user", content: question };
    setMessages((prev) => [...prev, userMessage]);
    setQuestion("");
    setIsLoading(true);

    try {
      const res = await fetch("/api/ask", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question, top_k: 5 }),
      });

      if (res.ok) {
        const data = await res.json();
        const assistantMessage: Message = {
          role: "assistant",
          content: data.answer,
          sources: data.sources,
        };
        setMessages((prev) => [...prev, assistantMessage]);
      } else {
        throw new Error("Failed to get response");
      }
    } catch (error) {
      const errorMessage: Message = {
        role: "assistant",
        content: "Sorry, I encountered an error processing your question.",
      };
      setMessages((prev) => [...prev, errorMessage]);
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="space-y-6">
      {/* Chat History */}
      <div className="glass rounded-2xl min-h-[400px] max-h-[600px] overflow-y-auto">
        {messages.length === 0 ? (
          <div className="h-[400px] flex flex-col items-center justify-center p-8">
            <div className="w-20 h-20 rounded-2xl bg-gradient-to-br from-coral-500/20 to-pink-500/20 flex items-center justify-center mb-6">
              <Bot className="w-10 h-10 text-coral-400/60" />
            </div>
            <h3 className="text-lg font-medium text-white/70 mb-2">
              Ask Questions
            </h3>
            <p className="text-white/40 text-center max-w-md">
              Ask questions about your documents. I&apos;ll use AI to find
              relevant information and provide accurate answers.
            </p>
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
                      "w-10 h-10 rounded-xl flex items-center justify-center shrink-0",
                      msg.role === "user"
                        ? "bg-ocean-500/20"
                        : "bg-coral-500/20"
                    )}
                  >
                    {msg.role === "user" ? (
                      <User className="w-5 h-5 text-ocean-400" />
                    ) : (
                      <Bot className="w-5 h-5 text-coral-400" />
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
                        "inline-block rounded-2xl p-4",
                        msg.role === "user"
                          ? "bg-ocean-500/20 text-white/90"
                          : "bg-white/5 text-white/80"
                      )}
                    >
                      <p className="text-sm leading-relaxed whitespace-pre-wrap text-left">
                        {msg.content}
                      </p>
                    </div>

                    {/* Sources */}
                    {msg.sources && msg.sources.length > 0 && (
                      <div className="mt-3 space-y-2">
                        <p className="text-xs text-white/40 mb-2">Sources:</p>
                        {msg.sources.slice(0, 3).map((source, idx) => (
                          <div
                            key={idx}
                            className="text-left p-3 rounded-lg bg-white/[0.03] border border-white/5"
                          >
                            <div className="flex items-center gap-2 mb-1">
                              <FileText className="w-3 h-3 text-ocean-400" />
                              <span className="text-xs text-ocean-400">
                                {source.metadata.filename}
                              </span>
                              <span className="text-xs text-white/30">
                                ({(source.score * 100).toFixed(0)}% match)
                              </span>
                            </div>
                            <p className="text-xs text-white/40 line-clamp-2">
                              {source.content}
                            </p>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                </motion.div>
              ))}
            </AnimatePresence>

            {isLoading && (
              <motion.div
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                className="flex gap-4"
              >
                <div className="w-10 h-10 rounded-xl bg-coral-500/20 flex items-center justify-center">
                  <Loader2 className="w-5 h-5 text-coral-400 animate-spin" />
                </div>
                <div className="flex-1">
                  <div className="inline-block rounded-2xl p-4 bg-white/5">
                    <div className="flex items-center gap-2">
                      <div className="w-2 h-2 rounded-full bg-coral-400 animate-pulse" />
                      <div className="w-2 h-2 rounded-full bg-coral-400 animate-pulse delay-100" />
                      <div className="w-2 h-2 rounded-full bg-coral-400 animate-pulse delay-200" />
                    </div>
                  </div>
                </div>
              </motion.div>
            )}
          </div>
        )}
      </div>

      {/* Input */}
      <form onSubmit={handleAsk}>
        <div className="relative group">
          <div className="absolute inset-0 bg-gradient-to-r from-coral-500/20 via-pink-500/20 to-rose-500/20 rounded-2xl blur-xl opacity-0 group-focus-within:opacity-100 transition-opacity duration-500" />

          <div className="relative glass rounded-2xl p-2 flex items-center gap-3">
            <div className="pl-4">
              <MessageSquare className="w-5 h-5 text-white/40" />
            </div>

            <input
              type="text"
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              placeholder="Ask a question about your documents..."
              className="flex-1 bg-transparent border-none outline-none text-white/90 placeholder:text-white/30 py-3"
            />

            <button
              type="submit"
              disabled={isLoading || !question.trim()}
              className={cn(
                "px-6 py-3 rounded-xl font-medium transition-all duration-300",
                "bg-gradient-to-r from-coral-500 to-pink-500",
                "hover:from-coral-400 hover:to-pink-400",
                "disabled:opacity-50 disabled:cursor-not-allowed",
                "flex items-center gap-2"
              )}
            >
              {isLoading ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <Send className="w-4 h-4" />
              )}
              <span>Ask</span>
            </button>
          </div>
        </div>
      </form>
    </div>
  );
}
