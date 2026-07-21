"use client";

import { useCallback, useEffect, useState } from "react";
import {
  AlertCircle,
  CalendarClock,
  Loader2,
  Pause,
  Play,
  RotateCcw,
  Square,
  Trash2,
} from "lucide-react";
import { api } from "@/lib/api";
import { useIsMounted } from "@/lib/hooks";
import type { AppTaskSummary } from "@/types";

const STATUS_CLS: Record<string, string> = {
  running: "text-[var(--accent)]",
  pending: "text-[var(--accent)]",
  paused: "text-amber-400",
  completed: "text-emerald-400",
  failed: "text-red-400",
  cancelled: "text-muted-foreground",
};

const ACTIVE = new Set(["running", "pending"]);

/**
 * Admin oversight for an app's platform tasks (scheduled syncs et al.) —
 * without it, a failing scheduled task is invisible unless someone opens
 * the app. Rendered inside the AppsManager expanded-details area.
 */
export function AppTasksPanel({ appId }: { appId: string }) {
  const [tasks, setTasks] = useState<AppTaskSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const mounted = useIsMounted();

  const refresh = useCallback(async () => {
    try {
      const data = await api.listAppTasks(appId);
      if (!mounted.current) return;
      setTasks(data);
      setError(null);
    } catch (err) {
      if (!mounted.current) return;
      setError(err instanceof Error ? err.message : "Failed to load tasks");
    }
  }, [appId, mounted]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // poll while anything is active
  useEffect(() => {
    if (!tasks?.some((t) => ACTIVE.has(t.status))) return;
    const timer = window.setTimeout(() => void refresh(), 5000);
    return () => window.clearTimeout(timer);
  }, [tasks, refresh]);

  async function act(taskId: string, action: string) {
    setBusy(taskId);
    try {
      await api.appTaskAction(appId, taskId, action);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : `${action} failed`);
    } finally {
      if (mounted.current) setBusy(null);
    }
  }

  async function remove(taskId: string) {
    setBusy(taskId);
    try {
      await api.deleteAppTask(appId, taskId);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Delete failed");
    } finally {
      if (mounted.current) setBusy(null);
    }
  }

  if (tasks === null && !error) return null; // first load — keep the row quiet
  if (!error && tasks?.length === 0) return null; // apps without tasks stay clean

  return (
    <div className="space-y-1">
      <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
        Background tasks
      </span>
      {error && (
        <div className="flex items-center gap-2 text-xs text-red-400">
          <AlertCircle className="w-3 h-3 shrink-0" />
          {error}
        </div>
      )}
      {tasks?.map((task) => {
        const counts = task.counts ?? {};
        const active = ACTIVE.has(task.status);
        return (
          <div
            key={task.task_id}
            className="flex items-center gap-2 flex-wrap text-xs border border-border/30 rounded px-2 py-1.5 bg-background/50"
          >
            <span className="font-medium text-foreground">{task.name}</span>
            <span className={`font-mono text-[10px] uppercase ${STATUS_CLS[task.status] ?? "text-muted-foreground"}`}>
              {task.status}
            </span>
            {task.schedule?.everyMinutes && (
              <span
                className="inline-flex items-center gap-1 text-[10px] text-muted-foreground"
                title="Runs on a schedule"
              >
                <CalendarClock className="w-3 h-3" />
                every{" "}
                {task.schedule.everyMinutes >= 60
                  ? `${task.schedule.everyMinutes / 60}h`
                  : `${task.schedule.everyMinutes}m`}
              </span>
            )}
            <span className="text-muted-foreground">
              {counts.done ?? 0}/{counts.total ?? 0} done
              {(counts.failed ?? 0) > 0 && (
                <span className="text-red-400"> · {counts.failed} failed</span>
              )}
              {(counts.skipped ?? 0) > 0 && <> · {counts.skipped} skipped</>}
            </span>
            {task.error && (
              <span className="text-red-400 truncate max-w-[16rem]" title={task.error}>
                {task.error}
              </span>
            )}

            <span className="ml-auto flex items-center gap-1">
              {busy === task.task_id ? (
                <Loader2 className="w-3.5 h-3.5 animate-spin text-muted-foreground" />
              ) : (
                <>
                  {active && (
                    <button
                      onClick={() => void act(task.task_id, "pause")}
                      className="p-1 text-muted-foreground hover:text-amber-400 transition-colors"
                      title="Pause"
                    >
                      <Pause className="w-3.5 h-3.5" />
                    </button>
                  )}
                  {task.status === "paused" && (
                    <button
                      onClick={() => void act(task.task_id, "resume")}
                      className="p-1 text-muted-foreground hover:text-[var(--accent)] transition-colors"
                      title="Resume"
                    >
                      <Play className="w-3.5 h-3.5" />
                    </button>
                  )}
                  {!active && (counts.failed ?? 0) > 0 && (
                    <button
                      onClick={() => void act(task.task_id, "retryFailed")}
                      className="p-1 text-muted-foreground hover:text-[var(--accent)] transition-colors"
                      title="Retry failed items"
                    >
                      <RotateCcw className="w-3.5 h-3.5" />
                    </button>
                  )}
                  {!active && task.schedule?.everyMinutes && (
                    <button
                      onClick={() => void act(task.task_id, "runNow")}
                      className="p-1 text-muted-foreground hover:text-[var(--accent)] transition-colors"
                      title="Run now"
                    >
                      <Play className="w-3.5 h-3.5" />
                    </button>
                  )}
                  {active && (
                    <button
                      onClick={() => void act(task.task_id, "cancel")}
                      className="p-1 text-muted-foreground hover:text-red-400 transition-colors"
                      title="Cancel"
                    >
                      <Square className="w-3.5 h-3.5" />
                    </button>
                  )}
                  {!active && (
                    <button
                      onClick={() => void remove(task.task_id)}
                      className="p-1 text-muted-foreground hover:text-red-400 transition-colors"
                      title="Delete task record"
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  )}
                </>
              )}
            </span>
          </div>
        );
      })}
    </div>
  );
}
