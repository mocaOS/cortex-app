"use client";

import { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Search, FileText, Loader2, Sparkles } from "lucide-react";
import { cn } from "@/lib/utils";

interface SearchResult {
  document_id: string;
  chunk_id: string;
  content: string;
  score: number;
  metadata: {
    filename: string;
    chunk_index: number;
  };
}

export default function SearchPanel() {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [isSearching, setIsSearching] = useState(false);
  const [hasSearched, setHasSearched] = useState(false);

  const handleSearch = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!query.trim()) return;

    setIsSearching(true);
    setHasSearched(true);

    try {
      const res = await fetch("/api/search", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query, top_k: 10 }),
      });

      if (res.ok) {
        const data = await res.json();
        setResults(data.results);
      }
    } catch (error) {
      console.error("Search failed:", error);
    } finally {
      setIsSearching(false);
    }
  };

  return (
    <div className="space-y-6">
      {/* Search Input */}
      <form onSubmit={handleSearch}>
        <div className="relative group">
          <div className="relative glass rounded-lg p-2 flex items-center gap-3">
            <div className="pl-4">
              <Search className="w-5 h-5 text-muted-foreground" />
            </div>

            <input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search the knowledge base..."
              className="flex-1 bg-transparent border-none outline-none text-foreground placeholder:text-muted-foreground py-3"
            />

            <button
              type="submit"
              disabled={isSearching || !query.trim()}
              className={cn(
                "px-6 py-3 rounded-lg font-medium transition-all duration-300",
                "bg-accent text-accent-foreground",
                "hover:bg-accent/90",
                "disabled:opacity-50 disabled:cursor-not-allowed",
                "flex items-center gap-2"
              )}
            >
              {isSearching ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <Sparkles className="w-4 h-4" />
              )}
              <span>Search</span>
            </button>
          </div>
        </div>
      </form>

      {/* Results */}
      <AnimatePresence mode="wait">
        {hasSearched && (
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -20 }}
            className="space-y-4"
          >
            {isSearching ? (
              <div className="glass rounded-lg p-12 text-center">
                <Loader2 className="w-8 h-8 text-accent animate-spin mx-auto mb-4" />
                <p className="text-muted-foreground">Searching...</p>
              </div>
            ) : results.length > 0 ? (
              <>
                <div className="flex items-center justify-between">
                  <p className="text-sm text-muted-foreground">
                    Found {results.length} results
                  </p>
                </div>

                <div className="space-y-3">
                  {results.map((result, index) => (
                    <motion.div
                      key={result.chunk_id}
                      initial={{ opacity: 0, y: 20 }}
                      animate={{ opacity: 1, y: 0 }}
                      transition={{ delay: index * 0.05 }}
                      className="glass glass-hover rounded-lg p-5 group"
                    >
                      <div className="flex items-start gap-4">
                        <div className="w-10 h-10 rounded-lg bg-muted flex items-center justify-center shrink-0">
                          <FileText className="w-5 h-5 text-foreground" />
                        </div>

                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-3 mb-2">
                            <span className="text-sm font-medium text-foreground">
                              {result.metadata.filename}
                            </span>
                            <span className="text-xs px-2 py-0.5 rounded-full bg-muted text-muted-foreground">
                              {(result.score * 100).toFixed(1)}% match
                            </span>
                            <span className="text-xs text-muted-foreground">
                              Chunk #{result.metadata.chunk_index + 1}
                            </span>
                          </div>

                          <p className="text-sm text-muted-foreground leading-relaxed line-clamp-3">
                            {result.content}
                          </p>
                        </div>
                      </div>
                    </motion.div>
                  ))}
                </div>
              </>
            ) : (
              <div className="glass rounded-lg p-12 text-center">
                <Search className="w-12 h-12 text-accent/50 mx-auto mb-4" />
                <p className="text-muted-foreground">No results found</p>
                <p className="text-sm text-muted-foreground/70 mt-2">
                  Try different keywords or upload more documents
                </p>
              </div>
            )}
          </motion.div>
        )}
      </AnimatePresence>

      {/* Empty State */}
      {!hasSearched && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          className="glass rounded-lg p-12 text-center"
        >
          <div className="w-16 h-16 mx-auto rounded-lg bg-accent/20 flex items-center justify-center mb-6">
            <Search className="w-8 h-8 text-accent" />
          </div>
          <h3 className="text-lg font-medium text-foreground mb-2">
            Semantic Search
          </h3>
          <p className="text-muted-foreground max-w-md mx-auto">
            Search through your knowledge base using natural language. Our AI
            understands meaning, not just keywords.
          </p>
        </motion.div>
      )}
    </div>
  );
}
