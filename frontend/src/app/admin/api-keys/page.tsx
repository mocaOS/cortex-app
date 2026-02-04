"use client";

import { useState, useEffect } from "react";
import { PageTransition } from "@/components/layout";
import { motion, AnimatePresence } from "framer-motion";
import { APIKeyListItem, CreateAPIKeyResponse, APIKeyPermission } from "@/types";
import { api } from "@/lib/api";
import { Plus, KeyRound, X, Check, Copy, Loader2 } from "lucide-react";

export default function APIKeysPage() {
  const [apiKeys, setApiKeys] = useState<APIKeyListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [newKeyResult, setNewKeyResult] = useState<CreateAPIKeyResponse | null>(null);
  const [copied, setCopied] = useState(false);

  // Fetch API keys
  const fetchApiKeys = async () => {
    try {
      setLoading(true);
      const data = await api.listApiKeys();
      setApiKeys(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "An error occurred");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchApiKeys();
  }, []);

  // Create new API key
  const createApiKey = async (name: string, permissions: APIKeyPermission[]) => {
    try {
      const data = await api.createApiKey({ name, permissions });
      setNewKeyResult(data);
      fetchApiKeys();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create API key");
    }
  };

  // Delete API key
  const deleteApiKey = async (keyId: string) => {
    if (!confirm("Are you sure you want to delete this API key?")) return;

    try {
      await api.deleteApiKey(keyId);
      fetchApiKeys();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete API key");
    }
  };

  // Toggle API key active status
  const toggleApiKey = async (keyId: string, isActive: boolean) => {
    try {
      await api.updateApiKey(keyId, { is_active: !isActive });
      fetchApiKeys();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to update API key");
    }
  };

  // Copy to clipboard
  const copyToClipboard = async (text: string) => {
    await navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <PageTransition>
      <div className="space-y-8">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-3xl font-bold text-foreground mb-2">API Keys</h1>
            <p className="text-muted-foreground">
              Create and manage API keys for backend access
            </p>
          </div>
          <button
            onClick={() => setShowCreateModal(true)}
            className="flex items-center gap-2 px-4 py-2 bg-accent hover:bg-accent/90 text-accent-foreground font-medium rounded-lg transition-all"
          >
            <Plus className="w-5 h-5" />
            Create API Key
          </button>
        </div>

        {/* Error Message */}
        {error && (
          <div className="bg-destructive/10 border border-destructive/20 rounded-lg px-4 py-3 text-destructive flex items-center justify-between">
            <span>{error}</span>
            <button onClick={() => setError(null)} className="text-destructive hover:text-destructive/80">
              <X className="w-5 h-5" />
            </button>
          </div>
        )}

        {/* API Keys List */}
        {loading ? (
          <div className="flex items-center justify-center py-12">
            <Loader2 className="w-8 h-8 text-accent animate-spin" />
          </div>
        ) : apiKeys.length === 0 ? (
          <div className="text-center py-12 glass rounded-xl">
            <div className="w-16 h-16 mx-auto rounded-lg bg-accent/20 flex items-center justify-center mb-4">
              <KeyRound className="w-8 h-8 text-accent" />
            </div>
            <h3 className="text-xl font-medium text-foreground mb-2">No API Keys</h3>
            <p className="text-muted-foreground mb-4">Create your first API key to get started</p>
            <button
              onClick={() => setShowCreateModal(true)}
              className="px-4 py-2 bg-accent hover:bg-accent/90 text-accent-foreground rounded-lg transition-colors"
            >
              Create API Key
            </button>
          </div>
        ) : (
          <div className="space-y-4">
            {apiKeys.map((key, index) => (
              <motion.div
                key={key.id}
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: index * 0.05 }}
                className={`glass rounded-xl p-6 ${
                  !key.is_active ? "border-destructive/30" : ""
                }`}
              >
                <div className="flex items-start justify-between">
                  <div className="flex-1">
                    <div className="flex items-center gap-3 mb-2">
                      <h3 className="text-lg font-medium text-foreground">{key.name}</h3>
                      {!key.is_active && (
                        <span className="px-2 py-0.5 bg-destructive/20 text-destructive text-xs rounded-full">
                          Revoked
                        </span>
                      )}
                    </div>
                    <div className="flex items-center gap-4 text-sm text-muted-foreground">
                      <span className="font-mono bg-muted px-2 py-1 rounded">
                        {key.key_prefix}...
                      </span>
                      <span>Created: {new Date(key.created_at).toLocaleDateString()}</span>
                      {key.last_used_at && (
                        <span>Last used: {new Date(key.last_used_at).toLocaleDateString()}</span>
                      )}
                    </div>
                    <div className="flex items-center gap-2 mt-3">
                      {key.permissions.map((perm) => (
                        <span
                          key={perm}
                          className={`px-2 py-1 text-xs rounded-full ${
                            perm === "manage"
                              ? "bg-accent/20 text-accent"
                              : "bg-muted text-muted-foreground"
                          }`}
                        >
                          {perm === "manage" ? "Read/Write" : "Read Only"}
                        </span>
                      ))}
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    <button
                      onClick={() => toggleApiKey(key.id, key.is_active)}
                      className={`px-3 py-1.5 text-sm rounded-lg transition-colors ${
                        key.is_active
                          ? "bg-destructive/10 text-destructive hover:bg-destructive/20"
                          : "bg-accent/10 text-accent hover:bg-accent/20"
                      }`}
                    >
                      {key.is_active ? "Revoke" : "Activate"}
                    </button>
                    <button
                      onClick={() => deleteApiKey(key.id)}
                      className="px-3 py-1.5 text-sm bg-muted text-muted-foreground hover:text-destructive hover:bg-muted/80 rounded-lg transition-colors"
                    >
                      Delete
                    </button>
                  </div>
                </div>
              </motion.div>
            ))}
          </div>
        )}

        {/* Create API Key Modal */}
        <AnimatePresence>
          {showCreateModal && (
            <CreateKeyModal
              onClose={() => setShowCreateModal(false)}
              onCreate={createApiKey}
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
      </div>
    </PageTransition>
  );
}

// Create Key Modal Component
function CreateKeyModal({
  onClose,
  onCreate,
}: {
  onClose: () => void;
  onCreate: (name: string, permissions: APIKeyPermission[]) => void;
}) {
  const [name, setName] = useState("");
  const [readOnly, setReadOnly] = useState(true);
  const [manage, setManage] = useState(false);
  const [creating, setCreating] = useState(false);

  const handleCreate = async () => {
    if (!name.trim()) return;

    const permissions: APIKeyPermission[] = [];
    if (readOnly || manage) permissions.push("read");
    if (manage) permissions.push("manage");

    setCreating(true);
    await onCreate(name, permissions);
    setCreating(false);
    onClose();
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
        className="bg-card rounded-xl border border-border p-6 max-w-md w-full"
        onClick={(e) => e.stopPropagation()}
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

          {/* Permissions */}
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
                  className="mt-0.5 w-4 h-4 rounded border-border text-accent focus:ring-ring focus:ring-offset-0 bg-background"
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
                  className="mt-0.5 w-4 h-4 rounded border-border text-accent focus:ring-ring focus:ring-offset-0 bg-background"
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
            disabled={!name.trim() || (!readOnly && !manage) || creating}
            className="flex-1 py-2.5 bg-accent hover:bg-accent/90 disabled:opacity-50 disabled:cursor-not-allowed text-accent-foreground rounded-lg transition-colors"
          >
            {creating ? "Creating..." : "Create Key"}
          </button>
        </div>
      </motion.div>
    </motion.div>
  );
}
