"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { useIsMounted } from "@/lib/hooks";
import {
  GitBranch,
  Plus,
  Trash2,
  ChevronDown,
  ChevronRight,
  Loader2,
  AlertCircle,
  RefreshCw,
  CheckCircle2,
  CircleSlash,
  FileWarning,
  HelpCircle,
  ExternalLink,
  Pencil,
  Save,
  GitPullRequest,
  BookOpen,
  KeyRound,
  FolderOpen,
} from "lucide-react";
import { api } from "@/lib/api";
import type {
  Collection,
  GitConnection,
  GitVendor,
  GitAccessLevel,
  GitVerifyResponse,
  GitOrphanedDocument,
} from "@/types";

const VENDOR_LABELS: Record<GitVendor, string> = {
  github: "GitHub",
  gitlab: "GitLab",
  gitea: "Gitea",
};

// Curated "documents only" default applied to new connections: markdown + PDFs,
// anywhere in the tree (including root-level README.md).
const DOC_DEFAULT_GLOBS = ["**/*.md", "**/*.pdf"];
const DOC_DEFAULT_TEXT = "**/*.md, **/*.pdf";

function parseGlobs(text: string): string[] {
  return text.split(",").map((g) => g.trim()).filter(Boolean);
}

function isDocDefault(globs: string[]): boolean {
  if (globs.length !== DOC_DEFAULT_GLOBS.length) return false;
  const set = new Set(globs);
  return DOC_DEFAULT_GLOBS.every((g) => set.has(g));
}

function originOf(baseUrl: string, fallback: string): string {
  if (!baseUrl.trim()) return fallback;
  try {
    return new URL(baseUrl.trim()).origin;
  } catch {
    return fallback;
  }
}

type PermissionItem = { name: string; why: string; optional?: boolean };
type PermissionGroup = { label: string; items: PermissionItem[] };

// Per-vendor token-generation guidance. We always recommend the least-privilege
// option for each provider and link straight to the right settings page.
const VENDOR_TOKEN_GUIDES: Record<
  GitVendor,
  {
    tokenType: string;
    settingsUrl: (baseUrl: string) => string;
    steps: string[];
    writeNote: string;
    caveat?: string;
    /** Exact permissions to tick, per access level. Rendered as chips under the
     *  guide so nobody has to guess which boxes the connect step will need. */
    permissions: Record<GitAccessLevel, PermissionGroup[]>;
  }
> = {
  github: {
    tokenType: "Fine-grained personal access token (least privilege)",
    settingsUrl: (b) => `${originOf(b, "https://github.com")}/settings/personal-access-tokens/new`,
    steps: [
      "Settings → Developer settings → Fine-grained tokens → Generate new token",
      "Resource owner: your account",
      'Repository access: "Only select repositories" → pick this repo',
      "Permissions → Contents: Read-only (enough for ingestion)",
      "Metadata: Read is added automatically",
    ],
    writeNote:
      "For read/write (agent opens PRs): set Contents → Read and write, plus Pull requests → Read and write.",
    caveat:
      "To ingest the wiki, use a classic token with the repo scope — fine-grained tokens don't cover wikis.",
    permissions: {
      read: [
        {
          label: "Fine-grained token → Repository permissions",
          items: [
            { name: "Contents: Read-only", why: "clone + read files" },
            { name: "Metadata: Read-only", why: "added automatically" },
          ],
        },
      ],
      read_write: [
        {
          label: "Fine-grained token → Repository permissions",
          items: [
            { name: "Contents: Read and write", why: "branches + commits" },
            { name: "Pull requests: Read and write", why: "open PRs + comment" },
            { name: "Metadata: Read-only", why: "added automatically" },
          ],
        },
      ],
    },
  },
  gitlab: {
    tokenType: "Project Access Token (least privilege)",
    settingsUrl: (b) => `${originOf(b, "https://gitlab.com")}/-/user_settings/personal_access_tokens`,
    steps: [
      "Your project → Settings → Access Tokens",
      "Role: Reporter (read-only)",
      "Scopes: read_api + read_repository (see the permission list below)",
      "Set an expiry, then create the token",
    ],
    writeNote:
      "For read/write (agent opens merge requests): use Role Developer with scopes api + write_repository.",
    caveat:
      "No project-token access? A Personal Access Token (User settings → Access tokens) works across all your projects — the button below opens it. For a fine-grained personal access token, tick exactly the permissions listed below on the repository. User: Read is not needed; Test then reports \"Token accepted\" without a username.",
    permissions: {
      read: [
        {
          label: "Fine-grained personal access token (GitLab 18.10+) → Group and project",
          items: [
            { name: "Code: Download", why: "git clone / fetch" },
            { name: "Project: Read", why: "resolve the project + default branch on connect" },
            { name: "Repository: Read", why: "read files via API" },
            { name: "Wiki: Read", why: "only if you ingest the wiki", optional: true },
          ],
        },
        {
          label: "Classic project / personal access token → Scopes",
          items: [
            { name: "read_api", why: "resolve the project, browse, wiki" },
            { name: "read_repository", why: "git clone / fetch" },
          ],
        },
      ],
      read_write: [
        {
          label: "Fine-grained personal access token (GitLab 18.10+) → Group and project",
          items: [
            { name: "Code: Download", why: "git clone / fetch" },
            { name: "Project: Read", why: "resolve the project + default branch" },
            { name: "Repository: Read", why: "read files via API" },
            { name: "Branch: Create", why: "agent branch" },
            { name: "Commit: Create", why: "agent commits" },
            { name: "Merge Request: Create", why: "open merge requests" },
            { name: "Work Item: Create", why: "comment on merge requests" },
            { name: "Wiki: Read", why: "only if you ingest the wiki", optional: true },
          ],
        },
        {
          label: "Classic project / personal access token → Scopes (role Developer)",
          items: [
            { name: "api", why: "project lookup + merge requests" },
            { name: "write_repository", why: "branches + commits" },
          ],
        },
      ],
    },
  },
  gitea: {
    tokenType: "Scoped personal access token (least privilege)",
    settingsUrl: (b) => `${originOf(b, "https://gitea.com")}/user/settings/applications`,
    steps: [
      "Settings → Applications → Manage Access Tokens",
      "Generate New Token and give it a name",
      "Scopes → Repository: Read (enough for ingestion)",
    ],
    writeNote:
      "For read/write (agent opens PRs): set Repository: Read and Write, plus Issue: Read and Write (for PR comments).",
    permissions: {
      read: [
        {
          label: "Scoped token → Permissions",
          items: [{ name: "Repository: Read", why: "clone, files, wiki" }],
        },
      ],
      read_write: [
        {
          label: "Scoped token → Permissions",
          items: [
            { name: "Repository: Read and Write", why: "branches, commits, PRs" },
            { name: "Issue: Read and Write", why: "PR comments" },
          ],
        },
      ],
    },
  },
};

function relativeTime(iso?: string | null): string {
  if (!iso) return "never";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "never";
  const diff = Date.now() - then;
  const min = Math.floor(diff / 60000);
  if (min < 1) return "just now";
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  return `${Math.floor(hr / 24)}d ago`;
}

// Explains what the selected access level lets Cortex do, shown under the
// access-level picker in both the create and edit forms.
function AccessLevelNote({ level }: { level: GitAccessLevel }) {
  if (level === "read_write") {
    return (
      <div className="flex items-start gap-1.5 text-[11px] p-2 rounded-lg bg-amber-500/5 border border-amber-500/15">
        <GitPullRequest className="w-3 h-3 text-amber-400 shrink-0 mt-0.5" />
        <span className="text-muted-foreground">
          <span className="text-foreground font-medium">Read/write</span> also lets the research
          agent act on this repository. Anything it changes is committed to a new{" "}
          <span className="font-mono">cortex/agent-…</span> branch and opened as a{" "}
          <span className="text-foreground">pull request for your review</span> — it never pushes
          to your default branch. Requires a token with write + pull-request permissions.
        </span>
      </div>
    );
  }
  return (
    <div className="flex items-start gap-1.5 text-[11px] p-2 rounded-lg bg-blue-500/5 border border-blue-500/15">
      <BookOpen className="w-3 h-3 text-blue-400 shrink-0 mt-0.5" />
      <span className="text-muted-foreground">
        <span className="text-foreground font-medium">Read-only</span> ingests this repository into
        the knowledge graph so it&rsquo;s searchable and queryable. The agent can read repo content
        but cannot modify the repository. A read-only token is enough.
      </span>
    </div>
  );
}

const DEFAULT_COLLECTION_VALUE = "";

/** Target-collection picker shared by the connect + edit forms. Hidden when
 *  collections are disabled or none could be loaded — the backend then files
 *  synced documents into the default collection. */
function CollectionPicker({
  collections,
  value,
  onChange,
  inputCls,
  hint,
}: {
  collections: Collection[];
  value: string;
  onChange: (id: string) => void;
  inputCls: string;
  hint?: string;
}) {
  if (collections.length === 0) return null;
  return (
    <div>
      <label className="text-[10px] text-muted-foreground uppercase tracking-wider flex items-center gap-1">
        <FolderOpen className="w-3 h-3" />
        Collection
      </label>
      <select value={value} onChange={(e) => onChange(e.target.value)} className={inputCls}>
        <option value={DEFAULT_COLLECTION_VALUE}>Default collection</option>
        {collections.map((c) => (
          <option key={c.id} value={c.id}>
            {c.name}
          </option>
        ))}
      </select>
      {hint && <p className="mt-1 text-[10px] text-muted-foreground">{hint}</p>}
    </div>
  );
}

function collectionName(collections: Collection[], id?: string | null): string {
  if (!id) return "default";
  return collections.find((c) => c.id === id)?.name ?? id;
}

export function GitIntegrations({ collectionsEnabled = true }: { collectionsEnabled?: boolean } = {}) {
  const [connections, setConnections] = useState<GitConnection[]>([]);
  // Collections the synced documents can be filed into (empty = picker hidden).
  const [collections, setCollections] = useState<Collection[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);

  // per-connection transient state
  const [syncing, setSyncing] = useState<Record<string, string>>({}); // id -> message
  const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [orphaned, setOrphaned] = useState<Record<string, GitOrphanedDocument[]>>({});

  const mounted = useIsMounted();
  // Pending poll timeout(s), cleared on unmount so a long-running sync poller
  // (up to ~20 min) stops firing getTaskStatus / setState after navigation.
  const pollTimeouts = useRef<Set<ReturnType<typeof setTimeout>>>(new Set());

  useEffect(() => {
    const timeouts = pollTimeouts.current;
    return () => {
      timeouts.forEach((t) => clearTimeout(t));
      timeouts.clear();
    };
  }, []);

  const fetchConnections = useCallback(async () => {
    try {
      setLoading(true);
      setConnections(await api.listGitConnections());
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load connections");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchConnections();
  }, [fetchConnections]);

  useEffect(() => {
    if (!collectionsEnabled) return;
    let cancelled = false;
    api
      .getCollections()
      .then((res) => {
        if (!cancelled) setCollections(res.collections);
      })
      .catch(() => {
        // Collections unavailable → keep the picker hidden; sync still works.
        if (!cancelled) setCollections([]);
      });
    return () => {
      cancelled = true;
    };
  }, [collectionsEnabled]);

  const pollSync = useCallback(
    async (connectionId: string, taskId: string) => {
      // Poll the task until it finishes, then refresh the list.
      for (let i = 0; i < 600; i++) {
        await new Promise<void>((resolve) => {
          const t = setTimeout(() => {
            pollTimeouts.current.delete(t);
            resolve();
          }, 2000);
          pollTimeouts.current.add(t);
        });
        if (!mounted.current) return;
        try {
          const task = await api.getTaskStatus(taskId);
          if (!mounted.current) return;
          setSyncing((prev) => ({ ...prev, [connectionId]: task.message || "Syncing..." }));
          if (task.status === "completed" || task.status === "failed") {
            if (task.status === "failed") {
              setError(task.error || "Sync failed");
            }
            break;
          }
        } catch {
          break;
        }
      }
      if (!mounted.current) return;
      setSyncing((prev) => {
        const next = { ...prev };
        delete next[connectionId];
        return next;
      });
      await fetchConnections();
    },
    [fetchConnections, mounted],
  );

  const handleSync = async (connectionId: string) => {
    setError(null);
    setSyncing((prev) => ({ ...prev, [connectionId]: "Starting..." }));
    try {
      const res = await api.syncGitConnection(connectionId);
      pollSync(connectionId, res.task_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to start sync");
      setSyncing((prev) => {
        const next = { ...prev };
        delete next[connectionId];
        return next;
      });
    }
  };

  const handleDelete = async (connectionId: string, purge: boolean) => {
    try {
      await api.deleteGitConnection(connectionId, purge);
      setConnections((prev) => prev.filter((c) => c.id !== connectionId));
      setDeleteConfirm(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete connection");
    }
  };

  const loadOrphaned = async (connectionId: string) => {
    try {
      const res = await api.getGitOrphanedDocuments(connectionId);
      setOrphaned((prev) => ({ ...prev, [connectionId]: res.documents }));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load orphaned documents");
    }
  };

  const toggleExpand = (id: string) => {
    const next = expanded === id ? null : id;
    setExpanded(next);
    if (next) loadOrphaned(next);
  };

  return (
    <div className="glass rounded-xl overflow-hidden">
      {/* Header */}
      <div className="px-6 py-4 border-b border-border/50">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <GitBranch className="w-5 h-5 text-accent" />
            <h2 className="text-lg font-semibold text-foreground">Git Integration</h2>
            {connections.length > 0 && (
              <span className="text-xs text-muted-foreground">({connections.length})</span>
            )}
          </div>
          <button
            onClick={() => setShowForm((s) => !s)}
            className="flex items-center gap-1 px-2 py-1 text-xs text-[var(--accent)] hover:bg-[var(--accent)]/10 rounded transition-colors"
          >
            <Plus className="w-3 h-3" />
            Connect repository
          </button>
        </div>
        <p className="text-muted-foreground text-sm mt-1">
          Ingest GitHub, GitLab, and Gitea repositories into the knowledge graph and let the agent
          read or open pull requests
        </p>
      </div>

      <div className="p-6 space-y-4">
        {error && <GitErrorNotice message={error} />}

        {/* Connect form */}
        <AnimatePresence>
          {showForm && (
            <motion.div
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: "auto", opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              className="overflow-hidden"
            >
              <ConnectForm
                collections={collections}
                onCreated={() => {
                  setShowForm(false);
                  fetchConnections();
                }}
                onError={setError}
              />
            </motion.div>
          )}
        </AnimatePresence>

        {/* Connection list */}
        {loading ? (
          <div className="flex items-center justify-center py-6">
            <Loader2 className="w-4 h-4 animate-spin text-muted-foreground" />
          </div>
        ) : connections.length === 0 ? (
          <p className="text-xs text-muted-foreground py-4 text-center">
            No repositories connected. Click &ldquo;Connect repository&rdquo; to add one.
          </p>
        ) : (
          <div className="space-y-1">
            {connections.map((conn) => {
              const orphanedDocs = orphaned[conn.id] || [];
              const isSyncing = conn.id in syncing;
              return (
                <div key={conn.id} className="border border-border/30 rounded-lg overflow-hidden">
                  <div className="flex items-center gap-2 px-3 py-2">
                    <button
                      onClick={() => toggleExpand(conn.id)}
                      className="text-muted-foreground hover:text-foreground transition-colors"
                    >
                      {expanded === conn.id ? (
                        <ChevronDown className="w-3 h-3" />
                      ) : (
                        <ChevronRight className="w-3 h-3" />
                      )}
                    </button>

                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="text-sm font-medium text-foreground truncate">
                          {conn.repo_owner}/{conn.repo_name}
                        </span>
                        <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] text-muted-foreground bg-muted border border-border/50">
                          {VENDOR_LABELS[conn.vendor]}
                        </span>
                        <span
                          className={`inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium ${
                            conn.access_level === "read_write"
                              ? "bg-amber-500/10 text-amber-400 border border-amber-500/20"
                              : "bg-blue-500/10 text-blue-400 border border-blue-500/20"
                          }`}
                        >
                          {conn.access_level === "read_write" ? "read/write" : "read-only"}
                        </span>
                        {conn.wiki_enabled && (
                          <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] text-muted-foreground bg-muted border border-border/50">
                            +wiki
                          </span>
                        )}
                      </div>
                      <p className="text-xs text-muted-foreground mt-0.5 flex items-center gap-1">
                        {isSyncing ? (
                          <>
                            <Loader2 className="w-3 h-3 animate-spin inline" />
                            {syncing[conn.id]}
                          </>
                        ) : conn.sync_status === "success" ? (
                          <>
                            <CheckCircle2 className="w-3 h-3 inline text-emerald-400" />
                            Synced {relativeTime(conn.last_synced_at)} · {conn.branch}
                          </>
                        ) : conn.sync_status === "partial" ? (
                          <>
                            <FileWarning className="w-3 h-3 inline text-amber-400" />
                            Partial sync {relativeTime(conn.last_synced_at)}
                          </>
                        ) : (
                          <>
                            <CircleSlash className="w-3 h-3 inline" />
                            Never synced
                          </>
                        )}
                      </p>
                    </div>

                    <button
                      onClick={() => handleSync(conn.id)}
                      disabled={isSyncing}
                      className="shrink-0 flex items-center gap-1 px-2 py-1 text-xs rounded bg-[var(--accent)]/10 text-[var(--accent)] border border-[var(--accent)]/20 hover:bg-[var(--accent)]/20 disabled:opacity-50 transition-colors"
                      title="Sync now"
                    >
                      <RefreshCw className={`w-3 h-3 ${isSyncing ? "animate-spin" : ""}`} />
                      Sync
                    </button>
                  </div>

                  <AnimatePresence>
                    {expanded === conn.id && (
                      <motion.div
                        initial={{ height: 0, opacity: 0 }}
                        animate={{ height: "auto", opacity: 1 }}
                        exit={{ height: 0, opacity: 0 }}
                        transition={{ duration: 0.15 }}
                        className="overflow-hidden"
                      >
                        <div className="px-3 py-2 border-t border-border/30 bg-muted/30 space-y-2 text-xs">
                          {editingId === conn.id ? (
                            <EditForm
                              collections={collections}
                              conn={conn}
                              onSaved={(updated) => {
                                setConnections((prev) =>
                                  prev.map((c) => (c.id === updated.id ? updated : c)),
                                );
                                setEditingId(null);
                              }}
                              onCancel={() => setEditingId(null)}
                              onError={setError}
                            />
                          ) : (
                          <>
                          <div className="grid grid-cols-2 gap-2">
                            <div>
                              <span className="text-muted-foreground">Token: </span>
                              <span className="text-foreground font-mono">{conn.pat_masked}</span>
                            </div>
                            <div>
                              <span className="text-muted-foreground">Schedule: </span>
                              <span className="text-foreground">
                                {conn.sync_interval_minutes > 0
                                  ? `every ${conn.sync_interval_minutes}m`
                                  : "manual"}
                              </span>
                            </div>
                            <div>
                              <span className="text-muted-foreground">Branch: </span>
                              <span className="text-foreground font-mono">{conn.branch || conn.default_branch}</span>
                            </div>
                            <div>
                              <span className="text-muted-foreground">Wiki: </span>
                              <span className="text-foreground">{conn.wiki_enabled ? "yes" : "no"}</span>
                            </div>
                            {collections.length > 0 && (
                              <div className="col-span-2">
                                <span className="text-muted-foreground">Collection: </span>
                                <span className="text-foreground">
                                  {collectionName(collections, conn.collection_id)}
                                </span>
                              </div>
                            )}
                            {conn.last_synced_sha && (
                              <div className="col-span-2">
                                <span className="text-muted-foreground">Last commit: </span>
                                <span className="text-foreground font-mono">
                                  {conn.last_synced_sha.slice(0, 10)}
                                </span>
                              </div>
                            )}
                            {(conn.include_globs.length > 0 || conn.exclude_globs.length > 0) && (
                              <div className="col-span-2">
                                <span className="text-muted-foreground">Filters: </span>
                                <span className="text-foreground">
                                  {conn.include_globs.map((g) => `+${g}`).concat(
                                    conn.exclude_globs.map((g) => `-${g}`),
                                  ).join(", ")}
                                </span>
                              </div>
                            )}
                          </div>

                          {/* Orphaned documents (source file removed from repo) */}
                          {orphanedDocs.length > 0 && (
                            <div className="p-2 rounded bg-amber-500/10 border border-amber-500/20">
                              <div className="flex items-center gap-1 text-amber-400 font-medium mb-1">
                                <FileWarning className="w-3 h-3" />
                                {orphanedDocs.length} document(s) flagged — source file removed from repo
                              </div>
                              <ul className="text-muted-foreground space-y-0.5 max-h-32 overflow-y-auto">
                                {orphanedDocs.map((d) => (
                                  <li key={d.id} className="font-mono truncate">
                                    {d.git_path}
                                  </li>
                                ))}
                              </ul>
                              <p className="text-[10px] text-muted-foreground mt-1">
                                Review and delete these from the Documents page if no longer needed.
                              </p>
                            </div>
                          )}

                          {/* Edit + Delete */}
                          {deleteConfirm === conn.id ? (
                            <div className="flex items-center gap-2 flex-wrap">
                              <span className="text-red-400">Delete connection?</span>
                              <button
                                onClick={() => handleDelete(conn.id, false)}
                                className="px-2 py-0.5 rounded bg-muted text-muted-foreground hover:text-foreground transition-colors"
                              >
                                Keep documents
                              </button>
                              <button
                                onClick={() => handleDelete(conn.id, true)}
                                className="px-2 py-0.5 rounded bg-red-500/20 text-red-400 hover:bg-red-500/30 transition-colors"
                              >
                                Delete + purge documents
                              </button>
                              <button
                                onClick={() => setDeleteConfirm(null)}
                                className="px-2 py-0.5 rounded bg-muted text-muted-foreground hover:text-foreground transition-colors"
                              >
                                Cancel
                              </button>
                            </div>
                          ) : (
                            <div className="flex items-center gap-4">
                              <button
                                onClick={() => setEditingId(conn.id)}
                                className="flex items-center gap-1 text-[var(--accent)]/70 hover:text-[var(--accent)] transition-colors"
                              >
                                <Pencil className="w-3 h-3" />
                                Edit
                              </button>
                              <button
                                onClick={() => setDeleteConfirm(conn.id)}
                                className="flex items-center gap-1 text-red-400/70 hover:text-red-400 transition-colors"
                              >
                                <Trash2 className="w-3 h-3" />
                                Delete connection
                              </button>
                            </div>
                          )}
                          </>
                          )}
                        </div>
                      </motion.div>
                    )}
                  </AnimatePresence>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

// =============================================================================
// Provider error translation
// =============================================================================
//
// The backend forwards the forge's own error text verbatim (token scrubbed),
// e.g. `gitlab GET …/projects/x → HTTP 403: {"error":"insufficient_scope",…}`.
// That is precise but opaque to someone who just created their first token.
// Match the well-known signatures and say what was set up wrong and how to
// fix it; the raw text stays available underneath for support.

type GitErrorExplanation = { title: string; fix: string };

const GITLAB_READ_FG = "Code: Download, Project: Read and Repository: Read (plus Wiki: Read for the wiki)";

function explainGitError(raw: string): GitErrorExplanation | null {
  const m = raw;
  const lower = m.toLowerCase();

  // GitLab fine-grained PAT (18.10+): names the exact missing permission.
  if (lower.includes("insufficient_granular_scope")) {
    const perm = m.match(/\[([^\]]+)\]/)?.[1];
    if (perm && /user:\s*read/i.test(perm)) {
      return {
        title: "GitLab asked for User: Read, which Cortex no longer needs.",
        fix: "Your Cortex backend is older than the git connector fix that tolerates profile-less tokens. Update Cortex, or grant User: Read on the token as a workaround.",
      };
    }
    return {
      title: perm
        ? `Your GitLab fine-grained token is missing the permission ${perm}.`
        : "Your GitLab fine-grained token is missing a permission.",
      fix: `In GitLab open the token → Group and project → tick ${perm ?? "the named permission"} for this repository, then retry. Read-only ingestion needs ${GITLAB_READ_FG}; read/write adds Branch, Commit, Merge Request and Work Item: Create.`,
    };
  }

  // GitLab classic scopes: lists the scopes the endpoint would accept.
  if (lower.includes("insufficient_scope")) {
    const scopes = m.match(/"scope"\s*:\s*"([^"]+)"/)?.[1];
    const wantsWrite = /write_repository|\bapi\b(?!.*read_api)/.test(scopes ?? "") && !/read_api/.test(scopes ?? "");
    return {
      title: "Your GitLab token is missing an API scope.",
      fix: scopes
        ? `GitLab accepts one of: ${scopes.split(/\s+/).join(", ")}. ${
            wantsWrite
              ? "This is a write action — the token needs the api scope (role Developer)."
              : "For read-only ingestion add read_api to the token: read_repository alone only covers git clone, not the project lookup Cortex does on connect."
          } Edit the token's scopes in GitLab or create a new one, then retry.`
        : "Add read_api (read-only) or api (read/write) to the token and retry.",
    };
  }

  // Gitea scoped tokens.
  if (lower.includes("required scope") || lower.includes("token does not have")) {
    return {
      title: "Your Gitea token is missing a scope.",
      fix: "Edit the token and grant Repository: Read. For read/write (pull requests + comments) grant Repository: Read and Write plus Issue: Read and Write.",
    };
  }

  // GitHub fine-grained token not covering this repo / permission.
  if (lower.includes("resource not accessible by personal access token")) {
    return {
      title: "The GitHub token can't reach this repository.",
      fix: "Edit the fine-grained token → Repository access → include this repository, and make sure Contents: Read-only is granted (Read and write plus Pull requests for read/write).",
    };
  }

  // Bad / expired / revoked token. (Provider 401 reaches the UI as a 403 so it is
  // never confused with an expired Cortex session.)
  if (/http 401|bad credentials|invalid_token|token is expired|token has expired|401 unauthorized|\b401\b/.test(lower)) {
    return {
      title: "The token was rejected as invalid or expired.",
      fix: "Check it was pasted completely (no leading or trailing spaces), that it hasn't expired or been revoked, and that the base URL points at the right server. Then Test again.",
    };
  }

  // Repository lookups: forges answer 404 for repos the token isn't allowed to see.
  if (/http 404/.test(lower) && /\/projects\/|\/repos\//.test(lower)) {
    const gitlab = lower.startsWith("gitlab");
    return {
      title: "Repository not found — or the token isn't allowed to see it.",
      fix: gitlab
        ? "Check the spelling: Owner is the full namespace (group/subgroup), Repository the project path. A project access token only sees its own project; a fine-grained personal token must have this repository selected. GitLab answers 404 instead of 403 for projects a token can't see."
        : "Check the owner and repository spelling. For a private repository the token must include it (GitHub fine-grained: Repository access → select this repository). GitHub answers 404 instead of 403 for repositories a token can't see.",
    };
  }

  // git clone / fetch authentication failures during sync.
  if (/git (clone|fetch|ls-remote) failed/.test(lower) && /authentication failed|access denied|could not read username|repository not found|403|401/.test(lower)) {
    return {
      title: "git could not authenticate against the repository during sync.",
      fix: "The token needs clone permission: GitLab read_repository (classic) or Code: Download (fine-grained); GitHub Contents: Read; Gitea Repository: Read. If the token was rotated or expired, paste a new one under Edit.",
    };
  }

  // Network / SSRF guard.
  if (lower.includes("request blocked") || lower.includes("request failed") || /http 502/.test(lower)) {
    return {
      title: "Cortex couldn't reach the git server.",
      fix: "Check the base URL. For a self-hosted server on a private address the backend needs GIT_HTTP_ALLOW_PRIVATE=true; for a self-signed certificate add the host to GIT_HTTP_INSECURE_HOSTS.",
    };
  }

  return null;
}

function GitErrorNotice({ message, compact = false }: { message: string; compact?: boolean }) {
  const explained = explainGitError(message);
  if (!explained) {
    return (
      <div className="flex items-center gap-2 text-xs text-red-400 p-2 rounded bg-red-500/10 border border-red-500/20">
        <AlertCircle className="w-3 h-3 shrink-0" />
        <span>{message}</span>
      </div>
    );
  }
  return (
    <div className={`text-xs p-2 rounded bg-red-500/10 border border-red-500/20 space-y-1 ${compact ? "text-[11px]" : ""}`}>
      <p className="flex items-start gap-2 text-red-400 font-medium">
        <AlertCircle className="w-3 h-3 shrink-0 mt-0.5" />
        <span>{explained.title}</span>
      </p>
      <p className="text-foreground/90 pl-5">{explained.fix}</p>
      <details className="pl-5">
        <summary className="cursor-pointer text-muted-foreground hover:text-foreground text-[10px]">
          Show the provider&rsquo;s response
        </summary>
        <code className="block mt-1 font-mono text-[10px] text-muted-foreground break-all whitespace-pre-wrap">{message}</code>
      </details>
    </div>
  );
}

// =============================================================================
// Connect form
// =============================================================================

function ConnectForm({
  collections,
  onCreated,
  onError,
}: {
  collections: Collection[];
  onCreated: () => void;
  onError: (msg: string | null) => void;
}) {
  const [vendor, setVendor] = useState<GitVendor>("github");
  const [collectionId, setCollectionId] = useState(DEFAULT_COLLECTION_VALUE);
  const [baseUrl, setBaseUrl] = useState("");
  const [pat, setPat] = useState("");
  const [owner, setOwner] = useState("");
  const [repo, setRepo] = useState("");
  const [accessLevel, setAccessLevel] = useState<GitAccessLevel>("read");
  const [wikiEnabled, setWikiEnabled] = useState(false);
  const [syncInterval, setSyncInterval] = useState(0);
  const [restrictToDocs, setRestrictToDocs] = useState(true);
  const [includeGlobs, setIncludeGlobs] = useState(DOC_DEFAULT_TEXT);
  const [excludeGlobs, setExcludeGlobs] = useState("");

  // Toggling the ".pdf and .md only" default. If the user has custom filters
  // defined, confirm before replacing/leaving them.
  const handleRestrictToggle = (next: boolean) => {
    const current = parseGlobs(includeGlobs);
    const hasCustom = current.length > 0 && !isDocDefault(current);
    if (hasCustom && !window.confirm(
      "Are you sure you want to change this setting? You have other file-type filters defined.",
    )) {
      return;
    }
    setRestrictToDocs(next);
    if (next) {
      setIncludeGlobs(DOC_DEFAULT_TEXT);
    } else {
      setShowAdvanced(true);
      if (current.length === 0) setIncludeGlobs(DOC_DEFAULT_TEXT);
    }
  };

  const [verifying, setVerifying] = useState(false);
  const [verifyResult, setVerifyResult] = useState<GitVerifyResponse | null>(null);
  const [creating, setCreating] = useState(false);
  const [showAdvanced, setShowAdvanced] = useState(false);

  const needsBaseUrl = vendor === "gitea" || vendor === "gitlab";

  const handleVerify = async () => {
    if (!pat.trim()) return;
    setVerifying(true);
    setVerifyResult(null);
    onError(null);
    try {
      const result = await api.verifyGitCredentials(vendor, pat.trim(), baseUrl.trim() || null);
      setVerifyResult(result);
    } catch (err) {
      setVerifyResult({ valid: false, message: err instanceof Error ? err.message : "Failed" });
    } finally {
      setVerifying(false);
    }
  };

  const handleCreate = async () => {
    if (!pat.trim() || !owner.trim() || !repo.trim()) return;
    setCreating(true);
    onError(null);
    try {
      await api.createGitConnection({
        vendor,
        base_url: baseUrl.trim() || null,
        repo_owner: owner.trim(),
        repo_name: repo.trim(),
        pat: pat.trim(),
        access_level: accessLevel,
        include_globs: restrictToDocs ? DOC_DEFAULT_GLOBS : parseGlobs(includeGlobs),
        exclude_globs: excludeGlobs.split(",").map((g) => g.trim()).filter(Boolean),
        wiki_enabled: wikiEnabled,
        sync_interval_minutes: syncInterval,
        collection_id: collectionId || null,
      });
      onCreated();
    } catch (err) {
      onError(err instanceof Error ? err.message : "Failed to create connection");
    } finally {
      setCreating(false);
    }
  };

  const inputCls =
    "w-full px-3 py-1.5 text-xs rounded-lg bg-background border border-border/50 text-foreground placeholder:text-muted-foreground focus:outline-none focus:border-[var(--accent)]/50";

  return (
    <div className="p-3 rounded-lg border border-border/30 bg-muted/20 space-y-3 mb-2">
      <div className="grid grid-cols-3 gap-2">
        <div>
          <label className="text-[10px] text-muted-foreground uppercase tracking-wider">Provider</label>
          <select
            value={vendor}
            onChange={(e) => setVendor(e.target.value as GitVendor)}
            className={inputCls}
          >
            <option value="github">GitHub</option>
            <option value="gitlab">GitLab</option>
            <option value="gitea">Gitea</option>
          </select>
        </div>
        <div className="col-span-2">
          <label className="text-[10px] text-muted-foreground uppercase tracking-wider">
            API base URL {needsBaseUrl ? "(self-hosted)" : "(optional)"}
          </label>
          <input
            type="text"
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
            placeholder={vendor === "github" ? "leave blank for github.com" : "https://git.example.com"}
            className={inputCls}
          />
        </div>
      </div>

      <div>
        <label className="text-[10px] text-muted-foreground uppercase tracking-wider">
          Personal access token
        </label>
        <div className="flex gap-2">
          <input
            type="password"
            value={pat}
            onChange={(e) => {
              setPat(e.target.value);
              setVerifyResult(null);
            }}
            placeholder="ghp_… / glpat_… / gitea token"
            className={inputCls}
          />
          <button
            onClick={handleVerify}
            disabled={verifying || !pat.trim()}
            className="shrink-0 px-3 py-1.5 text-xs rounded-lg bg-muted text-foreground border border-border/50 hover:bg-muted/70 disabled:opacity-50 transition-colors"
          >
            {verifying ? <Loader2 className="w-3 h-3 animate-spin" /> : "Test"}
          </button>
        </div>
        {verifyResult && verifyResult.valid && (
          <p className="text-[11px] mt-1 flex items-center gap-1 text-emerald-400">
            <CheckCircle2 className="w-3 h-3" />
            {verifyResult.login
              ? `Authenticated as ${verifyResult.login}`
              : verifyResult.message || "Token accepted"}
          </p>
        )}
        {verifyResult && !verifyResult.valid && (
          <div className="mt-1">
            <GitErrorNotice message={verifyResult.message || "Invalid credentials"} compact />
          </div>
        )}

        {/* Per-vendor token-generation guide */}
        {(() => {
          const guide = VENDOR_TOKEN_GUIDES[vendor];
          return (
            <div className="mt-2 p-2.5 rounded-lg bg-[var(--accent)]/5 border border-[var(--accent)]/15 text-[11px] space-y-1.5">
              <div className="flex items-center justify-between gap-2">
                <span className="flex items-center gap-1 text-foreground font-medium">
                  <HelpCircle className="w-3 h-3 text-[var(--accent)]" />
                  How to create a {VENDOR_LABELS[vendor]} token
                </span>
                <a
                  href={guide.settingsUrl(baseUrl)}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex items-center gap-1 text-[var(--accent)] hover:underline shrink-0"
                >
                  Open token settings <ExternalLink className="w-2.5 h-2.5" />
                </a>
              </div>
              <p className="text-muted-foreground">
                Recommended: <span className="text-foreground">{guide.tokenType}</span>
              </p>
              <ol className="list-decimal list-inside text-muted-foreground space-y-0.5">
                {guide.steps.map((s, i) => (
                  <li key={i}>{s}</li>
                ))}
              </ol>
              <div className="mt-1 p-2 rounded-md bg-background/60 border border-[var(--accent)]/20 space-y-1.5">
                <p className="flex items-center gap-1 text-foreground font-medium">
                  <KeyRound className="w-3 h-3 text-[var(--accent)]" />
                  Required permissions for {accessLevel === "read_write" ? "read/write" : "read-only"} access
                </p>
                {guide.permissions[accessLevel].map((group) => (
                  <div key={group.label} className="space-y-1">
                    <p className="text-[10px] uppercase tracking-wider text-muted-foreground">{group.label}</p>
                    <ul className="flex flex-wrap gap-1">
                      {group.items.map((item) => (
                        <li
                          key={item.name}
                          title={item.why}
                          className={`inline-flex items-baseline gap-1 px-1.5 py-0.5 rounded border text-[10.5px] ${
                            item.optional
                              ? "border-border text-muted-foreground"
                              : "border-[var(--accent)]/40 bg-[var(--accent)]/10 text-foreground"
                          }`}
                        >
                          <code className="font-mono">{item.name}</code>
                          <span className="text-muted-foreground">· {item.why}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                ))}
                <p className="text-muted-foreground">
                  The connect step fails with a 403 naming the missing permission if any of the highlighted ones is left out.
                </p>
              </div>
              <p className="text-muted-foreground">{guide.writeNote}</p>
              {guide.caveat && (
                <p className="text-amber-400/80 flex items-start gap-1">
                  <AlertCircle className="w-3 h-3 shrink-0 mt-0.5" />
                  <span>{guide.caveat}</span>
                </p>
              )}
            </div>
          );
        })()}
      </div>

      <div className="grid grid-cols-2 gap-2">
        <div>
          <label className="text-[10px] text-muted-foreground uppercase tracking-wider">Owner / org</label>
          <input value={owner} onChange={(e) => setOwner(e.target.value)} placeholder="octocat" className={inputCls} />
        </div>
        <div>
          <label className="text-[10px] text-muted-foreground uppercase tracking-wider">Repository</label>
          <input value={repo} onChange={(e) => setRepo(e.target.value)} placeholder="Hello-World" className={inputCls} />
        </div>
      </div>

      <div>
        <label className="text-[10px] text-muted-foreground uppercase tracking-wider">Access level</label>
        <select
          value={accessLevel}
          onChange={(e) => setAccessLevel(e.target.value as GitAccessLevel)}
          className={inputCls}
        >
          <option value="read">Read-only (ingest)</option>
          <option value="read_write">Read/write (agent can open PRs)</option>
        </select>
        <div className="mt-1.5">
          <AccessLevelNote level={accessLevel} />
        </div>
      </div>

      <CollectionPicker
        collections={collections}
        value={collectionId}
        onChange={setCollectionId}
        inputCls={inputCls}
        hint="Every file and wiki page synced from this repository is filed into this collection."
      />

      {/* Curated documents-only default */}
      <label className="flex items-start gap-2 text-xs text-foreground cursor-pointer">
        <input
          type="checkbox"
          checked={restrictToDocs}
          onChange={(e) => handleRestrictToggle(e.target.checked)}
          className="accent-[var(--accent)] mt-0.5"
        />
        <span>
          Only ingest <span className="font-mono">.pdf</span> and <span className="font-mono">.md</span> files
          <span className="text-muted-foreground"> (recommended)</span>
          <span className="block text-[10px] text-muted-foreground">
            Uncheck to define custom include/exclude globs.
          </span>
        </span>
      </label>

      {/* Advanced (optional) settings */}
      <div className="border-t border-border/30 pt-2">
        <button
          type="button"
          onClick={() => setShowAdvanced((s) => !s)}
          className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors"
        >
          {showAdvanced ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
          Advanced
        </button>

        <AnimatePresence>
          {showAdvanced && (
            <motion.div
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: "auto", opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              className="overflow-hidden"
            >
              <div className="space-y-3 pt-3">
                <div>
                  <label className="text-[10px] text-muted-foreground uppercase tracking-wider">
                    Auto-sync (minutes, 0 = manual)
                  </label>
                  <input
                    type="number"
                    min={0}
                    value={syncInterval}
                    onChange={(e) => setSyncInterval(Math.max(0, parseInt(e.target.value) || 0))}
                    className={inputCls}
                  />
                </div>

                {restrictToDocs ? (
                  <p className="text-[11px] text-muted-foreground">
                    Ingestion is restricted to <span className="font-mono">.pdf</span> and{" "}
                    <span className="font-mono">.md</span> files. Uncheck the option above to set custom globs.
                  </p>
                ) : (
                  <div className="grid grid-cols-2 gap-2">
                    <div>
                      <label className="text-[10px] text-muted-foreground uppercase tracking-wider">
                        Include globs (comma-sep)
                      </label>
                      <input
                        value={includeGlobs}
                        onChange={(e) => setIncludeGlobs(e.target.value)}
                        placeholder="src/**, docs/**"
                        className={inputCls}
                      />
                    </div>
                    <div>
                      <label className="text-[10px] text-muted-foreground uppercase tracking-wider">
                        Exclude globs (comma-sep)
                      </label>
                      <input
                        value={excludeGlobs}
                        onChange={(e) => setExcludeGlobs(e.target.value)}
                        placeholder="**/node_modules/**, *.lock"
                        className={inputCls}
                      />
                    </div>
                  </div>
                )}

                <label className="flex items-center gap-2 text-xs text-foreground cursor-pointer">
                  <input
                    type="checkbox"
                    checked={wikiEnabled}
                    onChange={(e) => setWikiEnabled(e.target.checked)}
                    className="accent-[var(--accent)]"
                  />
                  Also ingest the repository wiki
                </label>
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>

      <div className="flex justify-end gap-2 pt-1">
        <button
          onClick={handleCreate}
          disabled={creating || !pat.trim() || !owner.trim() || !repo.trim()}
          className="flex items-center gap-1 px-3 py-1.5 text-xs rounded-lg bg-[var(--accent)]/10 text-[var(--accent)] border border-[var(--accent)]/20 hover:bg-[var(--accent)]/20 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
        >
          {creating ? <Loader2 className="w-3 h-3 animate-spin" /> : <Plus className="w-3 h-3" />}
          Connect
        </button>
      </div>
    </div>
  );
}

// =============================================================================
// Edit form (existing connection)
// =============================================================================

function EditForm({
  conn,
  collections,
  onSaved,
  onCancel,
  onError,
}: {
  conn: GitConnection;
  collections: Collection[];
  onSaved: (updated: GitConnection) => void;
  onCancel: () => void;
  onError: (msg: string | null) => void;
}) {
  const [accessLevel, setAccessLevel] = useState<GitAccessLevel>(conn.access_level);
  const [collectionId, setCollectionId] = useState(conn.collection_id ?? DEFAULT_COLLECTION_VALUE);
  const [branch, setBranch] = useState(conn.branch || "");
  const [syncInterval, setSyncInterval] = useState(conn.sync_interval_minutes);
  const [restrictToDocs, setRestrictToDocs] = useState(isDocDefault(conn.include_globs));
  const [includeGlobs, setIncludeGlobs] = useState(
    conn.include_globs.length ? conn.include_globs.join(", ") : DOC_DEFAULT_TEXT,
  );
  const [excludeGlobs, setExcludeGlobs] = useState(conn.exclude_globs.join(", "));
  const [wikiEnabled, setWikiEnabled] = useState(conn.wiki_enabled);
  const [pat, setPat] = useState("");
  const [saving, setSaving] = useState(false);

  const inputCls =
    "w-full px-3 py-1.5 text-xs rounded-lg bg-background border border-border/50 text-foreground placeholder:text-muted-foreground focus:outline-none focus:border-[var(--accent)]/50";

  const handleRestrictToggle = (next: boolean) => {
    const current = parseGlobs(includeGlobs);
    const hasCustom = current.length > 0 && !isDocDefault(current);
    if (hasCustom && !window.confirm(
      "Are you sure you want to change this setting? You have other file-type filters defined.",
    )) {
      return;
    }
    setRestrictToDocs(next);
    if (next) {
      setIncludeGlobs(DOC_DEFAULT_TEXT);
    } else if (current.length === 0) {
      setIncludeGlobs(DOC_DEFAULT_TEXT);
    }
  };

  const handleSave = async () => {
    setSaving(true);
    onError(null);
    try {
      const update: import("@/types").GitConnectionUpdate = {
        access_level: accessLevel,
        include_globs: restrictToDocs ? DOC_DEFAULT_GLOBS : parseGlobs(includeGlobs),
        exclude_globs: excludeGlobs.split(",").map((g) => g.trim()).filter(Boolean),
        wiki_enabled: wikiEnabled,
        sync_interval_minutes: syncInterval,
      };
      if (branch.trim()) update.branch = branch.trim();
      if (pat.trim()) update.pat = pat.trim();
      if (collectionId !== (conn.collection_id ?? DEFAULT_COLLECTION_VALUE)) {
        update.collection_id = collectionId || null;
      }
      const updated = await api.updateGitConnection(conn.id, update);
      onSaved(updated);
    } catch (err) {
      onError(err instanceof Error ? err.message : "Failed to update connection");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-3">
      <p className="text-[10px] text-muted-foreground">
        Editing <span className="text-foreground font-medium">{conn.repo_owner}/{conn.repo_name}</span>
        {" "}— provider and repository can&rsquo;t be changed; create a new connection to point elsewhere.
      </p>

      <div className="grid grid-cols-2 gap-2">
        <div>
          <label className="text-[10px] text-muted-foreground uppercase tracking-wider">Access level</label>
          <select
            value={accessLevel}
            onChange={(e) => setAccessLevel(e.target.value as GitAccessLevel)}
            className={inputCls}
          >
            <option value="read">Read-only (ingest)</option>
            <option value="read_write">Read/write (agent can open PRs)</option>
          </select>
        </div>
        <div>
          <label className="text-[10px] text-muted-foreground uppercase tracking-wider">Branch</label>
          <input
            value={branch}
            onChange={(e) => setBranch(e.target.value)}
            placeholder={conn.default_branch || "main"}
            className={inputCls}
          />
        </div>
      </div>

      <AccessLevelNote level={accessLevel} />

      <CollectionPicker
        collections={collections}
        value={collectionId}
        onChange={setCollectionId}
        inputCls={inputCls}
        hint={
          collectionId && collectionId !== (conn.collection_id ?? DEFAULT_COLLECTION_VALUE)
            ? "Saving moves the documents already synced from this repository into the selected collection."
            : "New and updated files from this repository are filed into this collection."
        }
      />

      <div>
        <label className="text-[10px] text-muted-foreground uppercase tracking-wider">
          Auto-sync (minutes, 0 = manual)
        </label>
        <input
          type="number"
          min={0}
          value={syncInterval}
          onChange={(e) => setSyncInterval(Math.max(0, parseInt(e.target.value) || 0))}
          className={inputCls}
        />
      </div>

      {/* Curated documents-only default */}
      <label className="flex items-start gap-2 text-xs text-foreground cursor-pointer">
        <input
          type="checkbox"
          checked={restrictToDocs}
          onChange={(e) => handleRestrictToggle(e.target.checked)}
          className="accent-[var(--accent)] mt-0.5"
        />
        <span>
          Only ingest <span className="font-mono">.pdf</span> and <span className="font-mono">.md</span> files
          <span className="text-muted-foreground"> (recommended)</span>
          <span className="block text-[10px] text-muted-foreground">
            Uncheck to define custom include/exclude globs.
          </span>
        </span>
      </label>

      {!restrictToDocs && (
        <div className="grid grid-cols-2 gap-2">
          <div>
            <label className="text-[10px] text-muted-foreground uppercase tracking-wider">
              Include globs (comma-sep)
            </label>
            <input
              value={includeGlobs}
              onChange={(e) => setIncludeGlobs(e.target.value)}
              placeholder="**/*.md"
              className={inputCls}
            />
          </div>
          <div>
            <label className="text-[10px] text-muted-foreground uppercase tracking-wider">
              Exclude globs (comma-sep)
            </label>
            <input
              value={excludeGlobs}
              onChange={(e) => setExcludeGlobs(e.target.value)}
              placeholder="**/node_modules/**"
              className={inputCls}
            />
          </div>
        </div>
      )}

      <label className="flex items-center gap-2 text-xs text-foreground cursor-pointer">
        <input
          type="checkbox"
          checked={wikiEnabled}
          onChange={(e) => setWikiEnabled(e.target.checked)}
          className="accent-[var(--accent)]"
        />
        Also ingest the repository wiki
      </label>

      <div>
        <label className="text-[10px] text-muted-foreground uppercase tracking-wider">
          Rotate token (optional)
        </label>
        <input
          type="password"
          value={pat}
          onChange={(e) => setPat(e.target.value)}
          placeholder="leave blank to keep current token"
          className={inputCls}
        />
      </div>

      <div className="flex justify-end gap-2 pt-1">
        <button
          onClick={onCancel}
          className="px-3 py-1.5 text-xs rounded-lg bg-muted text-muted-foreground hover:text-foreground transition-colors"
        >
          Cancel
        </button>
        <button
          onClick={handleSave}
          disabled={saving}
          className="flex items-center gap-1 px-3 py-1.5 text-xs rounded-lg bg-[var(--accent)]/10 text-[var(--accent)] border border-[var(--accent)]/20 hover:bg-[var(--accent)]/20 disabled:opacity-50 transition-colors"
        >
          {saving ? <Loader2 className="w-3 h-3 animate-spin" /> : <Save className="w-3 h-3" />}
          Save
        </button>
      </div>
    </div>
  );
}
