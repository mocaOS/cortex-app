"use client";

import { useState, useEffect } from "react";
import { AnimatePresence } from "framer-motion";
import {
  FileText,
  Loader2,
  AlertCircle,
  Search,
} from "lucide-react";
import { api } from "@/lib/api";
import type { Collection } from "@/types";
import { DocumentCard, DocumentFilters, DocumentBulkActions } from "./documents";

interface Document {
  id: string;
  filename: string;
  file_type: string;
  file_size: number;
  file_path?: string | null;
  upload_date: string;
  chunk_count: number;
  processing_status: string;
  error_message?: string;
  progress_current?: number;
  progress_total?: number;
  progress_message?: string;
  collection_id?: string | null;
  collection_name?: string | null;
}

interface DocumentListProps {
  onDelete: () => void;
}

const isProcessing = (status: string) => {
  return status === "processing" || status === "extracting" || status === "pending";
};

export default function DocumentList({ onDelete }: DocumentListProps) {
  const [documents, setDocuments] = useState<Document[]>([]);
  const [collections, setCollections] = useState<Collection[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [isReprocessing, setIsReprocessing] = useState(false);
  const [reprocessingIds, setReprocessingIds] = useState<Set<string>>(new Set());
  const [isDeletingSelected, setIsDeletingSelected] = useState(false);
  const [filterCollectionId, setFilterCollectionId] = useState<string | null>(null);
  const [filterStatus, setFilterStatus] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [isMoving, setIsMoving] = useState(false);

  const fetchDocuments = async () => {
    try {
      const res = await fetch("/api/documents");
      if (res.ok) {
        const data = await res.json();
        setDocuments(data.documents);
      }
    } catch (error) {
      console.error("Failed to fetch documents:", error);
    } finally {
      setIsLoading(false);
    }
  };

  const fetchCollections = async () => {
    try {
      const data = await api.getCollections();
      setCollections(data.collections);
    } catch (error) {
      console.error("Failed to fetch collections:", error);
    }
  };

  useEffect(() => {
    fetchDocuments();
    fetchCollections();
    const interval = setInterval(fetchDocuments, 5000);
    return () => clearInterval(interval);
  }, []);

  // Sync selected IDs with existing documents
  useEffect(() => {
    const docIds = new Set(documents.map((d) => d.id));
    setSelectedIds((prev) => {
      const newSet = new Set<string>();
      prev.forEach((id) => {
        if (docIds.has(id)) newSet.add(id);
      });
      return newSet;
    });
  }, [documents]);

  // Filter documents
  const filteredDocuments = documents.filter((doc) => {
    const matchesCollection =
      filterCollectionId === null ||
      (filterCollectionId === "none" ? !doc.collection_id : doc.collection_id === filterCollectionId);

    const matchesStatus = (() => {
      if (filterStatus === null) return true;
      if (filterStatus === "in_progress") {
        return doc.processing_status === "processing" || doc.processing_status === "extracting";
      }
      return doc.processing_status === filterStatus;
    })();

    const matchesSearch =
      searchQuery.trim() === "" ||
      doc.filename.toLowerCase().includes(searchQuery.toLowerCase().trim());

    return matchesCollection && matchesStatus && matchesSearch;
  });

  // Status counts
  const statusCounts = {
    completed: documents.filter((d) => d.processing_status === "completed").length,
    in_progress: documents.filter((d) => ["processing", "extracting"].includes(d.processing_status)).length,
    pending: documents.filter((d) => d.processing_status === "pending").length,
    failed: documents.filter((d) => d.processing_status === "failed").length,
  };

  const availableTargetCollections = collections.filter(
    (c) => filterCollectionId === null || filterCollectionId === "none" || c.id !== filterCollectionId
  );

  const failedDocuments = filteredDocuments.filter((d) => d.processing_status === "failed");
  const inProgressDocuments = filteredDocuments.filter((d) => isProcessing(d.processing_status));
  const selectedInProgressCount = documents.filter(
    (d) => selectedIds.has(d.id) && isProcessing(d.processing_status) && d.file_path
  ).length;
  const allFilteredSelected = filteredDocuments.length > 0 && filteredDocuments.every((d) => selectedIds.has(d.id));

  // Action handlers
  const handleDelete = async (id: string) => {
    if (!confirm("Are you sure you want to delete this document?")) return;

    setDeletingId(id);
    try {
      const res = await fetch(`/api/documents/${id}`, { method: "DELETE" });
      if (res.ok) {
        setDocuments((prev) => prev.filter((d) => d.id !== id));
        setSelectedIds((prev) => {
          const newSet = new Set(prev);
          newSet.delete(id);
          return newSet;
        });
        onDelete();
      }
    } catch (error) {
      console.error("Failed to delete document:", error);
    } finally {
      setDeletingId(null);
    }
  };

  const toggleSelection = (id: string) => {
    setSelectedIds((prev) => {
      const newSet = new Set(prev);
      if (newSet.has(id)) {
        newSet.delete(id);
      } else {
        newSet.add(id);
      }
      return newSet;
    });
  };

  const toggleSelectAll = () => {
    const filteredIds = new Set(filteredDocuments.map((d) => d.id));
    const allSelected = filteredDocuments.every((d) => selectedIds.has(d.id));

    if (allSelected) {
      setSelectedIds((prev) => {
        const newSet = new Set(prev);
        filteredIds.forEach((id) => newSet.delete(id));
        return newSet;
      });
    } else {
      setSelectedIds((prev) => new Set([...prev, ...filteredIds]));
    }
  };

  const selectAllFailed = () => {
    const failedIds = filteredDocuments
      .filter((d) => d.processing_status === "failed")
      .map((d) => d.id);
    setSelectedIds(new Set(failedIds));
  };

  const selectInProgress = () => {
    setSelectedIds(new Set(inProgressDocuments.map((d) => d.id)));
  };

  const handleMoveToCollection = async (targetCollectionId: string) => {
    if (selectedIds.size === 0) return;

    const selectedArray = Array.from(selectedIds);
    const targetCollection = collections.find((c) => c.id === targetCollectionId);

    const confirmed = confirm(
      `Move ${selectedIds.size} document(s) to "${targetCollection?.name || 'collection'}"?`
    );
    if (!confirmed) return;

    setIsMoving(true);
    try {
      await api.moveDocumentsToCollection(selectedArray, targetCollectionId);
      setSelectedIds(new Set());
      await fetchDocuments();
      await fetchCollections();
      onDelete();
    } catch (error) {
      console.error("Failed to move documents:", error);
      alert("Failed to move documents");
    } finally {
      setIsMoving(false);
    }
  };

  const handleReprocessSelected = async () => {
    if (selectedIds.size === 0) return;

    const confirmed = confirm(
      `Reprocess ${selectedIds.size} document(s)? Original files are stored - no re-upload needed.`
    );
    if (!confirmed) return;

    setIsReprocessing(true);
    try {
      const result = await api.reprocessDocuments(Array.from(selectedIds));
      const errors = result.results.filter((r: { status: string }) => r.status === "error");
      if (errors.length > 0) {
        const errorMsgs = errors.map((e: { document_id: string; message: string }) => `${e.document_id}: ${e.message}`).join("\n");
        alert(`Some documents failed to queue:\n${errorMsgs}`);
      }
      setSelectedIds(new Set());
      await fetchDocuments();
      onDelete();
    } catch (error) {
      console.error("Failed to reprocess documents:", error);
      alert(`Failed to reprocess: ${error instanceof Error ? error.message : "Unknown error"}`);
    } finally {
      setIsReprocessing(false);
    }
  };

  const handleRestartSelected = async () => {
    const inProgressSelected = documents.filter(
      (d) => selectedIds.has(d.id) && isProcessing(d.processing_status) && d.file_path
    );

    if (inProgressSelected.length === 0) return;

    const confirmed = confirm(
      `Restart ${inProgressSelected.length} in-progress document(s)?`
    );
    if (!confirmed) return;

    setIsReprocessing(true);
    try {
      const docIds = inProgressSelected.map((d) => d.id);
      await api.reprocessDocuments(docIds);
      setSelectedIds(new Set());
      await fetchDocuments();
      onDelete();
    } catch (error) {
      console.error("Failed to restart documents:", error);
      alert(`Failed to restart: ${error instanceof Error ? error.message : "Unknown error"}`);
    } finally {
      setIsReprocessing(false);
    }
  };

  const handleDeleteSelected = async () => {
    if (selectedIds.size === 0) return;

    const confirmed = confirm(
      `Are you sure you want to delete ${selectedIds.size} document(s)?`
    );
    if (!confirmed) return;

    setIsDeletingSelected(true);
    try {
      const res = await fetch("/api/documents/delete", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ document_ids: Array.from(selectedIds) }),
      });

      if (res.ok) {
        setSelectedIds(new Set());
        await fetchDocuments();
        onDelete();
      }
    } catch (error) {
      console.error("Failed to delete documents:", error);
    } finally {
      setIsDeletingSelected(false);
    }
  };

  const handleReprocessWithFile = async (docId: string, file: File) => {
    setReprocessingIds((prev) => new Set(prev).add(docId));
    try {
      await api.reprocessDocument(docId, file);
      await fetchDocuments();
      onDelete();
    } catch (error) {
      console.error("Failed to reprocess document:", error);
      alert(`Reprocess failed: ${error instanceof Error ? error.message : "Unknown error"}`);
    } finally {
      setReprocessingIds((prev) => {
        const newSet = new Set(prev);
        newSet.delete(docId);
        return newSet;
      });
    }
  };

  const handleReprocessDocument = async (docId: string) => {
    setReprocessingIds((prev) => new Set(prev).add(docId));
    try {
      await api.reprocessDocument(docId);
      await fetchDocuments();
      onDelete();
    } catch (error) {
      console.error("Failed to reprocess document:", error);
      alert(`Reprocess failed: ${error instanceof Error ? error.message : "Unknown error"}`);
    } finally {
      setReprocessingIds((prev) => {
        const newSet = new Set(prev);
        newSet.delete(docId);
        return newSet;
      });
    }
  };

  const handleRestartDocument = async (docId: string) => {
    const confirmed = confirm("Restart this document?");
    if (!confirmed) return;

    setReprocessingIds((prev) => new Set(prev).add(docId));
    try {
      await api.reprocessDocument(docId);
      await fetchDocuments();
      onDelete();
    } catch (error) {
      console.error("Failed to restart document:", error);
      alert(`Restart failed: ${error instanceof Error ? error.message : "Unknown error"}`);
    } finally {
      setReprocessingIds((prev) => {
        const newSet = new Set(prev);
        newSet.delete(docId);
        return newSet;
      });
    }
  };

  // Loading state
  if (isLoading) {
    return (
      <div className="glass rounded-lg p-12 text-center">
        <Loader2 className="w-8 h-8 text-foreground animate-spin mx-auto mb-4" />
        <p className="text-muted-foreground">Loading documents...</p>
      </div>
    );
  }

  // Empty state
  if (documents.length === 0) {
    return (
      <div className="glass rounded-lg p-12 text-center">
        <div className="w-16 h-16 mx-auto rounded-lg bg-accent/20 flex items-center justify-center mb-6">
          <FileText className="w-8 h-8 text-accent" />
        </div>
        <h3 className="text-lg font-medium text-foreground mb-2">
          No Documents Yet
        </h3>
        <p className="text-muted-foreground max-w-md mx-auto">
          Upload your first document to start building your knowledge base.
        </p>
      </div>
    );
  }

  const hasFilters = filterCollectionId !== null || filterStatus !== null || searchQuery.trim() !== "";

  return (
    <div className="space-y-4">
      {/* Filters */}
      <DocumentFilters
        searchQuery={searchQuery}
        onSearchChange={setSearchQuery}
        filterCollectionId={filterCollectionId}
        onCollectionFilterChange={setFilterCollectionId}
        filterStatus={filterStatus}
        onStatusFilterChange={setFilterStatus}
        collections={collections}
        documents={documents}
        statusCounts={statusCounts}
      />

      {/* Bulk Actions */}
      <DocumentBulkActions
        selectedCount={selectedIds.size}
        totalCount={documents.length}
        filteredCount={filteredDocuments.length}
        allFilteredSelected={allFilteredSelected}
        failedCount={failedDocuments.length}
        inProgressCount={inProgressDocuments.length}
        selectedInProgressCount={selectedInProgressCount}
        isReprocessing={isReprocessing}
        isDeletingSelected={isDeletingSelected}
        isMoving={isMoving}
        availableTargetCollections={availableTargetCollections}
        hasFilters={hasFilters}
        onToggleSelectAll={toggleSelectAll}
        onSelectFailed={selectAllFailed}
        onSelectInProgress={selectInProgress}
        onReprocessSelected={handleReprocessSelected}
        onRestartSelected={handleRestartSelected}
        onDeleteSelected={handleDeleteSelected}
        onMoveToCollection={handleMoveToCollection}
        onRefresh={fetchDocuments}
      />

      {/* Failed documents summary */}
      {failedDocuments.length > 0 && (
        <div className="glass rounded-lg p-4 border border-destructive/20 bg-destructive/5">
          <div className="flex items-center gap-3">
            <AlertCircle className="w-5 h-5 text-destructive shrink-0" />
            <div className="flex-1 min-w-0">
              <h4 className="text-sm font-medium text-destructive">
                {failedDocuments.length} document{failedDocuments.length !== 1 ? "s" : ""} failed
              </h4>
              <p className="text-xs text-muted-foreground mt-0.5">
                Click reprocess to retry, or select multiple and use bulk actions.
              </p>
            </div>
          </div>
        </div>
      )}

      {/* No results */}
      {filteredDocuments.length === 0 && hasFilters && (
        <div className="glass rounded-lg p-8 text-center">
          <Search className="w-12 h-12 text-muted-foreground mx-auto mb-4" />
          <h3 className="text-lg font-medium text-foreground mb-2">No Documents Found</h3>
          <p className="text-muted-foreground">
            No documents match your current filters.
          </p>
        </div>
      )}

      {/* Document list */}
      <div className="grid gap-3">
        <AnimatePresence>
          {filteredDocuments.map((doc, index) => (
            <DocumentCard
              key={doc.id}
              doc={doc}
              index={index}
              isSelected={selectedIds.has(doc.id)}
              onToggleSelection={toggleSelection}
              onDelete={handleDelete}
              onReprocess={handleReprocessDocument}
              onReprocessWithFile={handleReprocessWithFile}
              onRestart={handleRestartDocument}
              isDeleting={deletingId === doc.id}
              isReprocessing={reprocessingIds.has(doc.id)}
            />
          ))}
        </AnimatePresence>
      </div>
    </div>
  );
}
