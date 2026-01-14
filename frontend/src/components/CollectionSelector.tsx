"use client";

import { useState, useEffect, useRef } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  FolderOpen,
  ChevronDown,
  Plus,
  Check,
  Loader2,
  X,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { api } from "@/lib/api";
import type { Collection } from "@/types";

interface CollectionSelectorProps {
  value?: string;
  onChange: (collectionId: string | undefined) => void;
  allowCreate?: boolean;
  className?: string;
}

export default function CollectionSelector({
  value,
  onChange,
  allowCreate = true,
  className,
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
      {/* Trigger button */}
      <button
        type="button"
        onClick={() => setIsOpen(!isOpen)}
        className={cn(
          "flex items-center gap-2 px-3 py-2 rounded-lg text-sm transition-all w-full",
          "bg-white/5 border border-white/10 hover:border-white/20",
          "text-white/70 hover:text-white/90",
          isOpen && "border-ocean-500/50 ring-1 ring-ocean-500/20"
        )}
      >
        <FolderOpen className="w-4 h-4 text-ocean-400/70" />
        <span className="flex-1 text-left truncate">
          {isLoading ? (
            "Loading..."
          ) : selectedCollection ? (
            selectedCollection.name
          ) : (
            <span className="text-white/40">Default Collection</span>
          )}
        </span>
        <ChevronDown
          className={cn(
            "w-4 h-4 text-white/40 transition-transform",
            isOpen && "rotate-180"
          )}
        />
      </button>

      {/* Dropdown */}
      <AnimatePresence>
        {isOpen && (
          <motion.div
            initial={{ opacity: 0, y: -10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -10 }}
            className="absolute z-50 top-full left-0 right-0 mt-1 glass rounded-lg border border-white/10 shadow-xl overflow-hidden"
          >
            {/* Create new */}
            {allowCreate && (
              <div className="p-2 border-b border-white/5">
                {isCreating ? (
                  <div className="flex items-center gap-2">
                    <input
                      type="text"
                      value={newName}
                      onChange={(e) => setNewName(e.target.value)}
                      placeholder="Collection name..."
                      className="flex-1 px-2 py-1.5 bg-white/5 rounded text-sm text-white placeholder:text-white/30 border border-white/10 focus:border-ocean-500/50 focus:outline-none"
                      autoFocus
                      onKeyDown={(e) => {
                        if (e.key === "Enter") handleCreate();
                        if (e.key === "Escape") setIsCreating(false);
                      }}
                    />
                    <button
                      onClick={handleCreate}
                      disabled={isSubmitting || !newName.trim()}
                      className="p-1.5 rounded bg-ocean-500/20 text-ocean-400 hover:bg-ocean-500/30 disabled:opacity-50"
                    >
                      {isSubmitting ? (
                        <Loader2 className="w-4 h-4 animate-spin" />
                      ) : (
                        <Check className="w-4 h-4" />
                      )}
                    </button>
                    <button
                      onClick={() => {
                        setIsCreating(false);
                        setNewName("");
                      }}
                      className="p-1.5 rounded text-white/40 hover:text-white/60 hover:bg-white/5"
                    >
                      <X className="w-4 h-4" />
                    </button>
                  </div>
                ) : (
                  <button
                    onClick={() => setIsCreating(true)}
                    className="flex items-center gap-2 w-full px-2 py-1.5 rounded text-sm text-ocean-400 hover:bg-ocean-500/10 transition-colors"
                  >
                    <Plus className="w-4 h-4" />
                    Create new collection
                  </button>
                )}
              </div>
            )}

            {/* Collection list */}
            <div className="max-h-48 overflow-y-auto">
              {/* Default option */}
              <button
                onClick={() => handleSelect(undefined)}
                className={cn(
                  "flex items-center gap-2 w-full px-3 py-2 text-sm transition-colors",
                  !value
                    ? "bg-ocean-500/10 text-ocean-400"
                    : "text-white/70 hover:bg-white/5"
                )}
              >
                <FolderOpen className="w-4 h-4" />
                <span className="flex-1 text-left">Default Collection</span>
                {!value && <Check className="w-4 h-4" />}
              </button>

              {/* Collections */}
              {isLoading ? (
                <div className="flex items-center justify-center py-4">
                  <Loader2 className="w-5 h-5 text-ocean-400 animate-spin" />
                </div>
              ) : (
                collections.map((collection) => (
                  <button
                    key={collection.id}
                    onClick={() => handleSelect(collection.id)}
                    className={cn(
                      "flex items-center gap-2 w-full px-3 py-2 text-sm transition-colors",
                      value === collection.id
                        ? "bg-ocean-500/10 text-ocean-400"
                        : "text-white/70 hover:bg-white/5"
                    )}
                  >
                    <FolderOpen className="w-4 h-4" />
                    <span className="flex-1 text-left truncate">{collection.name}</span>
                    <span className="text-xs text-white/30">
                      {collection.document_count} docs
                    </span>
                    {value === collection.id && <Check className="w-4 h-4" />}
                  </button>
                ))
              )}

              {!isLoading && collections.length === 0 && (
                <div className="px-3 py-4 text-center text-sm text-white/40">
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
