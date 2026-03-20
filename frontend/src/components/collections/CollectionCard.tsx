"use client";

import { useState, useRef, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  FolderOpen,
  Trash2,
  Pencil,
  Check,
  X,
  Loader2,
  FileText,
  Network,
  ChevronRight,
  ChevronDown,
} from "lucide-react";
import type { Collection, CollectionEntity } from "@/types";

interface CollectionCardProps {
  collection: Collection;
  index: number;
  isExpanded: boolean;
  isDeleting: boolean;
  isLoadingEntities: boolean;
  entities: CollectionEntity[];
  onToggleExpand: () => void;
  onDelete: () => void;
  onRename: (name: string) => Promise<void>;
}

export default function CollectionCard({
  collection,
  index,
  isExpanded,
  isDeleting,
  isLoadingEntities,
  entities,
  onToggleExpand,
  onDelete,
  onRename,
}: CollectionCardProps) {
  const [isEditing, setIsEditing] = useState(false);
  const [editName, setEditName] = useState(collection.name);
  const [isSaving, setIsSaving] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (isEditing) {
      inputRef.current?.focus();
      inputRef.current?.select();
    }
  }, [isEditing]);

  const handleSave = async () => {
    const trimmed = editName.trim();
    if (!trimmed || trimmed === collection.name) {
      setIsEditing(false);
      setEditName(collection.name);
      return;
    }
    setIsSaving(true);
    try {
      await onRename(trimmed);
      setIsEditing(false);
    } catch {
      setEditName(collection.name);
      setIsEditing(false);
    } finally {
      setIsSaving(false);
    }
  };

  const handleCancel = () => {
    setEditName(collection.name);
    setIsEditing(false);
  };

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: index * 0.05 }}
      className="glass rounded-lg overflow-hidden"
    >
      <div
        className="p-4 cursor-pointer hover:bg-muted/30 transition-colors"
        onClick={isEditing ? undefined : onToggleExpand}
      >
        <div className="flex items-start gap-4">
          <div className="w-10 h-10 rounded-lg bg-accent/20 flex items-center justify-center shrink-0">
            <FolderOpen className="w-5 h-5 text-accent" />
          </div>

          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2">
              {isEditing ? (
                <div className="flex items-center gap-1.5 flex-1 min-w-0" onClick={(e) => e.stopPropagation()}>
                  <input
                    ref={inputRef}
                    type="text"
                    value={editName}
                    onChange={(e) => setEditName(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") handleSave();
                      if (e.key === "Escape") handleCancel();
                    }}
                    className="flex-1 min-w-0 px-2 py-1 bg-card rounded text-sm font-medium text-foreground border border-border focus:border-foreground focus:outline-none"
                    disabled={isSaving}
                  />
                  <button
                    onClick={handleSave}
                    disabled={isSaving || !editName.trim()}
                    className="p-1 rounded text-accent hover:bg-accent/10 disabled:opacity-50"
                  >
                    {isSaving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Check className="w-4 h-4" />}
                  </button>
                  <button
                    onClick={handleCancel}
                    disabled={isSaving}
                    className="p-1 rounded text-muted-foreground hover:text-foreground hover:bg-muted"
                  >
                    <X className="w-4 h-4" />
                  </button>
                </div>
              ) : (
                <>
                  <h3 className="font-medium text-foreground truncate">
                    {collection.name}
                  </h3>
                  {isExpanded ? (
                    <ChevronDown className="w-4 h-4 text-muted-foreground" />
                  ) : (
                    <ChevronRight className="w-4 h-4 text-muted-foreground" />
                  )}
                </>
              )}
            </div>
            {collection.description && !isEditing && (
              <p className="text-sm text-muted-foreground mt-1 line-clamp-1">
                {collection.description}
              </p>
            )}
            <div className="flex items-center gap-4 mt-2">
              <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
                <FileText className="w-3.5 h-3.5" />
                {collection.document_count} documents
              </span>
              <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
                <Network className="w-3.5 h-3.5" />
                {collection.entity_count} entities
              </span>
            </div>
          </div>

          {collection.id !== "default" && !isEditing && (
            <div className="flex items-center gap-1" onClick={(e) => e.stopPropagation()}>
              <button
                onClick={() => setIsEditing(true)}
                className="p-2 rounded-lg text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
                title="Rename collection"
              >
                <Pencil className="w-4 h-4" />
              </button>
              <button
                onClick={onDelete}
                disabled={isDeleting}
                className="p-2 rounded-lg text-muted-foreground hover:text-destructive hover:bg-destructive/10 transition-colors"
                title="Delete collection (documents move to default)"
              >
                {isDeleting ? (
                  <Loader2 className="w-4 h-4 animate-spin" />
                ) : (
                  <Trash2 className="w-4 h-4" />
                )}
              </button>
            </div>
          )}
        </div>
      </div>

      <AnimatePresence>
        {isExpanded && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            className="overflow-hidden border-t border-border"
          >
            <div className="p-4 bg-card/30">
              <h4 className="text-xs font-medium text-muted-foreground uppercase tracking-wider mb-3">
                Top Entities
              </h4>

              {isLoadingEntities ? (
                <div className="flex items-center justify-center py-4">
                  <Loader2 className="w-5 h-5 text-accent animate-spin" />
                </div>
              ) : entities.length > 0 ? (
                <div className="grid gap-2">
                  {entities.slice(0, 10).map((entity) => (
                    <div
                      key={entity.name}
                      className="flex items-center gap-3 px-3 py-2 rounded-lg bg-card/50"
                    >
                      <span className="px-2 py-0.5 rounded text-xs font-medium bg-muted text-muted-foreground">
                        {entity.type}
                      </span>
                      <span className="flex-1 text-sm text-foreground truncate">
                        {entity.name}
                      </span>
                      <span className="text-xs text-muted-foreground">
                        {entity.mention_count} mentions
                      </span>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="text-sm text-muted-foreground text-center py-4">
                  No entities in this collection yet
                </p>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
}
