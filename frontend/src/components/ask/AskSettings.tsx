"use client";

import { motion, AnimatePresence } from "framer-motion";
import {
  Settings2,
  RotateCcw,
  ChevronDown,
  ChevronUp,
  Search,
  Layers,
  Zap,
  Gauge,
  Radio,
} from "lucide-react";
import { cn } from "@/lib/utils";

interface AskSettingsProps {
  showSettings: boolean;
  onToggleSettings: () => void;
  hasMessages: boolean;
  onClearConversation: () => void;
  useStreaming: boolean;
  onStreamingChange: (value: boolean) => void;
  useAgentic: boolean;
  onAgenticChange: (value: boolean) => void;
  useFastSearch: boolean;
  onFastSearchChange: (value: boolean) => void;
}

export default function AskSettings({
  showSettings,
  onToggleSettings,
  hasMessages,
  onClearConversation,
  useStreaming,
  onStreamingChange,
  useAgentic,
  onAgenticChange,
  useFastSearch,
  onFastSearchChange,
}: AskSettingsProps) {
  return (
    <div className="glass rounded-lg p-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-4">
          <button
            onClick={onToggleSettings}
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

          {hasMessages && (
            <button
              onClick={onClearConversation}
              className="flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
            >
              <RotateCcw className="w-4 h-4" />
              Clear
            </button>
          )}
        </div>

        <div className="flex items-center gap-3">
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            {useFastSearch ? (
              <span className="flex items-center gap-1 text-amber-500">
                <Gauge className="w-3 h-3" />
                Fast Mode
              </span>
            ) : (
              <>
                <span className="flex items-center gap-1">
                  <Search className="w-3 h-3" />
                  Hybrid
                </span>
                <span className="flex items-center gap-1">
                  <Layers className="w-3 h-3" />
                  Reranking
                </span>
              </>
            )}
            {useAgentic && !useFastSearch && (
              <span className="flex items-center gap-1 text-foreground">
                <Zap className="w-3 h-3" />
                Deep Research
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
            <div className="pt-4 mt-4 border-t border-border grid grid-cols-3 gap-4">
              <label className="flex items-center gap-3 cursor-pointer">
                <input
                  type="checkbox"
                  checked={useStreaming}
                  onChange={(e) => onStreamingChange(e.target.checked)}
                  className="w-4 h-4 rounded border-border bg-muted accent-accent"
                />
                <div>
                  <span className="text-sm text-foreground flex items-center gap-1.5">
                    <Radio className="w-3.5 h-3.5 text-green-500" />
                    Streaming Responses
                  </span>
                  <p className="text-xs text-muted-foreground">
                    See answers as they&apos;re generated
                  </p>
                </div>
              </label>

              <label className={cn(
                "flex items-center gap-3 cursor-pointer",
                useFastSearch && "opacity-50"
              )}>
                <input
                  type="checkbox"
                  checked={useAgentic}
                  onChange={(e) => onAgenticChange(e.target.checked)}
                  disabled={useFastSearch}
                  className="w-4 h-4 rounded border-border bg-muted accent-accent disabled:opacity-50"
                />
                <div>
                  <span className="text-sm text-foreground flex items-center gap-1.5">
                    <Zap className="w-3.5 h-3.5 text-purple-500" />
                    Deep Research Mode
                  </span>
                  <p className="text-xs text-muted-foreground">
                    Multi-step reasoning for complex questions
                  </p>
                </div>
              </label>

              <label className="flex items-center gap-3 cursor-pointer">
                <input
                  type="checkbox"
                  checked={useFastSearch}
                  onChange={(e) => onFastSearchChange(e.target.checked)}
                  className="w-4 h-4 rounded border-border bg-muted accent-accent"
                />
                <div>
                  <span className="text-sm text-foreground flex items-center gap-1.5">
                    <Gauge className="w-3.5 h-3.5 text-amber-500" />
                    Fast Mode
                  </span>
                  <p className="text-xs text-muted-foreground">
                    Simple vector search for quick answers
                  </p>
                </div>
              </label>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
