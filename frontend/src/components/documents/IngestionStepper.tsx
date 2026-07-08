"use client";

import { Check, Loader2, XCircle, FileImage } from "lucide-react";
import { cn } from "@/lib/utils";
import {
  deriveIngestionPhases,
  deriveImageProgress,
  IngestionDocLike,
} from "@/lib/ingestionPhases";

/**
 * Phase timeline for a document moving through the ingestion pipeline:
 * Convert → Chunk & Embed → Store → Extract, plus a parallel image-analysis
 * row when the document has images. Shared by the Documents list and the
 * Knowledge Graph (Step 1) page so both tell the same story.
 */
export function IngestionStepper({
  doc,
  compact = false,
}: {
  doc: IngestionDocLike;
  compact?: boolean;
}) {
  const info = deriveIngestionPhases(doc);
  const images = deriveImageProgress(doc);

  if (info.queued) {
    return (
      <p className={cn("text-muted-foreground", compact ? "text-[11px]" : "text-xs")}>
        {info.statusLine}
      </p>
    );
  }

  return (
    <div className="min-w-0">
      {/* Phase chips */}
      <div className="flex items-center gap-0 overflow-x-auto">
        {info.phases.map((phase, i) => (
          <div key={phase.key} className="flex items-center min-w-0 shrink-0">
            {i > 0 && (
              <div
                className={cn(
                  "h-px mx-1.5",
                  compact ? "w-3" : "w-5",
                  phase.state === "done" || phase.state === "active" || phase.state === "failed"
                    ? "bg-accent/50"
                    : "bg-border"
                )}
              />
            )}
            <div
              className={cn(
                "flex items-center gap-1",
                compact ? "text-[10px]" : "text-[11px]",
                phase.state === "active" && "text-accent",
                phase.state === "done" && "text-muted-foreground",
                phase.state === "todo" && "text-muted-foreground/40",
                phase.state === "failed" && "text-red-400"
              )}
            >
              {phase.state === "done" ? (
                <Check className={compact ? "w-2.5 h-2.5" : "w-3 h-3"} />
              ) : phase.state === "active" ? (
                <Loader2 className={cn("animate-spin", compact ? "w-2.5 h-2.5" : "w-3 h-3")} />
              ) : phase.state === "failed" ? (
                <XCircle className={compact ? "w-2.5 h-2.5" : "w-3 h-3"} />
              ) : (
                <span
                  className={cn(
                    "rounded-full border border-current inline-block",
                    compact ? "w-1.5 h-1.5" : "w-2 h-2"
                  )}
                />
              )}
              <span className="whitespace-nowrap">{phase.label}</span>
            </div>
          </div>
        ))}
      </div>

      {/* Live status line + overall bar */}
      {info.running && info.activeIndex >= 0 && (
        <div className={compact ? "mt-1" : "mt-1.5"}>
          <div
            className={cn(
              "flex items-center justify-between text-muted-foreground",
              compact ? "text-[10px]" : "text-xs"
            )}
          >
            <span className="truncate" title={doc.progress_message || undefined}>
              {info.statusLine}
            </span>
            <span className="shrink-0 ml-2">{info.percent}%</span>
          </div>
          <div className={cn("bg-muted rounded-full overflow-hidden", compact ? "h-1 mt-0.5" : "h-1.5 mt-1")}>
            <div
              className="h-full bg-accent transition-all duration-500"
              style={{ width: `${info.percent}%` }}
            />
          </div>
          {/* Within-phase fraction when the message carries live counts */}
          {typeof info.phases[info.activeIndex]?.fraction === "number" && (
            <div className={cn("bg-muted/60 rounded-full overflow-hidden", compact ? "h-0.5 mt-0.5" : "h-1 mt-1")}>
              <div
                className="h-full bg-accent/50 transition-all duration-500"
                style={{ width: `${Math.round((info.phases[info.activeIndex].fraction as number) * 100)}%` }}
              />
            </div>
          )}
        </div>
      )}

      {/* Parallel image analysis row. Images are extracted during conversion,
          so any counter showing while Convert is still active is stale data
          from an interrupted earlier run — hide it. */}
      {images.active && info.activeIndex >= 1 && (
        <div className={compact ? "mt-1" : "mt-1.5"}>
          <div
            className={cn(
              "flex items-center justify-between text-blue-400/80",
              compact ? "text-[10px]" : "text-xs"
            )}
          >
            <span className="flex items-center gap-1 truncate">
              <FileImage className={compact ? "w-2.5 h-2.5" : "w-3 h-3"} />
              {images.label}
              <span className="text-blue-400/50">(runs alongside extraction)</span>
            </span>
            <span className="shrink-0 ml-2">{images.percent}%</span>
          </div>
          <div className={cn("bg-muted rounded-full overflow-hidden", compact ? "h-0.5 mt-0.5" : "h-1 mt-1")}>
            <div
              className="h-full bg-blue-500/70 transition-all duration-500"
              style={{ width: `${images.percent}%` }}
            />
          </div>
        </div>
      )}
    </div>
  );
}
