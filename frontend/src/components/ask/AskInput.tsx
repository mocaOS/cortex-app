"use client";

import {
  MessageSquare,
  Send,
  Loader2,
  Zap,
} from "lucide-react";
import { cn } from "@/lib/utils";

interface AskInputProps {
  question: string;
  onQuestionChange: (value: string) => void;
  onSubmit: (e: React.FormEvent) => void;
  isLoading: boolean;
  useAgentic: boolean;
}

export default function AskInput({
  question,
  onQuestionChange,
  onSubmit,
  isLoading,
  useAgentic,
}: AskInputProps) {
  return (
    <form onSubmit={onSubmit}>
      <div className="relative group">
        <div className="relative glass rounded-lg p-2 flex items-center gap-3">
          <div className="pl-4">
            <MessageSquare className="w-5 h-5 text-muted-foreground" />
          </div>

          <input
            type="text"
            value={question}
            onChange={(e) => onQuestionChange(e.target.value)}
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
  );
}
