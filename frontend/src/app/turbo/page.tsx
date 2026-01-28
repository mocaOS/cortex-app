"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { PageTransition } from "@/components/layout";
import { api } from "@/lib/api";
import type { TurboStatus, TurboJob, TurboBalance } from "@/types";
import {
  Zap,
  Play,
  Square,
  Loader2,
  Server,
  Clock,
  DollarSign,
  Cpu,
  AlertCircle,
  CheckCircle,
  RefreshCw,
  Plus,
  History,
  ChevronDown,
  ChevronUp,
} from "lucide-react";
import { cn } from "@/lib/utils";

function formatDuration(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
  const hours = Math.floor(seconds / 3600);
  const mins = Math.floor((seconds % 3600) / 60);
  return `${hours}h ${mins}m`;
}

function formatTimestamp(ts?: number): string {
  if (!ts) return "-";
  return new Date(ts * 1000).toLocaleString();
}

function WarmingUpBanner({ jobId }: { jobId?: string }) {
  const [logs, setLogs] = useState<string>("");
  const [showLogs, setShowLogs] = useState(false);
  const [loadingLogs, setLoadingLogs] = useState(false);
  const logsContainerRef = useRef<HTMLPreElement>(null);

  const fetchLogs = useCallback(async () => {
    if (!jobId) return;
    setLoadingLogs(true);
    try {
      const result = await api.getTurboJobLogs(jobId);
      setLogs(result.logs || "No logs available yet...");
    } catch (err) {
      setLogs("Failed to fetch logs: " + (err instanceof Error ? err.message : "Unknown error"));
    } finally {
      setLoadingLogs(false);
    }
  }, [jobId]);

  useEffect(() => {
    if (showLogs && jobId) {
      fetchLogs();
      // Auto-refresh logs every 10 seconds while viewing
      const interval = setInterval(fetchLogs, 10000);
      return () => clearInterval(interval);
    }
  }, [showLogs, jobId, fetchLogs]);

  // Auto-scroll to bottom when logs update
  useEffect(() => {
    if (logsContainerRef.current && showLogs) {
      logsContainerRef.current.scrollTop = logsContainerRef.current.scrollHeight;
    }
  }, [logs, showLogs]);

  // Parse logs to extract progress information
  const getProgressInfo = () => {
    if (!logs) return null;
    
    // Look for common progress indicators
    if (logs.includes("Starting to load model")) {
      return { stage: "Loading model weights into GPU memory...", progress: 30 };
    }
    if (logs.includes("Loading model weights took")) {
      return { stage: "Model loaded, initializing inference engine...", progress: 70 };
    }
    if (logs.includes("Uvicorn running") || logs.includes("Application startup complete")) {
      return { stage: "Server ready!", progress: 100 };
    }
    if (logs.includes("Initializing a V1 LLM engine")) {
      return { stage: "Initializing vLLM engine...", progress: 20 };
    }
    if (logs.includes("Downloading")) {
      return { stage: "Downloading model from Hugging Face...", progress: 10 };
    }
    return null;
  };

  const progressInfo = getProgressInfo();

  return (
    <div className="glass rounded-lg p-4 border border-yellow-500/30 bg-yellow-500/10">
      <div className="flex items-start gap-3">
        <Loader2 className="w-5 h-5 text-yellow-400 animate-spin flex-shrink-0 mt-0.5" />
        <div className="flex-1">
          <p className="font-medium text-yellow-400">GPU Server Starting</p>
          
          {progressInfo && (
            <div className="mb-3">
              <div className="flex items-center justify-between text-xs text-yellow-400/80 mb-1">
                <span>{progressInfo.stage}</span>
                <span>{progressInfo.progress}%</span>
              </div>
              <div className="h-1.5 bg-yellow-500/20 rounded-full overflow-hidden">
                <div 
                  className="h-full bg-yellow-400 rounded-full transition-all duration-500"
                  style={{ width: `${progressInfo.progress}%` }}
                />
              </div>
            </div>
          )}

          <button
            onClick={() => setShowLogs(!showLogs)}
            className="flex items-center gap-1.5 text-xs text-yellow-400 hover:text-yellow-300 transition-colors"
          >
            {showLogs ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
            {showLogs ? "Hide Logs" : "Show Server Logs"}
            {loadingLogs && <Loader2 className="w-3 h-3 animate-spin" />}
          </button>

          {showLogs && (
            <div className="mt-3">
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs text-muted-foreground">vLLM Server Output</span>
                <button
                  onClick={fetchLogs}
                  disabled={loadingLogs}
                  className="text-xs text-yellow-400 hover:text-yellow-300 flex items-center gap-1"
                >
                  <RefreshCw className={cn("w-3 h-3", loadingLogs && "animate-spin")} />
                  Refresh
                </button>
              </div>
              <pre 
                ref={logsContainerRef}
                className="bg-black/50 rounded-lg p-3 text-xs text-green-400 font-mono overflow-x-auto max-h-64 overflow-y-auto whitespace-pre-wrap break-all"
              >
                {logs || "Loading..."}
              </pre>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function StatusBadge({ status, isRunning, isReady }: { status: string; isRunning?: boolean; isReady?: boolean }) {
  const statusMap: Record<string, { color: string; label: string }> = {
    running: { color: "bg-green-500/20 text-green-400 border-green-500/30", label: "Running" },
    pending: { color: "bg-yellow-500/20 text-yellow-400 border-yellow-500/30", label: "Pending" },
    queued: { color: "bg-blue-500/20 text-blue-400 border-blue-500/30", label: "Queued" },
    succeeded: { color: "bg-gray-500/20 text-gray-400 border-gray-500/30", label: "Completed" },
    failed: { color: "bg-red-500/20 text-red-400 border-red-500/30", label: "Failed" },
    canceled: { color: "bg-gray-500/20 text-gray-400 border-gray-500/30", label: "Canceled" },
    terminated: { color: "bg-orange-500/20 text-orange-400 border-orange-500/30", label: "Terminated" },
  };

  // Special handling for running jobs that aren't ready yet (warming up)
  if (isRunning && !isReady) {
    return (
      <span className="px-2 py-1 rounded-full text-xs font-medium border bg-yellow-500/20 text-yellow-400 border-yellow-500/30">
        <span className="inline-block w-2 h-2 bg-yellow-400 rounded-full mr-1.5 animate-pulse" />
        Warming Up
      </span>
    );
  }

  const config = statusMap[status] || { color: "bg-gray-500/20 text-gray-400 border-gray-500/30", label: status };

  return (
    <span className={cn("px-2 py-1 rounded-full text-xs font-medium border", config.color)}>
      {isRunning && isReady && <span className="inline-block w-2 h-2 bg-green-400 rounded-full mr-1.5 animate-pulse" />}
      {isRunning && isReady ? "Ready" : config.label}
    </span>
  );
}

function JobCard({ job, onStop }: { job: TurboJob; onStop?: () => void }) {
  const [showDetails, setShowDetails] = useState(false);

  return (
    <div className="glass rounded-lg p-4 space-y-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className={cn(
            "w-10 h-10 rounded-lg flex items-center justify-center",
            job.is_ready ? "bg-green-500/20" : job.is_running ? "bg-yellow-500/20" : "bg-muted"
          )}>
            {job.is_running && !job.is_ready ? (
              <Loader2 className="w-5 h-5 text-yellow-400 animate-spin" />
            ) : (
              <Server className={cn("w-5 h-5", job.is_ready ? "text-green-400" : job.is_running ? "text-yellow-400" : "text-muted-foreground")} />
            )}
          </div>
          <div>
            <h3 className="font-medium text-sm">{job.job_id}</h3>
            <p className="text-xs text-muted-foreground">
              {job.gpu_count}x {job.gpu_type.toUpperCase()} - {job.region?.toUpperCase() || "Auto"}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <StatusBadge status={job.state} isRunning={job.is_running} isReady={job.is_ready} />
          {job.is_running && onStop && (
            <button
              onClick={onStop}
              className="p-2 rounded-lg bg-red-500/20 text-red-400 hover:bg-red-500/30 transition-colors"
              title="Stop Job"
            >
              <Square className="w-4 h-4" />
            </button>
          )}
        </div>
      </div>

      <div className="grid grid-cols-3 gap-4 text-sm">
        <div>
          <p className="text-muted-foreground text-xs">Price</p>
          <p className="font-medium">${job.price_per_hour?.toFixed(2)}/hr</p>
        </div>
        <div>
          <p className="text-muted-foreground text-xs">Runtime</p>
          <p className="font-medium">{formatDuration(job.runtime)}</p>
        </div>
        <div>
          <p className="text-muted-foreground text-xs">Started</p>
          <p className="font-medium">{formatTimestamp(job.started_at)}</p>
        </div>
      </div>

      {job.base_url && (
        <div className="text-xs">
          <p className="text-muted-foreground mb-1">Endpoint</p>
          <code className="bg-muted px-2 py-1 rounded text-green-400 break-all">{job.base_url}</code>
        </div>
      )}

      <button
        onClick={() => setShowDetails(!showDetails)}
        className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors"
      >
        {showDetails ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
        {showDetails ? "Hide Details" : "Show Details"}
      </button>

      {showDetails && (
        <div className="text-xs space-y-1 pt-2 border-t border-border">
          <p><span className="text-muted-foreground">Created:</span> {formatTimestamp(job.created_at)}</p>
          <p><span className="text-muted-foreground">Hostname:</span> {job.hostname || "-"}</p>
          {job.completed_at && (
            <p><span className="text-muted-foreground">Completed:</span> {formatTimestamp(job.completed_at)}</p>
          )}
        </div>
      )}
    </div>
  );
}

export default function TurboModePage() {
  const [status, setStatus] = useState<TurboStatus | null>(null);
  const [balance, setBalance] = useState<TurboBalance | null>(null);
  const [jobs, setJobs] = useState<TurboJob[]>([]);
  const [loading, setLoading] = useState(true);
  const [starting, setStarting] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showHistory, setShowHistory] = useState(false);

  // Custom runtime options
  const [customRuntime, setCustomRuntime] = useState<number>(3600);

  const fetchStatus = useCallback(async () => {
    try {
      const [statusRes, balanceRes] = await Promise.all([
        api.getTurboStatus(),
        api.getTurboBalance().catch(() => null),
      ]);
      setStatus(statusRes);
      if (balanceRes) setBalance(balanceRes);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to fetch status");
    } finally {
      setLoading(false);
    }
  }, []);

  const fetchJobs = useCallback(async () => {
    try {
      const result = await api.listTurboJobs();
      setJobs(result.jobs);
    } catch (err) {
      console.error("Failed to fetch jobs:", err);
    }
  }, []);

  useEffect(() => {
    fetchStatus();
    fetchJobs();

    // Poll for status updates every 10 seconds
    const interval = setInterval(() => {
      fetchStatus();
      fetchJobs();
    }, 10000);

    return () => clearInterval(interval);
  }, [fetchStatus, fetchJobs]);

  const handleStart = async () => {
    setStarting(true);
    setError(null);
    try {
      await api.startTurboMode({ runtime: customRuntime });
      await fetchStatus();
      await fetchJobs();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to start Turbo Mode");
    } finally {
      setStarting(false);
    }
  };

  const handleStop = async (jobId?: string) => {
    setStopping(true);
    setError(null);
    try {
      await api.stopTurboMode(jobId);
      await fetchStatus();
      await fetchJobs();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to stop Turbo Mode");
    } finally {
      setStopping(false);
    }
  };

  if (loading) {
    return (
      <PageTransition>
        <div className="glass rounded-lg p-12 text-center">
          <Loader2 className="w-8 h-8 text-foreground animate-spin mx-auto mb-4" />
          <p className="text-muted-foreground">Loading Turbo Mode status...</p>
        </div>
      </PageTransition>
    );
  }

  if (!status?.available) {
    return (
      <PageTransition>
        <div className="glass rounded-lg p-12 text-center max-w-2xl mx-auto">
          <AlertCircle className="w-16 h-16 text-yellow-400 mx-auto mb-4" />
          <h2 className="text-2xl font-bold mb-4">Turbo Mode Not Available</h2>
          <p className="text-muted-foreground mb-6">
            To enable Turbo Mode, configure your <code className="bg-muted px-2 py-1 rounded">COMPUTE3_API_KEY</code> environment variable.
          </p>
          <p className="text-sm text-muted-foreground">
            Get your API key at{" "}
            <a
              href="https://console.compute3.ai"
              target="_blank"
              rel="noopener noreferrer"
              className="text-accent hover:underline"
            >
              console.compute3.ai
            </a>
          </p>
        </div>
      </PageTransition>
    );
  }

  const activeJob = status.active ? status.job : null;
  const runningJobs = jobs.filter((j) => j.is_running);
  const historyJobs = jobs.filter((j) => !j.is_running);

  return (
    <PageTransition>
      <div className="space-y-6">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-12 h-12 rounded-xl bg-gradient-to-br from-yellow-400 to-orange-500 flex items-center justify-center">
              <Zap className="w-6 h-6 text-white" />
            </div>
            <div>
              <h1 className="text-2xl font-bold">Turbo Mode</h1>
              <p className="text-muted-foreground text-sm">
                GPU-accelerated inference with Compute3
              </p>
            </div>
          </div>
          <button
            onClick={() => { fetchStatus(); fetchJobs(); }}
            className="p-2 rounded-lg hover:bg-muted transition-colors"
            title="Refresh"
          >
            <RefreshCw className="w-5 h-5" />
          </button>
        </div>

        {/* Error Banner */}
        {error && (
          <div className="glass rounded-lg p-4 border border-red-500/30 bg-red-500/10">
            <div className="flex items-center gap-2 text-red-400">
              <AlertCircle className="w-5 h-5" />
              <p>{error}</p>
            </div>
          </div>
        )}

        {/* Warming Up Banner with Logs */}
        {status.active && !status.ready && (
          <WarmingUpBanner jobId={status.job?.job_id} />
        )}

        {/* Status Card */}
        <div className="glass rounded-lg p-6">
          <div className="flex items-center justify-between mb-6">
            <div className="flex items-center gap-4">
              <div className={cn(
                "w-16 h-16 rounded-xl flex items-center justify-center",
                status.ready
                  ? "bg-gradient-to-br from-green-500/30 to-green-600/30"
                  : status.active
                  ? "bg-gradient-to-br from-yellow-500/30 to-orange-500/30"
                  : "bg-muted"
              )}>
                {status.ready ? (
                  <CheckCircle className="w-8 h-8 text-green-400" />
                ) : status.active ? (
                  <Loader2 className="w-8 h-8 text-yellow-400 animate-spin" />
                ) : (
                  <Server className="w-8 h-8 text-muted-foreground" />
                )}
              </div>
              <div>
                <h2 className="text-xl font-semibold">
                  {status.ready 
                    ? "Turbo Mode Ready" 
                    : status.active 
                    ? "Turbo Mode Starting..." 
                    : "Turbo Mode Inactive"}
                </h2>
                <p className="text-muted-foreground">
                  {status.ready
                    ? "GPU acceleration is active - all LLM calls use the turbo endpoint"
                    : status.active
                    ? "GPU server is starting - LLM calls will use turbo mode once ready"
                    : "Start Turbo Mode to enable GPU-accelerated inference"}
                </p>
              </div>
            </div>

            {status.active ? (
              <button
                onClick={() => handleStop()}
                disabled={stopping}
                className="flex items-center gap-2 px-6 py-3 rounded-lg bg-red-500/20 text-red-400 hover:bg-red-500/30 transition-colors disabled:opacity-50"
              >
                {stopping ? (
                  <Loader2 className="w-5 h-5 animate-spin" />
                ) : (
                  <Square className="w-5 h-5" />
                )}
                Stop Turbo Mode
              </button>
            ) : (
              <button
                onClick={handleStart}
                disabled={starting}
                className="flex items-center gap-2 px-6 py-3 rounded-lg bg-gradient-to-r from-yellow-400 to-orange-500 text-white font-medium hover:opacity-90 transition-opacity disabled:opacity-50"
              >
                {starting ? (
                  <Loader2 className="w-5 h-5 animate-spin" />
                ) : (
                  <Play className="w-5 h-5" />
                )}
                Start Turbo Mode
              </button>
            )}
          </div>

          {/* Stats Grid */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <div className="bg-muted/50 rounded-lg p-4">
              <div className="flex items-center gap-2 text-muted-foreground mb-1">
                <Cpu className="w-4 h-4" />
                <span className="text-xs">GPU Config</span>
              </div>
              <p className="font-semibold">
                {status.config ? `${status.config.gpu_count}x ${status.config.gpu_type.toUpperCase()}` : "-"}
              </p>
            </div>
            <div className="bg-muted/50 rounded-lg p-4">
              <div className="flex items-center gap-2 text-muted-foreground mb-1">
                <Server className="w-4 h-4" />
                <span className="text-xs">Model</span>
              </div>
              <p className="font-semibold">{status.config?.model || "-"}</p>
            </div>
            <div className="bg-muted/50 rounded-lg p-4">
              <div className="flex items-center gap-2 text-muted-foreground mb-1">
                <Clock className="w-4 h-4" />
                <span className="text-xs">Default Runtime</span>
              </div>
              <p className="font-semibold">
                {status.config ? formatDuration(status.config.default_runtime) : "-"}
              </p>
            </div>
            <div className="bg-muted/50 rounded-lg p-4">
              <div className="flex items-center gap-2 text-muted-foreground mb-1">
                <DollarSign className="w-4 h-4" />
                <span className="text-xs">Balance</span>
              </div>
              <p className="font-semibold">
                {balance?.total !== undefined ? `$${balance.total.toFixed(2)}` : "-"}
              </p>
            </div>
          </div>

          {/* Runtime Selector (when not active) */}
          {!status.active && (
            <div className="mt-6 pt-6 border-t border-border">
              <label className="block text-sm text-muted-foreground mb-2">
                Runtime Duration
              </label>
              <div className="flex gap-2">
                {[
                  { label: "30m", value: 1800 },
                  { label: "1h", value: 3600 },
                  { label: "2h", value: 7200 },
                  { label: "4h", value: 14400 },
                ].map((option) => (
                  <button
                    key={option.value}
                    onClick={() => setCustomRuntime(option.value)}
                    className={cn(
                      "px-4 py-2 rounded-lg text-sm font-medium transition-colors",
                      customRuntime === option.value
                        ? "bg-accent text-accent-foreground"
                        : "bg-muted hover:bg-muted/70"
                    )}
                  >
                    {option.label}
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Active Job */}
        {activeJob && (
          <div className="space-y-4">
            <h3 className="font-semibold flex items-center gap-2">
              <Zap className="w-5 h-5 text-yellow-400" />
              Active Job
            </h3>
            <JobCard job={activeJob} onStop={() => handleStop(activeJob.job_id)} />
          </div>
        )}

        {/* Running Jobs (if multiple) */}
        {runningJobs.length > 1 && (
          <div className="space-y-4">
            <h3 className="font-semibold flex items-center gap-2">
              <Server className="w-5 h-5 text-green-400" />
              Running Jobs ({runningJobs.length})
            </h3>
            <div className="space-y-3">
              {runningJobs.map((job) => (
                <JobCard
                  key={job.job_id}
                  job={job}
                  onStop={() => handleStop(job.job_id)}
                />
              ))}
            </div>
          </div>
        )}

        {/* Job History */}
        {historyJobs.length > 0 && (
          <div className="space-y-4">
            <button
              onClick={() => setShowHistory(!showHistory)}
              className="flex items-center gap-2 text-muted-foreground hover:text-foreground transition-colors"
            >
              <History className="w-5 h-5" />
              <span className="font-semibold">Job History ({historyJobs.length})</span>
              {showHistory ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
            </button>
            {showHistory && (
              <div className="space-y-3">
                {historyJobs.slice(0, 10).map((job) => (
                  <JobCard key={job.job_id} job={job} />
                ))}
              </div>
            )}
          </div>
        )}

        {/* Info Section */}
        <div className="glass rounded-lg p-6 mt-8">
          <h3 className="font-semibold mb-4">About Turbo Mode</h3>
          <div className="space-y-3 text-sm text-muted-foreground">
            <p>
              <strong className="text-foreground">What is Turbo Mode?</strong>{" "}
              Turbo Mode uses Compute3's GPU infrastructure to run a dedicated vLLM inference server,
              enabling faster document processing and LLM queries.
            </p>
            <p>
              <strong className="text-foreground">When to use it?</strong>{" "}
              Enable Turbo Mode when you need to process many documents quickly or want faster
              response times for your AI queries.
            </p>
            <p>
              <strong className="text-foreground">Pricing:</strong>{" "}
              You're billed per second of GPU usage. Unused time is refunded when you stop the job early.
              Check your balance at{" "}
              <a
                href="https://console.compute3.ai"
                target="_blank"
                rel="noopener noreferrer"
                className="text-accent hover:underline"
              >
                console.compute3.ai
              </a>
            </p>
          </div>
        </div>
      </div>
    </PageTransition>
  );
}
