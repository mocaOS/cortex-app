"use client";

import { useState, useEffect, useRef } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  FolderOpen,
  ChevronDown,
  Plus,
  Check,
  Loader2,
  Layers,
  X,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { api } from "@/lib/api";
import type { Collection } from "@/types";

interface CollectionSelectorProps {
  value?: string;
  onChange: (collectionId: string | undefined) => void;
  allowCreate?: boolean;
  autoSelectDefault?: boolean;
  showAllOption?: boolean;
  className?: string;
  placeholder?: string;
}

export default function CollectionSelector({
  value,
  onChange,
  allowCreate = true,
  autoSelectDefault = false,
  showAllOption = false,
  className,
  placeholder = "Select collection...",
}: CollectionSelectorProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [collections, setCollections] = useState<Collection[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isCreating, setIsCreating] = useState(false);
  const [newName, setNewName] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  const selectedCollection = collections.find((c) => c.id === value);

  useEffect(() => {
    fetchCollections();
  }, []);

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setIsOpen(false);
        setIsCreating(false);
      }
    };

    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  const fetchCollections = async () => {
    try {
      const data = await api.getCollections();
      setCollections(data.collections);
      if (autoSelectDefault && !value) {
        const defaultCol = data.collections.find((c: Collection) => c.id === "default");
        if (defaultCol) {
          onChange(defaultCol.id);
        }
      }
    } catch (error) {
      console.error("Failed to fetch collections:", error);
    } finally {
      setIsLoading(false);
    }
  };

  const handleCreate = async () => {
    if (!newName.trim()) return;

    setIsSubmitting(true);
    try {
      const collection = await api.createCollection({ name: newName.trim() });
      setCollections((prev) => [collection, ...prev]);
      onChange(collection.id);
      setNewName("");
      setIsCreating(false);
      setIsOpen(false);
    } catch (error) {
      console.error("Failed to create collection:", error);
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleSelect = (collectionId: string | undefined) => {
    onChange(collectionId);
    setIsOpen(false);
  };

  return (
    <div ref={dropdownRef} className={cn("relative", className)}>
      <button
        type="button"
        onClick={() => setIsOpen(!isOpen)}
        className={cn(
          "flex items-center gap-2 px-3 py-2 rounded-lg text-sm transition-all w-full",
          "bg-card border border-border hover:border-ring",
          "text-foreground hover:text-foreground",
          isOpen && "border-foreground ring-1 ring-foreground/20"
        )}
      >
        {selectedCollection || !showAllOption ? (
          <FolderOpen className="w-4 h-4 text-muted-foreground" />
        ) : (
          <Layers className="w-4 h-4 text-muted-foreground" />
        )}
        <span className="flex-1 text-left truncate">
          {isLoading ? (
            "Loading..."
          ) : selectedCollection ? (
            selectedCollection.name
          ) : (
            <span className={showAllOption ? "text-foreground" : "text-muted-foreground"}>{placeholder}</span>
          )}
        </span>
        <ChevronDown
          className={cn(
            "w-4 h-4 text-muted-foreground transition-transform",
            isOpen && "rotate-180"
          )}
        />
      </button>

      <AnimatePresence>
        {isOpen && (
          <motion.div
            initial={{ opacity: 0, y: -10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -10 }}
            className="absolute z-50 top-full left-0 mt-1 min-w-full w-max max-w-xs glass rounded-lg border border-border shadow-xl overflow-hidden"
          >
            {allowCreate && (
              <div className="p-2 border-b border-border">
                {isCreating ? (
                  <div className="flex items-center gap-2">
                    <input
                      type="text"
                      value={newName}
                      onChange={(e) => setNewName(e.target.value)}
                      placeholder="Collection name..."
                      className="flex-1 px-2 py-1.5 bg-card rounded text-sm text-foreground placeholder:text-muted-foreground border border-border focus:border-foreground focus:outline-none"
                      autoFocus
                      onKeyDown={(e) => {
                        if (e.key === "Enter") handleCreate();
                        if (e.key === "Escape") setIsCreating(false);
                      }}
                    />
                      <button
                      type="button"
                      onClick={handleCreate}
                      disabled={isSubmitting || !newName.trim()}
                      className="p-1.5 rounded bg-accent text-accent-foreground hover:bg-accent/90 disabled:opacity-50"
                    >
                      {isSubmitting ? (
                        <Loader2 className="w-4 h-4 animate-spin" />
                      ) : (
                        <Check className="w-4 h-4" />
                      )}
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        setIsCreating(false);
                        setNewName("");
                      }}
                      className="p-1.5 rounded text-muted-foreground hover:text-foreground hover:bg-muted"
                    >
                      <X className="w-4 h-4" />
                    </button>
                  </div>
                ) : (
                  <button
                    type="button"
                    onClick={() => setIsCreating(true)}
                    className="flex items-center gap-2 w-full px-2 py-1.5 rounded text-sm text-accent hover:bg-accent/10 transition-colors"
                  >
                    <Plus className="w-4 h-4" />
                    Create new collection
                  </button>
                )}
              </div>
            )}

            <div className="max-h-48 overflow-y-auto">
              {isLoading ? (
                <div className="flex items-center justify-center py-4">
                  <Loader2 className="w-5 h-5 text-accent animate-spin" />
                </div>
              ) : (
                <>
                  {showAllOption && (
                    <button
                      type="button"
                      onClick={() => handleSelect(undefined)}
                      className={cn(
                        "flex items-center gap-2 w-full px-3 py-2 text-sm transition-colors",
                        !value
                          ? "bg-muted text-foreground"
                          : "text-foreground hover:bg-muted"
                      )}
                    >
                      <Layers className="w-4 h-4" />
                      <span className="flex-1 text-left truncate">All Collections</span>
                      {!value && <Check className="w-4 h-4" />}
                    </button>
                  )}
                  {collections.map((collection) => (
                    <button
                      type="button"
                      key={collection.id}
                      onClick={() => handleSelect(collection.id)}
                      className={cn(
                        "flex items-center gap-2 w-full px-3 py-2 text-sm transition-colors",
                        value === collection.id
                          ? "bg-muted text-foreground"
                          : "text-foreground hover:bg-muted"
                      )}
                    >
                      <FolderOpen className="w-4 h-4" />
                      <span className="flex-1 text-left truncate">{collection.name}</span>
                      <span className="text-xs text-muted-foreground">
                        {collection.document_count} docs
                      </span>
                      {value === collection.id && <Check className="w-4 h-4" />}
                    </button>
                  ))}
                </>
              )}

              {!isLoading && collections.length === 0 && (
                <div className="px-3 py-4 text-center text-sm text-muted-foreground">
                  No collections yet
                </div>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
