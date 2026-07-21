"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { api } from "@/lib/api";
import { useAuth } from "@/components/layout/AuthProvider";
import type { Document, Stats } from "@/types";
import {
  Loader2,
  FileText,
  FileImage,
  Layers,
  Share2,
  Users,
  CheckCircle2,
  Circle,
  AlertCircle,
  RefreshCw,
  Network,
  ArrowRight,
  ExternalLink,
  X,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { IngestionStepper } from "@/components/documents/IngestionStepper";

type StepStatus = "pending" | "in_progress" | "complete";

export default function ExtractAnalyzePage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const hasAutoStarted = useRef(false);

  const [stats, setStats] = useState<Stats | null>(null);
  const [documents, setDocuments] = useState<Document[]>([]);
  const [loading, setLoading] = useState(true);

  // Relationship analysis state
  const [analyzingRelationships, setAnalyzingRelationships] = useState(false);
  const [relationshipTaskMessage, setRelationshipTaskMessage] = useState<string | null>(null);
  const [discoveredRelCount, setDiscoveredRelCount] = useState(0);
  const initialRelCount = useRef(0);

  // Community detection state
  const [detectingCommunities, setDetectingCommunities] = useState(false);
  const [communityTaskMessage, setCommunityTaskMessage] = useState<string | null>(null);

  // Entity extraction state
  const [isExtractingEntities, setIsExtractingEntities] = useState(false);

  // Staleness detection
  const [newDocsSinceAnalysis, setNewDocsSinceAnalysis] = useState(0);
  const [communitiesStale, setCommunitiesStale] = useState(false);
  const [communitiesStaleFromMerge, setCommunitiesStaleFromMerge] = useState(false);
  const [step2Skipped, setStep2Skipped] = useState(false);

  // Entity extraction task message (for polling)
  const [entityTaskMessage, setEntityTaskMessage] = useState<string | null>(null);
  const [showFreshInstanceWarning, setShowFreshInstanceWarning] = useState(false);

  // Regeneration flow state — persisted to sessionStorage to survive hot-reloads
  const [isRegenerating, setIsRegenerating] = useState(false);
  const [regenerateStep, setRegenerateStep] = useState(0);
  const [abortingGeneration, setAbortingGeneration] = useState(false);

  // Poll error resilience: retry on transient errors instead of aborting immediately
  const pollErrorCount = useRef(0);
  const MAX_POLL_ERRORS = 5;
  const activePollRef = useRef<{ taskId: string; step: 1 | 2 | 3 } | null>(null);
  // Stable ref to current poll functions (avoids hook ordering issues with visibilitychange)
  const pollFnsRef = useRef<{
    entity: (id: string) => void;
    relationship: (id: string) => void;
    community: (id: string) => void;
  } | null>(null);


  const fetchData = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    try {
      const [statsData, docsData] = await Promise.all([
        api.getStats(),
        api.getDocuments(),
      ]);
      setStats(statsData);
      setDocuments(docsData.documents || []);
    } catch (error) {
      console.error("Failed to fetch data:", error);
    } finally {
      if (!silent) setLoading(false);
    }
  }, []);

  // Check for running tasks on mount + restore regeneration flow from sessionStorage
  useEffect(() => {
    fetchData();

    // Check if a regeneration was in progress (survives navigation/reload).
    // The chain observer useEffect picks up from here — it looks up whichever
    // pipeline task is currently running on the backend and attaches the
    // appropriate poller.
    const regenActive = sessionStorage.getItem("regenerateActive");
    if (regenActive === "true") {
      const savedStep = sessionStorage.getItem("regenerateStep");
      const step = savedStep ? parseInt(savedStep, 10) : 1;
      setIsRegenerating(true);
      setRegenerateStep(step >= 1 && step <= 3 ? step : 1);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fetchData]);

  // Detect individually running pipeline tasks (not part of regeneration) —
  // continuously, not just on mount: tasks can start while the page is open
  // (a sync app's uploads, the Documents page in another tab, the scheduler).
  // The one-shot mount check used to miss those, leaving the CTA on
  // "Regenerate Graph" during a live build. Pollers take over once attached
  // (activePollRef), and the effect stands down while anything is tracked.
  useEffect(() => {
    if (isRegenerating || isExtractingEntities || analyzingRelationships || detectingCommunities) return;
    let cancelled = false;

    const detect = async () => {
      if (cancelled || activePollRef.current) return;
      try {
        const { tasks } = await api.listTasks("running", "relationship_analysis");
        if (cancelled) return;
        if (tasks.length > 0) {
          const task = tasks[0];
          setAnalyzingRelationships(true);
          setRelationshipTaskMessage(task.message || "Relationship analysis in progress...");
          pollRelationshipTask(task.task_id);
          return;
        }
      } catch {
        // No running tasks
      }

      try {
        const { tasks } = await api.listTasks("running", "community_detection");
        if (cancelled) return;
        if (tasks.length > 0) {
          const task = tasks[0];
          setDetectingCommunities(true);
          setCommunityTaskMessage(task.message || "Community detection in progress...");
          pollCommunityTask(task.task_id);
          return;
        }
      } catch {
        // No running tasks
      }

      try {
        const tasks1 = await api.listTasks("running", "batch_processing");
        const tasks2 = await api.listTasks("running", "reprocess_batch");
        if (cancelled) return;
        const task = tasks1.tasks[0] || tasks2.tasks[0];
        if (task) {
          setIsExtractingEntities(true);
          setEntityTaskMessage(task.message || "Entity extraction in progress...");
          pollEntityTask(task.task_id);
        }
      } catch {
        // No running tasks
      }
    };

    detect();
    const interval = setInterval(detect, 6000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isRegenerating, isExtractingEntities, analyzingRelationships, detectingCommunities]);

  // Compute how many docs were completed after the last relationship analysis
  useEffect(() => {
    const relationshipCount = stats?.relationship_count ?? 0;
    const lastAnalysis = stats?.last_relationship_analysis_at;
    const completedDocs = documents.filter((d) => d.processing_status === "completed");

    if (completedDocs.length === 0 || relationshipCount === 0) {
      setNewDocsSinceAnalysis(0);
      return;
    }

    if (!lastAnalysis) {
      setNewDocsSinceAnalysis(0);
      return;
    }

    // Ensure both dates are compared as UTC — upload_date is stored without
    // timezone info (naive), so append 'Z' to treat it as UTC consistently
    const analysisDate = new Date(lastAnalysis).getTime();
    const newDocs = completedDocs.filter((d) => {
      const dateStr = d.upload_date.includes("+") || d.upload_date.endsWith("Z")
        ? d.upload_date
        : d.upload_date + "Z";
      return new Date(dateStr).getTime() > analysisDate;
    });
    setNewDocsSinceAnalysis(newDocs.length);
  }, [documents, stats]);

  // Detect if communities are stale (relationship analysis or entity merge ran after last community detection)
  useEffect(() => {
    const communityCount = stats?.community_count ?? 0;
    const lastAnalysis = stats?.last_relationship_analysis_at;
    const lastDetection = stats?.last_community_detection_at;
    const lastMerge = stats?.last_entity_merge_at;

    if (communityCount === 0) {
      setCommunitiesStale(false);
      setCommunitiesStaleFromMerge(false);
      return;
    }

    const detectionDate = lastDetection ? new Date(lastDetection).getTime() : 0;

    // Relationship analysis staleness
    if (lastAnalysis) {
      const analysisDate = new Date(lastAnalysis).getTime();
      setCommunitiesStale(analysisDate > detectionDate);
    } else {
      setCommunitiesStale(false);
    }

    // Entity merge staleness (independent of relationship analysis)
    if (lastMerge) {
      const mergeDate = new Date(lastMerge).getTime();
      setCommunitiesStaleFromMerge(mergeDate > detectionDate);
    } else {
      setCommunitiesStaleFromMerge(false);
    }
  }, [stats]);

  // Track whether any documents are still analyzing images (background process)
  const hasImageAnalysisInProgress = documents.some((d) => {
    if (d.processing_status !== "completed") return false;
    const hasImages = (d.image_progress_total ?? 0) > 0;
    return hasImages && d.image_progress_current !== d.image_progress_total;
  });

  // Ingestion running regardless of who started it (an app upload with
  // start_processing, the pipeline auto-start, another tab): derived from
  // document states — the same signal the Step-1 tile uses. Per-document
  // processing has no batch task record, so the task flags alone miss it,
  // which used to leave the CTA on "Regenerate Graph" during a live build.
  const ingestionActive = documents.some(
    (d) =>
      d.processing_status === "processing" ||
      d.processing_status === "extracting" ||
      (d.processing_status === "completed" &&
        (d.image_progress_total ?? 0) > 0 &&
        d.image_progress_current !== d.image_progress_total),
  );

  // Auto-refresh stats: 5s while anything runs, slow 15s heartbeat when idle —
  // the heartbeat is what notices externally-started ingestion (sync apps,
  // other tabs) in the first place; without it document state never updates
  // and ingestionActive could never flip on.
  useEffect(() => {
    const active =
      analyzingRelationships || detectingCommunities || isExtractingEntities ||
      hasImageAnalysisInProgress || isRegenerating || ingestionActive;
    const interval = setInterval(() => fetchData(true), active ? 5000 : 15000);
    return () => clearInterval(interval);
  }, [analyzingRelationships, detectingCommunities, isExtractingEntities, hasImageAnalysisInProgress, isRegenerating, ingestionActive, fetchData]);

  // End the regeneration flow (called when chain finishes or aborts).
  const finishRegeneration = useCallback(() => {
    sessionStorage.removeItem("regenerateActive");
    sessionStorage.removeItem("regenerateStep");
    sessionStorage.removeItem("regenerateStartedAt");
    sessionStorage.removeItem("regenerateTaskId");
    setIsRegenerating(false);
    setRegenerateStep(0);
    fetchData(true);
  }, [fetchData]);

  // Backwards-compat alias used by polling functions on failure paths.
  const abortRegeneration = finishRegeneration;

  // User-initiated abort: stop the backend pipeline (Step-1 processing + the
  // Step-2/3 chain) and reset the UI to idle so the Generate/Regenerate button
  // is usable again. Non-destructive — documents/entities are kept.
  const handleAbortGeneration = async () => {
    if (!confirm("Abort graph generation? This stops the running pipeline. Your documents are kept — you can rebuild the graph afterward.")) return;
    setAbortingGeneration(true);
    try {
      await api.abortGraphGeneration();
    } catch (e) {
      console.error("Failed to abort graph generation:", e);
    } finally {
      // Clear the per-step running flags immediately so the idle CTA returns
      // without waiting for the next poll; abortRegeneration() clears the regen
      // flow state (sessionStorage + isRegenerating) and refetches.
      setIsExtractingEntities(false);
      setAnalyzingRelationships(false);
      setDetectingCommunities(false);
      setAbortingGeneration(false);
      abortRegeneration();
    }
  };

  const pollEntityTask = useCallback(async (taskId: string) => {
    activePollRef.current = { taskId, step: 1 };
    try {
      const status = await api.getTaskStatus(taskId);
      await fetchData(true);
      pollErrorCount.current = 0; // Reset on success

      const progressMsg = status.message || `Progress: ${status.progress_percent}%`;
      setEntityTaskMessage(progressMsg);

      if (status.status === "completed") {
        activePollRef.current = null;
        const result = status.result as Record<string, unknown> | undefined;
        const processed = result?.processed ?? 0;
        setEntityTaskMessage(`Entity extraction complete! ${processed} document${processed !== 1 ? "s" : ""} processed.`);
        await fetchData(true);
        // The chain-observer useEffect will detect the backend-spawned
        // relationship_analysis task and pick it up — no frontend advance here.
        setTimeout(() => {
          setIsExtractingEntities(false);
          if (sessionStorage.getItem("regenerateActive") === null) {
            setEntityTaskMessage(null);
          }
        }, 1500);
      } else if (status.status === "failed") {
        activePollRef.current = null;
        setEntityTaskMessage(`Failed: ${status.message}`);
        setIsExtractingEntities(false);
        if (sessionStorage.getItem("regenerateActive") !== null) abortRegeneration();
      } else {
        setTimeout(() => pollEntityTask(taskId), 2000);
      }
    } catch {
      pollErrorCount.current += 1;
      if (pollErrorCount.current >= MAX_POLL_ERRORS) {
        activePollRef.current = null;
        setEntityTaskMessage(null);
        setIsExtractingEntities(false);
        if (sessionStorage.getItem("regenerateActive") !== null) abortRegeneration();
      } else {
        // Retry with backoff
        setTimeout(() => pollEntityTask(taskId), 3000 * pollErrorCount.current);
      }
    }
  }, [fetchData, abortRegeneration]);

  const pollRelationshipTask = useCallback(async (taskId: string) => {
    activePollRef.current = { taskId, step: 2 };
    try {
      const status = await api.getTaskStatus(taskId);
      const statsData = await api.getStats();
      setStats(statsData);
      pollErrorCount.current = 0; // Reset on success

      const crossDocRels = (statsData.relationship_count ?? 0) - (statsData.per_chunk_relationship_count ?? 0);
      const newlyDiscovered = crossDocRels - initialRelCount.current;
      if (newlyDiscovered > 0) setDiscoveredRelCount(newlyDiscovered);

      const backendMsg = status.message || `Progress: ${status.progress_percent}%`;
      const countMsg = newlyDiscovered > 0
        ? `${newlyDiscovered} new cross-document relations found, still connecting the dots... (${backendMsg})`
        : backendMsg;
      setRelationshipTaskMessage(countMsg);

      if (status.status === "completed") {
        activePollRef.current = null;
        setRelationshipTaskMessage(`Analysis complete! ${crossDocRels - initialRelCount.current} cross-document relations discovered.`);
        setNewDocsSinceAnalysis(0);
        await fetchData(true);
        // The chain-observer useEffect will detect the backend-spawned
        // community_detection task and pick it up — no frontend advance here.
        setTimeout(() => {
          setAnalyzingRelationships(false);
          setDiscoveredRelCount(0);
          if (sessionStorage.getItem("regenerateActive") === null) {
            setRelationshipTaskMessage(null);
          }
        }, 1500);
      } else if (status.status === "failed") {
        activePollRef.current = null;
        setRelationshipTaskMessage(`Failed: ${status.message}`);
        setAnalyzingRelationships(false);
        if (sessionStorage.getItem("regenerateActive") !== null) abortRegeneration();
      } else {
        setTimeout(() => pollRelationshipTask(taskId), 2000);
      }
    } catch {
      pollErrorCount.current += 1;
      if (pollErrorCount.current >= MAX_POLL_ERRORS) {
        activePollRef.current = null;
        setRelationshipTaskMessage(null);
        setAnalyzingRelationships(false);
        if (sessionStorage.getItem("regenerateActive") !== null) abortRegeneration();
      } else {
        // Retry with backoff
        setTimeout(() => pollRelationshipTask(taskId), 3000 * pollErrorCount.current);
      }
    }
  }, [fetchData, abortRegeneration]);

  const handleAnalyzeRelationships = async (rebuild = false) => {
    try {
      setAnalyzingRelationships(true);
      setDiscoveredRelCount(0);
      // Capture current cross-document relationship count so we only show newly discovered ones
      // (per-chunk relationships from Step 1 are excluded from the count)
      initialRelCount.current = (stats?.relationship_count ?? 0) - (stats?.per_chunk_relationship_count ?? 0);
      setRelationshipTaskMessage(rebuild ? "Starting full rebuild..." : "Starting relationship analysis...");
      const result = await api.analyzeRelationships(undefined, "full", rebuild);
      setTimeout(() => pollRelationshipTask(result.task_id), 1500);
    } catch (error) {
      console.error("Failed to analyze relationships:", error);
      setRelationshipTaskMessage(null);
      setAnalyzingRelationships(false);
    }
  };

  const pollCommunityTask = useCallback(async (taskId: string) => {
    activePollRef.current = { taskId, step: 3 };
    try {
      const status = await api.getTaskStatus(taskId);
      pollErrorCount.current = 0; // Reset on success

      const progressMsg = status.message || `Progress: ${status.progress_percent}%`;
      setCommunityTaskMessage(progressMsg);

      if (status.status === "completed") {
        activePollRef.current = null;
        const communityCount = (status.result as Record<string, unknown>)?.total ?? 0;
        setCommunityTaskMessage(`Communities detected successfully! ${communityCount} communities found.`);
        setCommunitiesStale(false);
        await fetchData(true);
        const isRegen = sessionStorage.getItem("regenerateActive") !== null;
        setTimeout(() => {
          setCommunityTaskMessage(null);
          setDetectingCommunities(false);
          // Step 3 is the final step of the chain — end the regen flow here.
          if (isRegen) finishRegeneration();
        }, isRegen ? 1000 : 3000);
      } else if (status.status === "failed") {
        activePollRef.current = null;
        setCommunityTaskMessage(`Failed: ${status.message}`);
        setDetectingCommunities(false);
        if (sessionStorage.getItem("regenerateActive") !== null) abortRegeneration();
      } else {
        setTimeout(() => pollCommunityTask(taskId), 2000);
      }
    } catch {
      pollErrorCount.current += 1;
      if (pollErrorCount.current >= MAX_POLL_ERRORS) {
        activePollRef.current = null;
        setCommunityTaskMessage(null);
        setDetectingCommunities(false);
        if (sessionStorage.getItem("regenerateActive") !== null) abortRegeneration();
      } else {
        // Retry with backoff
        setTimeout(() => pollCommunityTask(taskId), 3000 * pollErrorCount.current);
      }
    }
  }, [fetchData, finishRegeneration, abortRegeneration]);

  // Keep poll function refs current and resume polling on tab visibility change.
  // Browsers throttle setTimeout in background tabs, so polls may stall during
  // long-running tasks (Step 2 can take 30+ minutes). This ensures we catch up
  // immediately when the user returns to the tab.
  pollFnsRef.current = { entity: pollEntityTask, relationship: pollRelationshipTask, community: pollCommunityTask };
  useEffect(() => {
    const handleVisibility = () => {
      if (document.visibilityState !== "visible") return;
      const active = activePollRef.current;
      const fns = pollFnsRef.current;
      if (!active || !fns) return;
      pollErrorCount.current = 0;
      if (active.step === 1) fns.entity(active.taskId);
      else if (active.step === 2) fns.relationship(active.taskId);
      else if (active.step === 3) fns.community(active.taskId);
    };
    document.addEventListener("visibilitychange", handleVisibility);
    return () => document.removeEventListener("visibilitychange", handleVisibility);
  }, []);

  const handleDetectCommunities = async () => {
    try {
      setDetectingCommunities(true);
      setCommunityTaskMessage("Detecting communities...");
      const result = await api.detectCommunities(3);
      setTimeout(() => pollCommunityTask(result.task_id), 1500);
    } catch (error) {
      console.error("Failed to detect communities:", error);
      setCommunityTaskMessage("Failed to detect communities.");
      setDetectingCommunities(false);
    }
  };

  const handleExtractEntities = async () => {
    setIsExtractingEntities(true);
    setEntityTaskMessage("Starting entity extraction...");
    try {
      const result = await api.processPendingDocuments();
      if (!result.task_id) {
        setEntityTaskMessage(null);
        setIsExtractingEntities(false);
        await fetchData(true);
        return;
      }
      setTimeout(() => pollEntityTask(result.task_id), 1500);
    } catch (error) {
      console.error("Failed to start processing:", error);
      setEntityTaskMessage(null);
      setIsExtractingEntities(false);
    }
  };

  // Chain observer: while a regeneration is active, poll for whichever
  // pipeline task type is currently running on the backend and attach the
  // appropriate poller. The backend chains Step 1 → 2 → 3 server-side, so
  // we just observe — no advanceStep / no startStep / no per-step task IDs.
  // This is what makes the flow survive navigation, browser close, and
  // backend restarts (the only failure mode now is the backend itself
  // losing in-memory state on reload, which we surface and clear).
  const highestSeenStep = useRef(0);
  const emptyPollCount = useRef(0);
  const EMPTY_POLL_LIMIT = 10; // ~30s of no tasks → abort
  useEffect(() => {
    if (!isRegenerating) return;
    let cancelled = false;

    // Restore highest step seen across reloads
    const savedStep = sessionStorage.getItem("regenerateStep");
    if (savedStep) {
      const n = parseInt(savedStep, 10);
      if (n > highestSeenStep.current) highestSeenStep.current = n;
    }

    const checkActive = async () => {
      if (cancelled) return;
      try {
        const [b1, b2, rel, com] = await Promise.all([
          api.listTasks("running", "batch_processing"),
          api.listTasks("running", "reprocess_batch"),
          api.listTasks("running", "relationship_analysis"),
          api.listTasks("running", "community_detection"),
        ]);
        if (cancelled) return;

        const batchTasks = [...(b1.tasks || []), ...(b2.tasks || [])];
        const relTasks = rel.tasks || [];
        const comTasks = com.tasks || [];

        let activeStep = 0;
        let activeTaskId: string | null = null;
        if (batchTasks.length > 0) {
          activeStep = 1;
          activeTaskId = batchTasks[0].task_id;
        } else if (relTasks.length > 0) {
          activeStep = 2;
          activeTaskId = relTasks[0].task_id;
        } else if (comTasks.length > 0) {
          activeStep = 3;
          activeTaskId = comTasks[0].task_id;
        }

        if (activeStep > 0 && activeTaskId) {
          emptyPollCount.current = 0;
          if (activeStep > highestSeenStep.current) {
            highestSeenStep.current = activeStep;
          }
          if (activeStep !== regenerateStep) {
            setRegenerateStep(activeStep);
            sessionStorage.setItem("regenerateStep", String(activeStep));
          }
          // Attach the appropriate poller if we aren't already polling this task
          if (activePollRef.current?.taskId !== activeTaskId) {
            if (activeStep === 1) {
              setIsExtractingEntities(true);
              pollEntityTask(activeTaskId);
            } else if (activeStep === 2) {
              setAnalyzingRelationships(true);
              if (initialRelCount.current === 0) {
                initialRelCount.current = (stats?.relationship_count ?? 0) - (stats?.per_chunk_relationship_count ?? 0);
              }
              pollRelationshipTask(activeTaskId);
            } else if (activeStep === 3) {
              setDetectingCommunities(true);
              pollCommunityTask(activeTaskId);
            }
          }
        } else {
          // No pipeline task running. Either we're between steps (backend
          // is about to spawn the next one — wait), or we're done, or
          // backend lost state.
          if (highestSeenStep.current >= 3) {
            // Step 3 was seen and is now gone → chain complete
            finishRegeneration();
            return;
          }
          emptyPollCount.current += 1;
          if (emptyPollCount.current >= EMPTY_POLL_LIMIT) {
            // ~30s of nothing running → backend likely lost state (reload)
            console.warn("Regen chain: no pipeline task observed for 30s, aborting");
            abortRegeneration();
            return;
          }
        }
      } catch (err) {
        // Transient network issue — try again on next tick
        console.warn("Regen observer poll failed:", err);
      }
    };

    checkActive(); // immediate
    const interval = setInterval(checkActive, 3000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isRegenerating]);

  const handleRegenerateGraph = async () => {
    const hasExistingGraph = (stats?.entity_count ?? 0) > 0;
    if (hasExistingGraph) {
      if (!confirm("CAREFUL! This action will reprocess all documents and rebuild the entire knowledge graph from scratch. Continue?")) return;
    }

    // Clear any previous regen state and flag this run as active.
    sessionStorage.removeItem("regenerateTaskId"); // legacy cleanup
    sessionStorage.setItem("regenerateActive", "true");
    sessionStorage.setItem("regenerateStep", "1");
    sessionStorage.setItem("regenerateStartedAt", new Date().toISOString());
    highestSeenStep.current = 1;
    emptyPollCount.current = 0;
    setIsRegenerating(true);
    setRegenerateStep(1);

    // Clear existing graph data, then kick off Step 1 with the backend chain.
    setIsExtractingEntities(true);
    setEntityTaskMessage("Clearing existing graph data...");
    try {
      await api.deleteAllCommunities();
      await api.deleteAllRelationships();
      await api.deleteAllEntities();
    } catch { /* may fail if nothing exists */ }
    await fetchData(true);

    setEntityTaskMessage("Processing all documents...");
    try {
      const allDocIds = documents.map(d => d.id);
      // The backend chain parameter is what makes this flow survive
      // navigation/close — after Step 1's task finishes, the backend
      // spawns Step 2's task on its own, and Step 3 after that.
      const result = await api.reprocessDocuments(
        allDocIds,
        undefined,
        "relationship_analysis,community_detection",
      );
      if (result.task_id) {
        setTimeout(() => pollEntityTask(result.task_id!), 1500);
      } else {
        // No documents queued — the chain observer will detect Step 2
        // when the backend spawns it (or time out if it doesn't).
        setIsExtractingEntities(false);
        setEntityTaskMessage(null);
      }
    } catch (error) {
      console.error("Graph generation failed:", error);
      abortRegeneration();
    }
  };

  // Auto-trigger graph work when navigated here with ?autostart=1 (the banner
  // button on /documents). Fires exactly once per arrival; the URL param is
  // stripped immediately so a refresh won't re-fire. Respects the same
  // preconditions as the manual buttons (docs must be loaded and present, no
  // run already in progress). With an existing graph, arriving from the
  // Documents banner means "fold my new uploads in" — run incremental Step 1
  // (same as "Extract Entities"), never the destructive full rebuild. Only a
  // fresh instance (no entities yet) gets the full Generate Graph chain.
  useEffect(() => {
    if (hasAutoStarted.current) return;
    if (searchParams.get("autostart") !== "1") return;
    if (loading) return;
    if (documents.length === 0) return;
    if (isRegenerating || isExtractingEntities) return;
    hasAutoStarted.current = true;
    router.replace("/extract", { scroll: false });
    if ((stats?.entity_count ?? 0) > 0) {
      handleExtractEntities();
    } else {
      handleRegenerateGraph();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams, loading, documents.length, isRegenerating, isExtractingEntities, router]);

  if (loading) {
    return (
      <div className="py-6">
        <div className="flex items-center justify-center h-96">
          <Loader2 className="w-8 h-8 animate-spin text-muted-foreground" />
        </div>
      </div>
    );
  }

  // Compute step statuses
  const docCount = stats?.document_count ?? 0;
  const entityCount = stats?.entity_count ?? 0;
  const relationshipCount = stats?.relationship_count ?? 0;
  const communityCount = stats?.community_count ?? 0;
  // Queued = parked on the processing-slot semaphore (status stays
  // 'processing' backend-side, but nothing is running for them yet) —
  // count them as waiting, not working, or a 300-doc sync-app burst reads
  // as 300 concurrent pipelines.
  const queuedDocs = documents.filter(
    (d) =>
      (d.processing_status === "processing" || d.processing_status === "extracting") &&
      d.processing_queued
  );
  const processingDocs = documents.filter(
    (d) =>
      (d.processing_status === "processing" || d.processing_status === "extracting") &&
      !d.processing_queued
  );
  const completedDocs = documents.filter((d) => d.processing_status === "completed");
  const failedDocs = documents.filter((d) => d.processing_status === "failed");
  const pendingDocs = documents.filter((d) => d.processing_status === "pending");

  // Documents that are "completed" but still have images being analyzed in the background
  const analyzingImagesDocs = documents.filter((d) => {
    if (d.processing_status !== "completed") return false;
    const hasImages = (d.image_progress_total ?? 0) > 0;
    const imagesDone = d.image_progress_current === d.image_progress_total;
    return hasImages && !imagesDone;
  });
  const totalImagesCurrent = analyzingImagesDocs.reduce((sum, d) => sum + (d.image_progress_current ?? 0), 0);
  const totalImagesTotal = analyzingImagesDocs.reduce((sum, d) => sum + (d.image_progress_total ?? 0), 0);
  // Fully done = completed status AND no pending image analysis
  const fullyCompletedDocs = completedDocs.filter((d) => {
    const hasImages = (d.image_progress_total ?? 0) > 0;
    const imagesDone = !hasImages || d.image_progress_current === d.image_progress_total;
    return imagesDone;
  });

  // Step 1: Entity Extraction & Relationship Discovery
  const step1Stale = entityCount > 0 && pendingDocs.length > 0 && processingDocs.length === 0 && queuedDocs.length === 0 && analyzingImagesDocs.length === 0 && !isExtractingEntities;
  const step1Status: StepStatus =
    processingDocs.length > 0 || queuedDocs.length > 0 || documents.some((d) => d.processing_status === "extracting") || isExtractingEntities || analyzingImagesDocs.length > 0
      ? "in_progress"
      : entityCount > 0 && !step1Stale
        ? "complete"
        : "pending";

  // Step 2: Deep Relationship Analysis
  const step2Blocked = step1Status !== "complete";
  const step2Stale = !step2Blocked && relationshipCount > 0 && newDocsSinceAnalysis > 0 && !analyzingRelationships;
  const step2Status: StepStatus = step2Blocked
    ? "pending"
    : analyzingRelationships
      ? "in_progress"
      : relationshipCount > 0 && !step2Stale
        ? "complete"
        : "pending";

  // Step 3: Community Detection
  const step2EffectivelyComplete = step2Status === "complete" || (step2Skipped && relationshipCount > 0);
  const step3Blocked = step2Blocked || !step2EffectivelyComplete;
  const step3Stale = !step3Blocked && communityCount > 0 && (communitiesStale || communitiesStaleFromMerge) && !detectingCommunities;
  const step3Status: StepStatus = step3Blocked
    ? "pending"
    : detectingCommunities
      ? "in_progress"
      : communityCount > 0 && !step3Stale
        ? "complete"
        : "pending";

  const getStepIcon = (status: StepStatus) => {
    switch (status) {
      case "complete":
        return <CheckCircle2 className="w-6 h-6 text-green-400" />;
      case "in_progress":
        return <Loader2 className="w-6 h-6 text-accent animate-spin" />;
      default:
        return <Circle className="w-6 h-6 text-muted-foreground" />;
    }
  };

  const getStepBorder = (status: StepStatus) => {
    switch (status) {
      case "complete":
        return "border-green-500/30";
      case "in_progress":
        return "border-accent/50";
      default:
        return "border-border";
    }
  };

  return (
    <div className="py-6">
      {/* Top CTA: Generate/Regenerate when idle; Abort while a pipeline run is
          active. Keyed off the actual running flags AND document-derived
          ingestion activity (per-document processing has no batch task — e.g.
          sync-app uploads), so a live build can always be stopped from here. */}
      <div className="mb-6 flex justify-end">
        {(isRegenerating || isExtractingEntities || analyzingRelationships || detectingCommunities || ingestionActive) ? (
          <button
            onClick={handleAbortGeneration}
            disabled={abortingGeneration}
            title="Stop the running graph generation. Documents are kept; you can rebuild afterward."
            className={cn(
              "inline-flex items-center gap-2.5 px-6 py-3 rounded-lg text-base font-semibold transition-all border",
              abortingGeneration
                ? "border-red-500/30 text-red-400/60 cursor-not-allowed opacity-60"
                : "border-red-500/40 text-red-400 hover:bg-red-500/10"
            )}
          >
            <X className="w-5 h-5" />
            {abortingGeneration ? "Aborting…" : "Abort Generation"}
          </button>
        ) : (
          <button
            onClick={handleRegenerateGraph}
            disabled={docCount === 0}
            className={cn(
              "inline-flex items-center gap-2.5 px-6 py-3 rounded-lg text-base font-semibold transition-all",
              docCount === 0
                ? "bg-accent/50 text-accent-foreground cursor-not-allowed opacity-50"
                : "bg-accent text-accent-foreground hover:bg-accent/90"
            )}
          >
            <RefreshCw className="w-5 h-5" />
            {entityCount === 0 ? "Generate Graph" : "Regenerate Graph"}
          </button>
        )}
      </div>

      {/* Generation/Regeneration progress banner */}
      {isRegenerating && (
        <div className="mb-4 p-4 bg-accent/10 border border-accent/20 rounded-xl">
          <div className="flex items-center gap-3">
            <div className="flex-1">
              <p className="text-sm font-medium text-foreground">Generating Knowledge Graph</p>
              <p className="text-xs text-muted-foreground mt-0.5">
                {regenerateStep === 1 ? "Step 1 of 3: Extracting entities and relationships from documents..." :
                 regenerateStep === 2 ? "Step 2 of 3: Deep analysis of cross-document relations..." :
                 "Step 3 of 3: Detecting communities in the knowledge graph..."}
              </p>
            </div>
            <div className="flex gap-1.5">
              {[1, 2, 3].map(s => (
                <div key={s} className={cn(
                  "w-2 h-2 rounded-full transition-colors",
                  s < regenerateStep ? "bg-green-400" :
                  s === regenerateStep ? "bg-accent animate-pulse" :
                  "bg-muted-foreground/30"
                )} />
              ))}
            </div>
          </div>
        </div>
      )}

      {/* Pipeline Steps */}
      <div className="space-y-4">
        {/* Step 1: Entity Extraction & Relationship Discovery */}
        <div className={cn("p-6 bg-card border rounded-xl transition-colors", step1Stale ? "border-yellow-500/30" : getStepBorder(step1Status))}>
          <div className="flex items-start gap-4">
            <div className="flex-shrink-0 mt-0.5">
              {step1Stale ? <AlertCircle className="w-6 h-6 text-yellow-400" /> : getStepIcon(step1Status)}
            </div>
            <div className="flex-1 min-w-0">
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-3">
                  <h3 className="text-lg font-semibold">Step 1: Entity Extraction & Relationship Discovery</h3>
                  <span className={cn(
                    "px-2 py-0.5 text-xs rounded-full font-medium",
                    step1Stale ? "bg-yellow-500/20 text-yellow-400" :
                    step1Status === "complete" ? "bg-green-500/20 text-green-400" :
                    step1Status === "in_progress" ? "bg-accent/20 text-accent" :
                    "bg-muted text-muted-foreground"
                  )}>
                    {step1Stale ? "Needs Update" : step1Status === "complete" ? "Complete" : step1Status === "in_progress" ? "In Progress" : "Pending"}
                  </span>
                </div>
                {entityCount > 0 && (
                  <Link href="/explore?tab=entities" className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-muted hover:bg-muted/80 rounded-lg text-xs font-medium transition-colors">
                    <ExternalLink className="w-3 h-3" />
                    Inspect
                  </Link>
                )}
              </div>
              <p className="text-sm text-muted-foreground mb-4">
                Extracts entities like people, organizations, and concepts from each document, and discovers relations grounded in the source text.
              </p>

              {/* Document processing summary */}
              <div className={cn(
                "grid gap-3 mb-4 grid-cols-2",
                (["sm:grid-cols-4", "sm:grid-cols-5", "sm:grid-cols-6"] as const)[
                  (analyzingImagesDocs.length > 0 ? 1 : 0) + (queuedDocs.length > 0 ? 1 : 0)
                ]
              )}>
                <div className="p-3 bg-muted/50 rounded-lg">
                  <p className="text-lg font-semibold">{fullyCompletedDocs.length}</p>
                  <p className="text-xs text-muted-foreground">Processed</p>
                </div>
                <div className="p-3 bg-muted/50 rounded-lg">
                  <p className="text-lg font-semibold">{processingDocs.length}</p>
                  <p className="text-xs text-muted-foreground">Processing</p>
                </div>
                {queuedDocs.length > 0 && (
                  <div className="p-3 bg-muted/50 rounded-lg">
                    <p className="text-lg font-semibold">{queuedDocs.length}</p>
                    <p className="text-xs text-muted-foreground">Queued</p>
                  </div>
                )}
                {analyzingImagesDocs.length > 0 && (
                  <div className="p-3 bg-blue-500/10 border border-blue-500/20 rounded-lg">
                    <p className="text-lg font-semibold text-blue-400">{analyzingImagesDocs.length}</p>
                    <p className="text-xs text-blue-400/70">Analyzing Images</p>
                  </div>
                )}
                <div className="p-3 bg-muted/50 rounded-lg">
                  <p className="text-lg font-semibold">{pendingDocs.length}</p>
                  <p className="text-xs text-muted-foreground">Pending</p>
                </div>
                <div className="p-3 bg-muted/50 rounded-lg">
                  <p className="text-lg font-semibold">{failedDocs.length}</p>
                  <p className="text-xs text-muted-foreground">Failed</p>
                </div>
              </div>

              {/* Per-document phase breakdown while Step 1 runs */}
              {(processingDocs.length > 0 || analyzingImagesDocs.length > 0) && (
                <div className="mb-3 p-3 bg-accent/5 border border-accent/20 rounded-lg">
                  <div className="flex items-center gap-2 mb-2 text-sm text-accent">
                    <span>
                      {processingDocs.length > 0 &&
                        `${processingDocs.length} document${processingDocs.length !== 1 ? "s" : ""} in the text pipeline`}
                      {processingDocs.length > 0 && analyzingImagesDocs.length > 0 && " · "}
                      {analyzingImagesDocs.length > 0 &&
                        `${analyzingImagesDocs.length} finishing image analysis (${totalImagesCurrent}/${totalImagesTotal} images)`}
                      {queuedDocs.length > 0 && ` · ${queuedDocs.length} queued for a slot`}
                      {pendingDocs.length > 0 && ` · ${pendingDocs.length} pending`}
                    </span>
                  </div>
                  <div className="space-y-2.5 max-h-72 overflow-y-auto pr-1">
                    {processingDocs.map((d) => (
                      <div key={d.id} className="min-w-0">
                        <p className="text-xs text-foreground/80 truncate mb-1" title={d.filename}>
                          {d.filename}
                        </p>
                        <IngestionStepper doc={d} compact />
                      </div>
                    ))}
                    {analyzingImagesDocs.map((d) => (
                      <div key={d.id} className="min-w-0">
                        <p className="text-xs text-foreground/80 truncate mb-1" title={d.filename}>
                          {d.filename}
                        </p>
                        <div className="flex items-center justify-between text-[10px] text-blue-400/80">
                          <span className="flex items-center gap-1">
                            <FileImage className="w-2.5 h-2.5" />
                            Analyzing images {d.image_progress_current ?? 0}/{d.image_progress_total ?? 0}
                          </span>
                          <span>
                            {Math.round(((d.image_progress_current ?? 0) / Math.max(1, d.image_progress_total ?? 0)) * 100)}%
                          </span>
                        </div>
                        <div className="h-1 bg-muted rounded-full overflow-hidden mt-0.5">
                          <div
                            className="h-full bg-blue-500/70 transition-all duration-500"
                            style={{ width: `${Math.round(((d.image_progress_current ?? 0) / Math.max(1, d.image_progress_total ?? 0)) * 100)}%` }}
                          />
                        </div>
                      </div>
                    ))}
                  </div>
                  {analyzingImagesDocs.length > 0 && (
                    <p className="text-xs text-blue-400/60 mt-2">
                      Entities from images are included once analysis completes; Step 1 finishes when all images are processed.
                    </p>
                  )}
                </div>
              )}

              {(pendingDocs.length > 0) && processingDocs.length === 0 && !isExtractingEntities && (
                <div className="flex items-center justify-between mb-3 p-3 bg-yellow-500/10 border border-yellow-500/20 rounded-lg">
                  <div className="flex items-start gap-2">
                    <AlertCircle className="w-4 h-4 text-yellow-400 mt-0.5 flex-shrink-0" />
                    <p className="text-sm text-yellow-200">
                      {pendingDocs.length} new document{pendingDocs.length !== 1 ? "s have" : " has"} been uploaded but not yet processed. Extract entities to include them in your knowledge graph.
                    </p>
                  </div>
                  <button
                    onClick={() => {
                      if (entityCount === 0) {
                        setShowFreshInstanceWarning(true);
                      } else {
                        handleExtractEntities();
                      }
                    }}
                    className="inline-flex items-center gap-2 px-4 py-2 bg-accent text-accent-foreground rounded-lg text-sm font-medium hover:bg-accent/90 transition-colors shrink-0 ml-4"
                  >
                    <Layers className="w-4 h-4" />
                    Extract Entities ({pendingDocs.length})
                  </button>
                </div>
              )}

              {/* Fresh instance warning when trying to extract entities only */}
              {showFreshInstanceWarning && (
                <div className="mb-3 p-4 bg-yellow-500/10 border border-yellow-500/20 rounded-lg">
                  <div className="flex items-start gap-3">
                    <AlertCircle className="w-5 h-5 text-yellow-400 mt-0.5 flex-shrink-0" />
                    <div className="flex-1">
                      <p className="text-sm font-medium text-yellow-200 mb-1">Fresh Instance Detected</p>
                      <p className="text-sm text-yellow-200/80 mb-4">
                        This is a fresh instance with no existing entities. We recommend using <strong>Generate Graph</strong> which runs the entire multi-step pipeline — entity extraction, relationship analysis, and community detection — to fully build your knowledge graph in one go.
                      </p>
                      <div className="flex items-center gap-3">
                        <button
                          onClick={() => {
                            setShowFreshInstanceWarning(false);
                            handleRegenerateGraph();
                          }}
                          className="inline-flex items-center gap-2 px-4 py-2 bg-accent text-accent-foreground rounded-lg text-sm font-medium hover:bg-accent/90 transition-colors"
                        >
                          <RefreshCw className="w-4 h-4" />
                          Generate Graph
                        </button>
                        <button
                          onClick={() => {
                            setShowFreshInstanceWarning(false);
                            handleExtractEntities();
                          }}
                          className="inline-flex items-center gap-2 px-3 py-2 bg-muted hover:bg-muted/80 rounded-lg text-xs font-medium transition-colors text-muted-foreground"
                        >
                          Continue with Step 1 Only
                        </button>
                      </div>
                    </div>
                  </div>
                </div>
              )}

              {/* Aggregate task message — only when the per-document breakdown
                  above isn't showing (it tells the same story in more detail) */}
              {entityTaskMessage && processingDocs.length === 0 && analyzingImagesDocs.length === 0 && (
                <div className="flex items-center gap-2 mb-3 p-3 bg-accent/10 border border-accent/20 rounded-lg">
                  {isExtractingEntities && <Loader2 className="w-4 h-4 animate-spin text-accent" />}
                  <span className="text-sm text-accent">{entityTaskMessage}</span>
                </div>
              )}

              {entityCount > 0 && (
                <p className="text-sm text-green-400 mb-3">
                  {entityCount.toLocaleString()} entities and {(stats?.per_chunk_relationship_count ?? 0).toLocaleString()} relations extracted.
                </p>
              )}

            </div>
          </div>
        </div>

        {/* Connector */}
        <div className="flex justify-start ml-[18px]">
          <div className={cn(
            "w-0.5 h-6",
            step1Status === "complete" ? "bg-green-500/30" : "bg-border"
          )} />
        </div>

        {/* Step 2: Deep Relationship Analysis */}
        <div className={cn(
          "p-6 bg-card border rounded-xl transition-colors",
          step2Blocked ? "opacity-40 border-border" : step2Stale ? "border-yellow-500/30" : getStepBorder(step2Status)
        )}>
          <div className="flex items-start gap-4">
            <div className="flex-shrink-0 mt-0.5">
              {step2Stale ? <AlertCircle className="w-6 h-6 text-yellow-400" /> : getStepIcon(step2Status)}
            </div>
            <div className="flex-1 min-w-0">
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-3">
                  <h3 className="text-lg font-semibold">Step 2: Deep Relationship Analysis</h3>
                  <span className={cn(
                    "px-2 py-0.5 text-xs rounded-full font-medium",
                    step2Stale ? "bg-yellow-500/20 text-yellow-400" :
                    step2Status === "complete" ? "bg-green-500/20 text-green-400" :
                    step2Status === "in_progress" ? "bg-accent/20 text-accent" :
                    "bg-muted text-muted-foreground"
                  )}>
                    {step2Blocked ? "Waiting" : step2Skipped && step2Stale ? "Skipped" : step2Stale ? "Needs Update" : step2Status === "complete" ? "Complete" : step2Status === "in_progress" ? "Analyzing" : "Pending"}
                  </span>
                </div>
                {relationshipCount > 0 && (
                  <Link href="/explore?tab=relationships" className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-muted hover:bg-muted/80 rounded-lg text-xs font-medium transition-colors">
                    <ExternalLink className="w-3 h-3" />
                    Inspect
                  </Link>
                )}
              </div>

              {step2Blocked ? (
                <p className="text-sm text-muted-foreground">
                  Complete Step 1 first, then run deep analysis to discover cross-document relations.
                </p>
              ) : entityCount > 0 && relationshipCount === 0 && !analyzingRelationships ? (
                <div className="flex items-center justify-between mb-3 p-3 bg-yellow-500/10 border border-yellow-500/20 rounded-lg">
                  <div className="flex items-start gap-2">
                    <AlertCircle className="w-4 h-4 text-yellow-400 mt-0.5 flex-shrink-0" />
                    <p className="text-sm text-yellow-200">
                      Step 1 relations are extracted. Run deep analysis to discover additional cross-document relations.
                    </p>
                  </div>
                  <div className="flex items-center gap-2 shrink-0 ml-4">
                    <button
                      onClick={() => handleAnalyzeRelationships()}
                      className="inline-flex items-center gap-2 px-4 py-2 bg-accent text-accent-foreground rounded-lg text-sm font-medium hover:bg-accent/90 transition-colors"
                    >
                      <Share2 className="w-4 h-4" />
                      Analyze Relationships
                    </button>
                  </div>
                </div>
              ) : entityCount === 0 ? (
                <p className="text-sm text-muted-foreground">
                  Waiting for Step 1 to complete. Process your documents first, then run deep analysis here.
                </p>
              ) : relationshipCount > 0 && newDocsSinceAnalysis > 0 && !analyzingRelationships ? (
                <div className="mb-3 p-3 bg-yellow-500/10 border border-yellow-500/20 rounded-lg">
                  <div className="flex items-center justify-between">
                    <div className="flex items-start gap-2">
                      <AlertCircle className="w-4 h-4 text-yellow-400 mt-0.5 flex-shrink-0" />
                      <p className="text-sm text-yellow-200">
                        {newDocsSinceAnalysis} new document{newDocsSinceAnalysis !== 1 ? "s have" : " has"} been processed since the last analysis. New entities are not yet connected in the knowledge graph.
                      </p>
                    </div>
                    <div className="flex items-center gap-2 shrink-0 ml-4">
                      <button
                        onClick={() => handleAnalyzeRelationships()}
                        className="inline-flex items-center gap-2 px-4 py-2 bg-accent text-accent-foreground rounded-lg text-sm font-medium hover:bg-accent/90 transition-colors"
                      >
                        <Share2 className="w-4 h-4" />
                        Analyze Relationships
                      </button>
                      <button
                        onClick={() => {
                          if (confirm("This will delete cross-document relations and run a full deep analysis. Relations from Step 1 are preserved. Continue?")) {
                            handleAnalyzeRelationships(true);
                          }
                        }}
                        className="inline-flex items-center gap-1.5 px-3 py-2 bg-muted hover:bg-muted/80 rounded-lg text-xs font-medium transition-colors text-foreground"
                      >
                        <AlertCircle className="w-3 h-3" />
                        Rebuild
                      </button>
                      <button
                        onClick={() => setStep2Skipped(true)}
                        className="inline-flex items-center gap-1.5 px-3 py-2 bg-muted hover:bg-muted/80 rounded-lg text-xs font-medium transition-colors text-muted-foreground"
                      >
                        Skip
                      </button>
                    </div>
                  </div>
                </div>
              ) : (
                <p className="text-sm text-muted-foreground mb-4">
                  Relationship analysis examines how entities are connected across your documents, discovering links such as &quot;works at&quot;, &quot;located in&quot;, or &quot;related to&quot;.
                </p>
              )}

              {/* Task progress */}
              {relationshipTaskMessage && (
                <div className="mt-3 flex items-center gap-2 p-3 bg-accent/10 border border-accent/20 rounded-lg">
                  {analyzingRelationships && <Loader2 className="w-4 h-4 animate-spin text-accent" />}
                  <span className="text-sm text-accent">{relationshipTaskMessage}</span>
                </div>
              )}

              {relationshipCount > 0 && newDocsSinceAnalysis === 0 && !analyzingRelationships && !step2Blocked && (
                <div className="mt-3 space-y-2">
                  <div className="flex items-center gap-4">
                    <p className="text-sm text-green-400">
                      {(relationshipCount - (stats?.per_chunk_relationship_count ?? 0)).toLocaleString()} cross-document relations discovered.
                    </p>
                    {/* Entity-Relationship Ratio (ERR) Indicator */}
                    {stats && (stats.entity_count ?? 0) > 0 && (
                      <div className="relative group flex items-center gap-1.5">
                        <span className="text-xs text-muted-foreground">ERR</span>
                        <span className={cn(
                          "text-xs font-mono font-medium px-2 py-0.5 rounded",
                          (stats.entity_relationship_ratio ?? 0) >= 0.69
                            ? "bg-green-500/10 text-green-400"
                            : (stats.entity_relationship_ratio ?? 0) >= 0.29
                              ? "bg-yellow-500/10 text-yellow-400"
                              : "bg-red-500/10 text-red-400"
                        )}>
                          {(stats.entity_relationship_ratio ?? 0).toFixed(2)} / {(stats.relationship_target_ratio ?? 3).toFixed(1).replace(/\.0$/, '')}
                        </span>
                        {(stats.entity_relationship_ratio ?? 0) < 0.69 && (
                          <span className="text-xs text-muted-foreground">
                            — consider re-analyzing to reveal more relationships
                          </span>
                        )}
                        {/* Tooltip */}
                        <div className="absolute bottom-full left-0 mb-2 hidden group-hover:block z-50 pointer-events-none">
                          <div className="bg-popover border border-border rounded-lg shadow-lg px-3 py-2 text-xs w-64">
                            <p className="font-medium text-foreground mb-1">Entity-Relationship Ratio (ERR)</p>
                            <p className="text-muted-foreground leading-relaxed">
                              Average number of relationships per entity. A ratio of{" "}
                              <span className="font-mono text-foreground">{(stats.entity_relationship_ratio ?? 0).toFixed(2)}</span> means
                              each entity has ~{(stats.entity_relationship_ratio ?? 0).toFixed(2)} connections on average.
                              Target is{" "}
                              <span className="font-mono text-foreground">{(stats.relationship_target_ratio ?? 3).toFixed(1).replace(/\.0$/, '')}</span>.
                              {(stats.entity_relationship_ratio ?? 0) < (stats.relationship_target_ratio ?? 3)
                                ? " Run additional rounds of relationship analysis to improve graph connectivity."
                                : " Your graph is well-connected."}
                            </p>
                          </div>
                        </div>
                      </div>
                    )}
                  </div>
                  <div className="flex items-center gap-2">
                    <button
                      onClick={() => handleAnalyzeRelationships(false)}
                      className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-muted hover:bg-muted/80 rounded-lg text-xs font-medium transition-colors"
                    >
                      <RefreshCw className="w-3 h-3" />
                      Find more
                    </button>
                    <button
                      onClick={() => {
                        if (confirm("This will delete cross-document relations and run a full deep analysis. Relations from Step 1 are preserved. Continue?")) {
                          handleAnalyzeRelationships(true);
                        }
                      }}
                      className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-muted hover:bg-muted/80 rounded-lg text-xs font-medium transition-colors text-foreground"
                    >
                      <AlertCircle className="w-3 h-3" />
                      Rebuild from scratch
                    </button>
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>

        {/* Connector */}
        <div className="flex justify-start ml-[18px]">
          <div className={cn(
            "w-0.5 h-6",
            step2Status === "complete" ? "bg-green-500/30" : "bg-border"
          )} />
        </div>

        {/* Step 3: Community Detection */}
        <div className={cn(
          "p-6 bg-card border rounded-xl transition-colors",
          step3Blocked ? "opacity-40 border-border" : step3Stale ? "border-yellow-500/30" : getStepBorder(step3Status)
        )}>
          <div className="flex items-start gap-4">
            <div className="flex-shrink-0 mt-0.5">
              {step3Stale ? <AlertCircle className="w-6 h-6 text-yellow-400" /> : getStepIcon(step3Status)}
            </div>
            <div className="flex-1 min-w-0">
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-3">
                  <h3 className="text-lg font-semibold">Step 3: Community Detection</h3>
                  <span className={cn(
                    "px-2 py-0.5 text-xs rounded-full font-medium",
                    step3Stale ? "bg-yellow-500/20 text-yellow-400" :
                    step3Status === "complete" ? "bg-green-500/20 text-green-400" :
                    step3Status === "in_progress" ? "bg-accent/20 text-accent" :
                    "bg-muted text-muted-foreground"
                  )}>
                    {step3Blocked ? "Waiting" : step3Stale ? "Needs Update" : step3Status === "complete" ? "Complete" : step3Status === "in_progress" ? "Detecting" : "Pending"}
                  </span>
                </div>
                {communityCount > 0 && (
                  <Link href="/explore?tab=communities" className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-muted hover:bg-muted/80 rounded-lg text-xs font-medium transition-colors">
                    <ExternalLink className="w-3 h-3" />
                    Inspect
                  </Link>
                )}
              </div>

              {step3Blocked ? (
                <p className="text-sm text-muted-foreground">
                  Complete the previous steps first, then detect communities here.
                </p>
              ) : relationshipCount > 0 && communityCount === 0 && !detectingCommunities ? (
                <div className="flex items-center justify-between mb-3 p-3 bg-yellow-500/10 border border-yellow-500/20 rounded-lg">
                  <div className="flex items-start gap-2">
                    <AlertCircle className="w-4 h-4 text-yellow-400 mt-0.5 flex-shrink-0" />
                    <p className="text-sm text-yellow-200">
                      Relationships have been analyzed but no communities detected yet. Detect communities to organize your knowledge graph into meaningful groups.
                    </p>
                  </div>
                  <button
                    onClick={handleDetectCommunities}
                    className="inline-flex items-center gap-2 px-4 py-2 bg-accent text-accent-foreground rounded-lg text-sm font-medium hover:bg-accent/90 transition-colors shrink-0 ml-4"
                  >
                    <Users className="w-4 h-4" />
                    Detect Communities
                  </button>
                </div>
              ) : step3Stale ? (
                <div className="flex items-center justify-between mb-3 p-3 bg-yellow-500/10 border border-yellow-500/20 rounded-lg">
                  <div className="flex items-start gap-2">
                    <AlertCircle className="w-4 h-4 text-yellow-400 mt-0.5 flex-shrink-0" />
                    <p className="text-sm text-yellow-200">
                      {communitiesStaleFromMerge && !communitiesStale
                        ? "Entities have been deduplicated since communities were last detected. Re-detect to update community groupings."
                        : communitiesStaleFromMerge && communitiesStale
                          ? "Relationships and entity deduplication have changed since communities were last detected. Re-detect to update community groupings."
                          : "Relationships have been updated since communities were last detected. Re-detect to update community groupings."}
                    </p>
                  </div>
                  <button
                    onClick={handleDetectCommunities}
                    className="inline-flex items-center gap-2 px-4 py-2 bg-accent text-accent-foreground rounded-lg text-sm font-medium hover:bg-accent/90 transition-colors shrink-0 ml-4"
                  >
                    <Users className="w-4 h-4" />
                    Re-detect Communities
                  </button>
                </div>
              ) : (
                <p className="text-sm text-muted-foreground mb-4">
                  Communities are clusters of closely related entities discovered through graph analysis. They help organize your knowledge base and improve search quality.
                </p>
              )}

              {/* Task progress */}
              {communityTaskMessage && (
                <div className="mt-3 flex items-center gap-2 p-3 bg-accent/10 border border-accent/20 rounded-lg">
                  {detectingCommunities && <Loader2 className="w-4 h-4 animate-spin text-accent" />}
                  <span className="text-sm text-accent">{communityTaskMessage}</span>
                </div>
              )}

              {communityCount > 0 && !step3Stale && !detectingCommunities && (
                <div className="flex items-center gap-4 mt-3">
                  <p className="text-sm text-green-400">
                    {communityCount} communit{communityCount !== 1 ? "ies" : "y"} detected.
                  </p>
                  <button
                    onClick={handleDetectCommunities}
                    className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-muted hover:bg-muted/80 rounded-lg text-xs font-medium transition-colors"
                  >
                    <RefreshCw className="w-3 h-3" />
                    Re-detect
                  </button>
                </div>
              )}
            </div>
          </div>
        </div>

        {/* Connector */}
        <div className="flex justify-start ml-[18px]">
          <div className={cn(
            "w-0.5 h-6",
            step3Status === "complete" ? "bg-green-500/30" : "bg-border"
          )} />
        </div>

        {/* Inspect Knowledge Graph */}
        <Link
          href="/explore?tab=graph"
          className="block p-6 bg-card border border-border rounded-xl hover:border-accent/50 transition-colors group"
        >
          <div className="flex items-center gap-4">
            <div className="flex-shrink-0">
              <Network className="w-6 h-6 text-muted-foreground group-hover:text-accent transition-colors" />
            </div>
            <div className="flex-1 min-w-0">
              <h3 className="text-lg font-semibold group-hover:text-accent transition-colors">Inspect Knowledge Graph</h3>
              <p className="text-sm text-muted-foreground">
                Explore and visualize your generated knowledge graph — browse entities, relationships, and communities interactively.
              </p>
            </div>
            <ArrowRight className="w-5 h-5 text-muted-foreground group-hover:text-accent transition-colors" />
          </div>
        </Link>

        {/* Regenerate Graph — below Inspect, only when graph exists and no flow is running */}
        {entityCount > 0 && !isRegenerating && (
          <div className="flex justify-end mt-4">
            <button
              onClick={handleRegenerateGraph}
              disabled={isExtractingEntities || analyzingRelationships || detectingCommunities}
              className={cn(
                "inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-all",
                isExtractingEntities || analyzingRelationships || detectingCommunities
                  ? "bg-muted/50 text-muted-foreground border border-border cursor-not-allowed opacity-50"
                  : "bg-muted hover:bg-muted/80 border border-border hover:border-accent/30"
              )}
            >
              <AlertCircle className="w-4 h-4" />
              Regenerate Graph
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
