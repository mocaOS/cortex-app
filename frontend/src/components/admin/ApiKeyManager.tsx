"use client";

import { useState, useEffect } from "react";
import { createPortal } from "react-dom";
import { motion, AnimatePresence } from "framer-motion";
import {
  KeyRound,
  Plus,
  X,
  Check,
  Copy,
  Loader2,
  AlertCircle,
  RefreshCw,
  Coins,
} from "lucide-react";
import { api } from "@/lib/api";
import { copyToClipboard as copyTextToClipboard } from "@/lib/utils";
import { useModalDismiss } from "@/lib/hooks";
import { useBodyScrollLock } from "@/hooks/useBodyScrollLock";
import type {
  APIKeyWithStats,
  CreateAPIKeyResponse,
  APIKeyPermission,
  CollectionScope,
  Collection,
  X402ConfigResponse,
} from "@/types";
import { ApiKeyCard } from "./ApiKeyCard";
import { ApiKeyAnalytics } from "./ApiKeyAnalytics";

export function ApiKeyManager() {
  const [apiKeys, setApiKeys] = useState<APIKeyWithStats[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState<string | null>(null);

  // Modal states
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [newKeyResult, setNewKeyResult] = useState<CreateAPIKeyResponse | null>(null);
  const [analyticsKey, setAnalyticsKey] = useState<APIKeyWithStats | null>(null);
  const [copied, setCopied] = useState(false);

  // Portal mount state for SSR compatibility
  const [mounted, setMounted] = useState(false);

  // x402 status gates the "Monetized public key" option in the create modal
  // and provides the asset name for price display. null = feature unavailable.
  const [x402Config, setX402Config] = useState<X402ConfigResponse | null>(null);

  useBodyScrollLock(showCreateModal || !!newKeyResult);

  useEffect(() => {
    setMounted(true);
  }, []);

  useEffect(() => {
    api
      .getX402Config()
      .then((c) => setX402Config(c))
      .catch(() => {
        // x402 not available on this backend — monetized keys stay hidden/disabled.
      });
  }, []);

  // Fetch API keys with stats
  const fetchApiKeys = async () => {
    try {
      setLoading(true);
      const data = await api.listApiKeysWithStats();
      setApiKeys(data);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load API keys");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchApiKeys();
  }, []);

  // Create new API key
  const handleCreate = async (
    name: string,
    permissions: APIKeyPermission[],
    collectionScope: CollectionScope,
    allowedCollections: string[],
    pricePerQuery?: string
  ) => {
    try {
      const result = await api.createApiKey({
        name,
        permissions,
        collection_scope: collectionScope,
        allowed_collections: collectionScope === "restricted" ? allowedCollections : undefined,
        price_per_query: pricePerQuery,
      });
      setNewKeyResult(result);
      setShowCreateModal(false);
      fetchApiKeys();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create API key");
    }
  };

  // Revoke API key
  const handleRevoke = async (keyId: string) => {
    setActionLoading(keyId);
    try {
      await api.revokeApiKey(keyId);
      fetchApiKeys();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to revoke API key");
    } finally {
      setActionLoading(null);
    }
  };

  // Activate API key
  const handleActivate = async (keyId: string) => {
    setActionLoading(keyId);
    try {
      await api.activateApiKey(keyId);
      fetchApiKeys();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to activate API key");
    } finally {
      setActionLoading(null);
    }
  };

  // Delete API key
  const handleDelete = async (keyId: string) => {
    if (!confirm("Are you sure you want to permanently delete this API key?")) {
      return;
    }

    setActionLoading(keyId);
    try {
      await api.deleteApiKey(keyId);
      fetchApiKeys();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete API key");
    } finally {
      setActionLoading(null);
    }
  };

  // View analytics
  const handleViewAnalytics = (keyId: string) => {
    const key = apiKeys.find((k) => k.id === keyId);
    if (key) {
      setAnalyticsKey(key);
    }
  };

  // Copy to clipboard (with an insecure-origin fallback for self-hosted HTTP)
  const copyToClipboard = async (text: string) => {
    const ok = await copyTextToClipboard(text);
    if (!ok) {
      alert("Couldn't copy automatically — please select and copy the key manually.");
      return;
    }
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="glass rounded-xl overflow-hidden">
      {/* Header */}
      <div className="px-6 py-4 border-b border-border/50 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <KeyRound className="w-5 h-5 text-accent" />
          <h2 className="text-lg font-semibold text-foreground">API Keys</h2>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={fetchApiKeys}
            disabled={loading}
            className="p-2 rounded-lg hover:bg-muted text-muted-foreground hover:text-foreground transition-colors disabled:opacity-50"
            title="Refresh"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? "animate-spin" : ""}`} />
          </button>
          <button
            onClick={() => setShowCreateModal(true)}
            className="flex items-center gap-2 px-3 py-1.5 bg-accent hover:bg-accent/90 text-accent-foreground text-sm font-medium rounded-lg transition-colors"
          >
            <Plus className="w-4 h-4" />
            New Key
          </button>
        </div>
      </div>

      {/* Error Message */}
      {error && (
        <div className="mx-6 mt-4 bg-destructive/10 border border-destructive/20 rounded-lg px-4 py-3 text-destructive flex items-center justify-between">
          <div className="flex items-center gap-2">
            <AlertCircle className="w-4 h-4" />
            <span className="text-sm">{error}</span>
          </div>
          <button onClick={() => setError(null)} className="text-destructive hover:text-destructive/80">
            <X className="w-4 h-4" />
          </button>
        </div>
      )}

      {/* Content */}
      <div className="p-6">
        {loading ? (
          <div className="flex items-center justify-center py-8">
            <Loader2 className="w-6 h-6 animate-spin text-accent" />
          </div>
        ) : apiKeys.length === 0 ? (
          <div className="text-center py-8">
            <div className="w-12 h-12 mx-auto rounded-lg bg-muted flex items-center justify-center mb-3">
              <KeyRound className="w-6 h-6 text-muted-foreground" />
            </div>
            <h3 className="text-foreground font-medium mb-1">No API Keys</h3>
            <p className="text-sm text-muted-foreground mb-4">
              Create your first API key to get started
            </p>
            <button
              onClick={() => setShowCreateModal(true)}
              className="px-4 py-2 bg-accent hover:bg-accent/90 text-accent-foreground text-sm rounded-lg transition-colors"
            >
              Create API Key
            </button>
          </div>
        ) : (
          <div className="space-y-3">
            {apiKeys.map((key) => (
              <ApiKeyCard
                key={key.id}
                apiKey={key}
                onRevoke={handleRevoke}
                onActivate={handleActivate}
                onDelete={handleDelete}
                onViewAnalytics={handleViewAnalytics}
                isLoading={actionLoading === key.id}
                x402AssetName={x402Config?.asset_name ?? undefined}
              />
            ))}
          </div>
        )}
      </div>

      {/* Modals - rendered via portal to document.body */}
      {mounted && createPortal(
        <>
          {/* Create API Key Modal */}
          <AnimatePresence>
            {showCreateModal && (
              <CreateKeyModal
                onClose={() => setShowCreateModal(false)}
                onCreate={handleCreate}
                x402Config={x402Config}
              />
            )}
          </AnimatePresence>

          {/* New Key Result Modal */}
          <AnimatePresence>
            {newKeyResult && (
              <motion.div
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                className="fixed inset-0 bg-background/80 backdrop-blur-sm flex items-center justify-center z-50 p-4"
                onClick={() => setNewKeyResult(null)}
              >
                <motion.div
                  initial={{ scale: 0.9, opacity: 0 }}
                  animate={{ scale: 1, opacity: 1 }}
                  exit={{ scale: 0.9, opacity: 0 }}
                  className="bg-card rounded-xl border border-border p-6 max-w-lg w-full"
                  onClick={(e) => e.stopPropagation()}
                >
                  <div className="text-center mb-6">
                    <div className="inline-flex items-center justify-center w-14 h-14 rounded-xl bg-accent/20 mb-4">
                      <Check className="w-7 h-7 text-accent" />
                    </div>
                    <h2 className="text-xl font-bold text-foreground">API Key Created</h2>
                    <p className="text-muted-foreground mt-2">
                      Make sure to copy your API key now. You won&apos;t be able to see it again!
                    </p>
                  </div>

                  <div className="bg-muted rounded-lg p-4 mb-6">
                    <label className="block text-sm text-muted-foreground mb-2">Your API Key</label>
                    <div className="flex items-center gap-2">
                      <code className="flex-1 font-mono text-sm text-accent break-all">
                        {newKeyResult.key}
                      </code>
                      <button
                        onClick={() => copyToClipboard(newKeyResult.key)}
                        className={`p-2 rounded-lg transition-colors ${
                          copied
                            ? "bg-accent/20 text-accent"
                            : "bg-background text-muted-foreground hover:text-foreground"
                        }`}
                      >
                        {copied ? <Check className="w-5 h-5" /> : <Copy className="w-5 h-5" />}
                      </button>
                    </div>
                  </div>

                  <button
                    onClick={() => setNewKeyResult(null)}
                    className="w-full py-2 bg-accent hover:bg-accent/90 text-accent-foreground rounded-lg transition-colors"
                  >
                    Done
                  </button>
                </motion.div>
              </motion.div>
            )}
          </AnimatePresence>

          {/* Analytics Modal */}
          <AnimatePresence>
            {analyticsKey && (
              <ApiKeyAnalytics
                apiKey={analyticsKey}
                onClose={() => setAnalyticsKey(null)}
              />
            )}
          </AnimatePresence>
        </>,
        document.body
      )}
    </div>
  );
}

// Valid positive decimal, e.g. "0.05", "1", ".5"
const PRICE_RE = /^(\d+(\.\d+)?|\.\d+)$/;

// Create Key Modal Component
function CreateKeyModal({
  onClose,
  onCreate,
  preselectedCollectionId,
  x402Config,
}: {
  onClose: () => void;
  onCreate: (name: string, permissions: APIKeyPermission[], collectionScope: CollectionScope, allowedCollections: string[], pricePerQuery?: string) => void;
  preselectedCollectionId?: string;
  x402Config?: X402ConfigResponse | null;
}) {
  const [name, setName] = useState("");
  const [keyType, setKeyType] = useState<"member" | "monetized">("member");
  const [readOnly, setReadOnly] = useState(true);
  const [manage, setManage] = useState(false);
  const [price, setPrice] = useState("");
  const [creating, setCreating] = useState(false);
  const dialogRef = useModalDismiss<HTMLDivElement>(onClose);

  // With X402_ENABLED=false the modal shows NO trace of x402 (no key-type
  // chooser at all) — users who don't know x402 shouldn't meet it. When the
  // flag is on but the config is unverified, the option renders disabled
  // with a pointer to the x402 Payments section (the server enforces the
  // same rule with a 400).
  const x402Enabled = !!x402Config?.enabled;
  const monetizedAvailable = x402Enabled && !!x402Config?.verified;
  const isMonetized = keyType === "monetized";
  const priceValid = PRICE_RE.test(price.trim()) && parseFloat(price.trim()) > 0;
  
  // Collection scope state
  const [collectionScope, setCollectionScope] = useState<CollectionScope>(
    preselectedCollectionId ? "restricted" : "all"
  );
  const [selectedCollections, setSelectedCollections] = useState<string[]>(
    preselectedCollectionId ? [preselectedCollectionId] : []
  );
  const [collections, setCollections] = useState<Collection[]>([]);
  const [loadingCollections, setLoadingCollections] = useState(false);
  
  // Fetch collections when scope changes to restricted
  useEffect(() => {
    if (collectionScope === "restricted" && collections.length === 0) {
      setLoadingCollections(true);
      api.getCollections()
        .then((response) => {
          setCollections(response.collections || []);
        })
        .catch((err) => {
          console.error("Failed to load collections:", err);
        })
        .finally(() => {
          setLoadingCollections(false);
        });
    }
  }, [collectionScope, collections.length]);

  const handleCreate = async () => {
    if (!name.trim()) return;
    if (collectionScope === "restricted" && selectedCollections.length === 0) return;
    if (isMonetized && !priceValid) return;

    // Monetized public keys are always read-only (server rejects manage).
    const permissions: APIKeyPermission[] = [];
    if (isMonetized) {
      permissions.push("read");
    } else {
      if (readOnly || manage) permissions.push("read");
      if (manage) permissions.push("manage");
    }

    setCreating(true);
    await onCreate(
      name,
      permissions,
      collectionScope,
      selectedCollections,
      isMonetized ? price.trim() : undefined
    );
    setCreating(false);
  };

  const toggleCollection = (collectionId: string) => {
    setSelectedCollections((prev) =>
      prev.includes(collectionId)
        ? prev.filter((id) => id !== collectionId)
        : [...prev, collectionId]
    );
  };

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      className="fixed inset-0 bg-background/80 backdrop-blur-sm flex items-center justify-center z-50 p-4"
      onClick={onClose}
    >
      <motion.div
        initial={{ scale: 0.9, opacity: 0 }}
        animate={{ scale: 1, opacity: 1 }}
        exit={{ scale: 0.9, opacity: 0 }}
        className="bg-card rounded-xl border border-border p-6 max-w-md w-full max-h-[90vh] overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
        ref={dialogRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
      >
        <h2 className="text-xl font-bold text-foreground mb-6">Create API Key</h2>

        <div className="space-y-4">
          {/* Name Input */}
          <div>
            <label className="block text-sm font-medium text-foreground mb-2">
              Key Name
            </label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g., Production API Key"
              className="w-full px-4 py-2.5 bg-background border border-border rounded-lg text-foreground placeholder-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
            />
          </div>

          {/* Key Type — only on x402-enabled instances */}
          {x402Enabled && (
          <div>
            <label className="block text-sm font-medium text-foreground mb-3">
              Key Type
            </label>
            <div className="space-y-3">
              <label className="flex items-start gap-3 p-3 bg-muted/50 rounded-lg cursor-pointer hover:bg-muted transition-colors">
                <input
                  type="radio"
                  name="keyType"
                  checked={keyType === "member"}
                  onChange={() => setKeyType("member")}
                  className="mt-0.5 w-4 h-4 border-border bg-muted accent-accent"
                />
                <div>
                  <div className="font-medium text-foreground">Member key</div>
                  <div className="text-sm text-muted-foreground">
                    Standard key with configurable permissions
                  </div>
                </div>
              </label>

              <label
                title={!monetizedAvailable ? "Configure and verify x402 Payments first" : undefined}
                className={`flex items-start gap-3 p-3 bg-muted/50 rounded-lg transition-colors ${
                  monetizedAvailable
                    ? "cursor-pointer hover:bg-muted"
                    : "opacity-50 cursor-not-allowed"
                }`}
              >
                <input
                  type="radio"
                  name="keyType"
                  checked={keyType === "monetized"}
                  disabled={!monetizedAvailable}
                  onChange={() => setKeyType("monetized")}
                  className="mt-0.5 w-4 h-4 border-border bg-muted accent-accent"
                />
                <div>
                  <div className="font-medium text-foreground flex items-center gap-1.5">
                    <Coins className="w-3.5 h-3.5 text-accent" />
                    Monetized public key (x402)
                  </div>
                  <div className="text-sm text-muted-foreground">
                    Read-only retrieval key that charges a price per query via x402
                    {!monetizedAvailable && " — configure and verify x402 Payments first"}
                  </div>
                </div>
              </label>
            </div>
          </div>
          )}

          {/* Permissions (member keys) / forced read-only + price (monetized) */}
          {isMonetized ? (
            <>
              <div className="flex items-center gap-2 text-sm text-muted-foreground bg-muted/50 rounded-lg p-3">
                <KeyRound className="w-4 h-4 text-accent flex-shrink-0" />
                <span>
                  Monetized keys are always <span className="text-foreground">read-only</span> and
                  restricted to the retrieval endpoints.
                </span>
              </div>
              <div>
                <label className="block text-sm font-medium text-foreground mb-2">
                  Price per query
                </label>
                <div className="relative">
                  <input
                    type="text"
                    inputMode="decimal"
                    value={price}
                    onChange={(e) => setPrice(e.target.value)}
                    placeholder="0.05"
                    className="w-full px-4 py-2.5 pr-20 bg-background border border-border rounded-lg text-foreground placeholder-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring font-mono"
                  />
                  <span className="absolute right-4 top-1/2 -translate-y-1/2 text-sm text-muted-foreground pointer-events-none">
                    {x402Config?.asset_name || ""}
                  </span>
                </div>
                {price.trim() !== "" && !priceValid && (
                  <p className="text-xs text-destructive mt-1">
                    Enter a positive decimal amount, e.g. 0.05
                  </p>
                )}
              </div>
            </>
          ) : (
            <div>
              <label className="block text-sm font-medium text-foreground mb-3">
                Permissions
              </label>
              <div className="space-y-3">
                <label className="flex items-start gap-3 p-3 bg-muted/50 rounded-lg cursor-pointer hover:bg-muted transition-colors">
                  <input
                    type="checkbox"
                    checked={readOnly}
                    onChange={(e) => {
                      setReadOnly(e.target.checked);
                      if (!e.target.checked) setManage(false);
                    }}
                    className="mt-0.5 w-4 h-4 rounded border-border bg-muted accent-accent"
                  />
                  <div>
                    <div className="font-medium text-foreground">Read Only</div>
                    <div className="text-sm text-muted-foreground">
                      Can use Ask AI, search, and view the knowledge graph
                    </div>
                  </div>
                </label>

                <label className="flex items-start gap-3 p-3 bg-muted/50 rounded-lg cursor-pointer hover:bg-muted transition-colors">
                  <input
                    type="checkbox"
                    checked={manage}
                    onChange={(e) => {
                      setManage(e.target.checked);
                      if (e.target.checked) setReadOnly(true);
                    }}
                    className="mt-0.5 w-4 h-4 rounded border-border bg-muted accent-accent"
                  />
                  <div>
                    <div className="font-medium text-foreground">Read/Write (Manage)</div>
                    <div className="text-sm text-muted-foreground">
                      Can upload, edit, and delete documents and collections
                    </div>
                  </div>
                </label>
              </div>
            </div>
          )}

          {/* Collection Scope */}
          <div>
            <label className="block text-sm font-medium text-foreground mb-3">
              Collection Access
            </label>
            <div className="space-y-3">
              <label className="flex items-start gap-3 p-3 bg-muted/50 rounded-lg cursor-pointer hover:bg-muted transition-colors">
                <input
                  type="radio"
                  name="collectionScope"
                  checked={collectionScope === "all"}
                  onChange={() => setCollectionScope("all")}
                  className="mt-0.5 w-4 h-4 border-border bg-muted accent-accent"
                />
                <div>
                  <div className="font-medium text-foreground">All Collections</div>
                  <div className="text-sm text-muted-foreground">
                    Can access all current and future collections
                  </div>
                </div>
              </label>

              <label className="flex items-start gap-3 p-3 bg-muted/50 rounded-lg cursor-pointer hover:bg-muted transition-colors">
                <input
                  type="radio"
                  name="collectionScope"
                  checked={collectionScope === "restricted"}
                  onChange={() => setCollectionScope("restricted")}
                  className="mt-0.5 w-4 h-4 border-border bg-muted accent-accent"
                />
                <div>
                  <div className="font-medium text-foreground">Specific Collections</div>
                  <div className="text-sm text-muted-foreground">
                    Restrict access to selected collections only
                  </div>
                </div>
              </label>
            </div>
          </div>

          {/* Collection Picker (when restricted) */}
          {collectionScope === "restricted" && (
            <div className="pl-7">
              <label className="block text-sm font-medium text-foreground mb-2">
                Select Collections
              </label>
              {loadingCollections ? (
                <div className="flex items-center gap-2 text-sm text-muted-foreground py-2">
                  <Loader2 className="w-4 h-4 animate-spin" />
                  Loading collections...
                </div>
              ) : collections.length === 0 ? (
                <div className="text-sm text-muted-foreground py-2">
                  No collections available
                </div>
              ) : (
                <div className="space-y-2 max-h-40 overflow-y-auto border border-border rounded-lg p-2">
                  {collections.map((collection) => (
                    <label
                      key={collection.id}
                      className="flex items-center gap-2 p-2 rounded hover:bg-muted/50 cursor-pointer"
                    >
                      <input
                        type="checkbox"
                        checked={selectedCollections.includes(collection.id)}
                        onChange={() => toggleCollection(collection.id)}
                        className="w-4 h-4 rounded border-border bg-muted accent-accent"
                      />
                      <span className="text-sm text-foreground">{collection.name}</span>
                    </label>
                  ))}
                </div>
              )}
              {selectedCollections.length > 0 && (
                <div className="mt-2 text-xs text-muted-foreground">
                  {selectedCollections.length} collection{selectedCollections.length !== 1 ? "s" : ""} selected
                </div>
              )}
            </div>
          )}
        </div>

        {/* Actions */}
        <div className="flex gap-3 mt-6">
          <button
            onClick={onClose}
            className="flex-1 py-2.5 bg-muted hover:bg-muted/80 text-foreground rounded-lg transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={handleCreate}
            disabled={
              !name.trim() ||
              (isMonetized ? !priceValid : !readOnly && !manage) ||
              (collectionScope === "restricted" && selectedCollections.length === 0) ||
              creating
            }
            className="flex-1 py-2.5 bg-accent hover:bg-accent/90 disabled:opacity-50 disabled:cursor-not-allowed text-accent-foreground rounded-lg transition-colors"
          >
            {creating ? "Creating..." : "Create Key"}
          </button>
        </div>
      </motion.div>
    </motion.div>
  );
}
