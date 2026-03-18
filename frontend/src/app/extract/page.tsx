"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import type { Document, Stats } from "@/types";
import {
  Loader2,
  FileText,
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
} from "lucide-react";
import { cn } from "@/lib/utils";

type StepStatus = "pending" | "in_progress" | "complete";

export default function ExtractAnalyzePage() {
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

  // Check for running relationship analysis tasks on mount
  useEffect(() => {
    fetchData();

    const checkRunningTasks = async () => {
      try {
        const { tasks } = await api.listTasks("running", "relationship_analysis");
        if (tasks.length > 0) {
          const task = tasks[0];
          setAnalyzingRelationships(true);
          setRelationshipTaskMessage(task.message || "Relationship analysis in progress...");
          pollRelationshipTask(task.task_id);
        }
      } catch {
        // No running tasks
      }

      try {
        const { tasks } = await api.listTasks("running", "community_detection");
        if (tasks.length > 0) {
          const task = tasks[0];
          setDetectingCommunities(true);
          setCommunityTaskMessage(task.message || "Community detection in progress...");
          pollCommunityTask(task.task_id);
        }
      } catch {
        // No running tasks
      }
    };

    checkRunningTasks();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fetchData]);

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

  // Detect if communities are stale (relationship analysis ran after last community detection)
  useEffect(() => {
    const communityCount = stats?.community_count ?? 0;
    const lastAnalysis = stats?.last_relationship_analysis_at;
    const lastDetection = stats?.last_community_detection_at;

    if (communityCount === 0 || !lastAnalysis) {
      setCommunitiesStale(false);
      return;
    }

    // No detection timestamp means communities pre-date tracking — treat as stale
    if (!lastDetection) {
      setCommunitiesStale(true);
      return;
    }

    const analysisDate = new Date(lastAnalysis).getTime();
    const detectionDate = new Date(lastDetection).getTime();
    setCommunitiesStale(analysisDate > detectionDate);
  }, [stats]);

  // Auto-refresh stats while tasks are running
  useEffect(() => {
    if (!analyzingRelationships && !detectingCommunities) return;
    const interval = setInterval(() => fetchData(true), 5000);
    return () => clearInterval(interval);
  }, [analyzingRelationships, detectingCommunities, fetchData]);

  const pollRelationshipTask = useCallback(async (taskId: string) => {
    try {
      const status = await api.getTaskStatus(taskId);
      const statsData = await api.getStats();
      setStats(statsData);

      const currentRels = statsData.relationship_count ?? 0;
      const newlyDiscovered = currentRels - initialRelCount.current;
      if (newlyDiscovered > 0) setDiscoveredRelCount(newlyDiscovered);

      const backendMsg = status.message || `Progress: ${status.progress_percent}%`;
      const countMsg = newlyDiscovered > 0
        ? `${newlyDiscovered} new relationships found, still connecting the dots... (${backendMsg})`
        : backendMsg;
      setRelationshipTaskMessage(countMsg);

      if (status.status === "completed") {
        setRelationshipTaskMessage(`Analysis complete! ${currentRels - initialRelCount.current} relationships discovered.`);
        setNewDocsSinceAnalysis(0);
        await fetchData(true);
        setTimeout(() => {
          setRelationshipTaskMessage(null);
          setAnalyzingRelationships(false);
          setDiscoveredRelCount(0);
        }, 3000);
      } else if (status.status === "failed") {
        setRelationshipTaskMessage(`Failed: ${status.message}`);
        setAnalyzingRelationships(false);
      } else {
        setTimeout(() => pollRelationshipTask(taskId), 2000);
      }
    } catch {
      setRelationshipTaskMessage(null);
      setAnalyzingRelationships(false);
    }
  }, [fetchData]);

  const handleAnalyzeRelationships = async (rebuild = false) => {
    try {
      setAnalyzingRelationships(true);
      setDiscoveredRelCount(0);
      initialRelCount.current = rebuild ? 0 : (stats?.relationship_count ?? 0);
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
    try {
      const status = await api.getTaskStatus(taskId);

      const progressMsg = status.message || `Progress: ${status.progress_percent}%`;
      setCommunityTaskMessage(progressMsg);

      if (status.status === "completed") {
        const communityCount = (status.result as Record<string, unknown>)?.total ?? 0;
        setCommunityTaskMessage(`Communities detected successfully! ${communityCount} communities found.`);
        setCommunitiesStale(false);
        await fetchData(true);
        setTimeout(() => {
          setCommunityTaskMessage(null);
          setDetectingCommunities(false);
        }, 3000);
      } else if (status.status === "failed") {
        setCommunityTaskMessage(`Failed: ${status.message}`);
        setDetectingCommunities(false);
      } else {
        setTimeout(() => pollCommunityTask(taskId), 2000);
      }
    } catch {
      setCommunityTaskMessage(null);
      setDetectingCommunities(false);
    }
  }, [fetchData]);

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
    try {
      await api.processPendingDocuments();
      await fetchData(true);
    } catch (error) {
      console.error("Failed to start processing:", error);
    } finally {
      setIsExtractingEntities(false);
    }
  };

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
  const pendingCount = stats?.pending_count ?? 0;

  const processingDocs = documents.filter(
    (d) => d.processing_status === "processing" || d.processing_status === "extracting"
  );
  const completedDocs = documents.filter((d) => d.processing_status === "completed");
  const failedDocs = documents.filter((d) => d.processing_status === "failed");
  const pendingDocs = documents.filter((d) => d.processing_status === "pending");

  // Step 1: Entity Extraction
  const step1Stale = entityCount > 0 && pendingDocs.length > 0 && processingDocs.length === 0 && !isExtractingEntities;
  const step1Status: StepStatus =
    processingDocs.length > 0 || documents.some((d) => d.processing_status === "extracting") || isExtractingEntities
      ? "in_progress"
      : entityCount > 0 && !step1Stale
        ? "complete"
        : "pending";

  // Step 2: Relationship Analysis
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
  const step3Blocked = step2Blocked || step2Status !== "complete";
  const step3Stale = !step3Blocked && communityCount > 0 && communitiesStale && !detectingCommunities;
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
      {/* Pipeline Steps */}
      <div className="space-y-4">
        {/* Step 1: Entity Extraction */}
        <div className={cn("p-6 bg-card border rounded-xl transition-colors", step1Stale ? "border-yellow-500/30" : getStepBorder(step1Status))}>
          <div className="flex items-start gap-4">
            <div className="flex-shrink-0 mt-0.5">
              {step1Stale ? <AlertCircle className="w-6 h-6 text-yellow-400" /> : getStepIcon(step1Status)}
            </div>
            <div className="flex-1 min-w-0">
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-3">
                  <h3 className="text-lg font-semibold">Step 1: Entity Extraction</h3>
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
                Entities are automatically extracted when documents are processed. Upload documents and process them to extract entities like people, organizations, concepts, and more.
              </p>

              {/* Document processing summary */}
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-4">
                <div className="p-3 bg-muted/50 rounded-lg">
                  <p className="text-lg font-semibold">{completedDocs.length}</p>
                  <p className="text-xs text-muted-foreground">Processed</p>
                </div>
                <div className="p-3 bg-muted/50 rounded-lg">
                  <p className="text-lg font-semibold">{processingDocs.length}</p>
                  <p className="text-xs text-muted-foreground">Processing</p>
                </div>
                <div className="p-3 bg-muted/50 rounded-lg">
                  <p className="text-lg font-semibold">{pendingDocs.length + pendingCount}</p>
                  <p className="text-xs text-muted-foreground">Pending</p>
                </div>
                <div className="p-3 bg-muted/50 rounded-lg">
                  <p className="text-lg font-semibold">{failedDocs.length}</p>
                  <p className="text-xs text-muted-foreground">Failed</p>
                </div>
              </div>

              {processingDocs.length > 0 && (
                <div className="flex items-center gap-2 mb-3 p-3 bg-accent/10 border border-accent/20 rounded-lg">
                  <Loader2 className="w-4 h-4 animate-spin text-accent" />
                  <span className="text-sm text-accent">
                    Processing {processingDocs.length} document{processingDocs.length !== 1 ? "s" : ""} in parallel... Entities are being extracted... {processingDocs.length + pendingDocs.length} document{processingDocs.length + pendingDocs.length !== 1 ? "s" : ""} remaining...
                  </span>
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
                    onClick={handleExtractEntities}
                    className="inline-flex items-center gap-2 px-4 py-2 bg-accent text-accent-foreground rounded-lg text-sm font-medium hover:bg-accent/90 transition-colors shrink-0 ml-4"
                  >
                    <Layers className="w-4 h-4" />
                    Extract Entities ({pendingDocs.length})
                  </button>
                </div>
              )}

              {isExtractingEntities && (
                <div className="flex items-center gap-2 mb-3 p-3 bg-accent/10 border border-accent/20 rounded-lg">
                  <Loader2 className="w-4 h-4 animate-spin text-accent" />
                  <span className="text-sm text-accent">Starting entity extraction...</span>
                </div>
              )}

              {entityCount > 0 && (
                <p className="text-sm text-green-400 mb-3">
                  {entityCount} entities extracted from {completedDocs.length} document{completedDocs.length !== 1 ? "s" : ""}.
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

        {/* Step 2: Relationship Analysis */}
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
                  <h3 className="text-lg font-semibold">Step 2: Relationship Analysis</h3>
                  <span className={cn(
                    "px-2 py-0.5 text-xs rounded-full font-medium",
                    step2Stale ? "bg-yellow-500/20 text-yellow-400" :
                    step2Status === "complete" ? "bg-green-500/20 text-green-400" :
                    step2Status === "in_progress" ? "bg-accent/20 text-accent" :
                    "bg-muted text-muted-foreground"
                  )}>
                    {step2Blocked ? "Waiting" : step2Stale ? "Needs Update" : step2Status === "complete" ? "Complete" : step2Status === "in_progress" ? "Analyzing" : "Pending"}
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
                  Complete Step 1 first to extract entities, then analyze relationships here.
                </p>
              ) : entityCount > 0 && relationshipCount === 0 && !analyzingRelationships ? (
                <div className="flex items-center justify-between mb-3 p-3 bg-yellow-500/10 border border-yellow-500/20 rounded-lg">
                  <div className="flex items-start gap-2">
                    <AlertCircle className="w-4 h-4 text-yellow-400 mt-0.5 flex-shrink-0" />
                    <p className="text-sm text-yellow-200">
                      Entities have been extracted but relationships haven&apos;t been analyzed yet. Analyze to discover how entities connect across your documents.
                    </p>
                  </div>
                  <button
                    onClick={() => handleAnalyzeRelationships()}
                    className="inline-flex items-center gap-2 px-4 py-2 bg-accent text-accent-foreground rounded-lg text-sm font-medium hover:bg-accent/90 transition-colors shrink-0 ml-4"
                  >
                    <Share2 className="w-4 h-4" />
                    Analyze Relationships
                  </button>
                </div>
              ) : entityCount === 0 ? (
                <p className="text-sm text-muted-foreground">
                  Waiting for entities to be extracted first. Process your documents in Step 1 to extract entities, then come back here to analyze relationships.
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
                          if (confirm("This will delete all existing relationships and rebuild from scratch. Continue?")) {
                            handleAnalyzeRelationships(true);
                          }
                        }}
                        className="inline-flex items-center gap-1.5 px-3 py-2 bg-muted hover:bg-muted/80 rounded-lg text-xs font-medium transition-colors text-yellow-400"
                      >
                        <AlertCircle className="w-3 h-3" />
                        Rebuild
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

              {relationshipCount > 0 && newDocsSinceAnalysis === 0 && !analyzingRelationships && (
                <div className="flex items-center gap-4 mt-3">
                  <p className="text-sm text-green-400">
                    {relationshipCount} relationships discovered.
                  </p>
                  <button
                    onClick={() => handleAnalyzeRelationships(false)}
                    className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-muted hover:bg-muted/80 rounded-lg text-xs font-medium transition-colors"
                  >
                    <RefreshCw className="w-3 h-3" />
                    Re-analyze
                  </button>
                  <button
                    onClick={() => {
                      if (confirm("This will delete all existing relationships and rebuild from scratch. Continue?")) {
                        handleAnalyzeRelationships(true);
                      }
                    }}
                    className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-muted hover:bg-muted/80 rounded-lg text-xs font-medium transition-colors text-yellow-400"
                  >
                    <AlertCircle className="w-3 h-3" />
                    Rebuild from scratch
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
                      Relationships have been updated since communities were last detected. Re-detect to update community groupings.
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
      </div>
    </div>
  );
}
