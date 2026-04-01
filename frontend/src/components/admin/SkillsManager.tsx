"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Puzzle,
  Plus,
  Trash2,
  ChevronDown,
  ChevronRight,
  Loader2,
  AlertCircle,
  Globe,
  FolderOpen,
  Link,
  Search,
  Download,
  Wrench,
  BookOpen,
  RefreshCw,
  ToggleLeft,
  ToggleRight,
  Settings,
  FileText,
  ChevronUp,
} from "lucide-react";
import { api } from "@/lib/api";
import { SkillConfigModal } from "./SkillConfigModal";
import type { SkillInfo, SkillRegistryItem } from "@/types";

export function SkillsManager() {
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expandedSkill, setExpandedSkill] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState<string | null>(null);

  // Install from URL
  const [installUrl, setInstallUrl] = useState("");
  const [installing, setInstalling] = useState(false);
  const [installError, setInstallError] = useState<string | null>(null);

  // Registry search
  const [registryQuery, setRegistryQuery] = useState("");
  const [registryResults, setRegistryResults] = useState<SkillRegistryItem[]>([]);
  const [searching, setSearching] = useState(false);
  const searchTimeout = useRef<NodeJS.Timeout | null>(null);

  // Delete confirmation
  const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null);

  // Config modal
  const [configModalSkillId, setConfigModalSkillId] = useState<string | null>(null);
  const [configModalSkillName, setConfigModalSkillName] = useState<string>("");

  // Skill body viewer
  const [viewingBodySkillId, setViewingBodySkillId] = useState<string | null>(null);
  const [skillBody, setSkillBody] = useState<string>("");
  const [loadingBody, setLoadingBody] = useState(false);

  const fetchSkills = useCallback(async () => {
    try {
      setLoading(true);
      const data = await api.listSkills();
      setSkills(data);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load skills");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchSkills();
  }, [fetchSkills]);

  const handleToggleSkill = async (skillId: string, currentEnabled: boolean) => {
    setActionLoading(skillId);
    try {
      const updated = await api.updateSkill(skillId, { enabled: !currentEnabled });
      setSkills((prev) => prev.map((s) => (s.skill_id === skillId ? updated : s)));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to update skill");
    } finally {
      setActionLoading(null);
    }
  };

  const handleDeleteSkill = async (skillId: string) => {
    setActionLoading(skillId);
    try {
      await api.deleteSkill(skillId);
      setSkills((prev) => prev.filter((s) => s.skill_id !== skillId));
      setDeleteConfirm(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete skill");
    } finally {
      setActionLoading(null);
    }
  };

  const openConfigWizardIfNeeded = async (skill: SkillInfo) => {
    try {
      const analysis = await api.analyzeSkillConfig(skill.skill_id);
      if (analysis.variables.length > 0) {
        setConfigModalSkillId(skill.skill_id);
        setConfigModalSkillName(skill.name);
      }
    } catch {
      // Analysis failure should not block the install
    }
  };

  const handleInstallFromUrl = async () => {
    if (!installUrl.trim()) return;
    setInstalling(true);
    setInstallError(null);
    try {
      const installed = await api.installSkill({ url: installUrl.trim() });
      setInstallUrl("");
      await fetchSkills();
      await openConfigWizardIfNeeded(installed);
    } catch (err) {
      setInstallError(err instanceof Error ? err.message : "Installation failed");
    } finally {
      setInstalling(false);
    }
  };

  const handleInstallFromRegistry = async (item: SkillRegistryItem) => {
    setActionLoading(`registry-${item.namespace}/${item.name}`);
    try {
      const installed = await api.installSkill({ registry_id: `${item.namespace}/${item.name}` });
      await fetchSkills();
      await openConfigWizardIfNeeded(installed);
    } catch (err) {
      setInstallError(err instanceof Error ? err.message : "Installation failed");
    } finally {
      setActionLoading(null);
    }
  };

  const handleRegistrySearch = useCallback(async (query: string) => {
    if (!query.trim()) {
      setRegistryResults([]);
      return;
    }
    setSearching(true);
    try {
      const results = await api.searchSkillRegistry(query.trim());
      setRegistryResults(results);
    } catch {
      setRegistryResults([]);
    } finally {
      setSearching(false);
    }
  }, []);

  const handleRegistryQueryChange = (value: string) => {
    setRegistryQuery(value);
    if (searchTimeout.current) clearTimeout(searchTimeout.current);
    searchTimeout.current = setTimeout(() => handleRegistrySearch(value), 300);
  };

  const handleDiscover = async () => {
    setActionLoading("discover");
    try {
      await api.discoverSkills();
      await fetchSkills();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Discovery failed");
    } finally {
      setActionLoading(null);
    }
  };

  const sourceIcon = (source: string) => {
    switch (source) {
      case "registry":
        return <Globe className="w-3 h-3" />;
      case "url":
        return <Link className="w-3 h-3" />;
      default:
        return <FolderOpen className="w-3 h-3" />;
    }
  };

  return (
    <div className="glass rounded-xl overflow-hidden">
      {/* Header */}
      <div className="px-6 py-4 border-b border-border/50">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <Puzzle className="w-5 h-5 text-accent" />
            <h2 className="text-lg font-semibold text-foreground">Agent Skills</h2>
            {skills.length > 0 && (
              <span className="text-xs text-muted-foreground">
                ({skills.filter((s) => s.enabled).length}/{skills.length} active)
              </span>
            )}
          </div>
          <button
            onClick={handleDiscover}
            disabled={actionLoading === "discover"}
            className="flex items-center gap-1 px-2 py-1 text-xs text-muted-foreground hover:text-foreground rounded transition-colors"
            title="Re-scan skills directory"
          >
            <RefreshCw className={`w-3 h-3 ${actionLoading === "discover" ? "animate-spin" : ""}`} />
            Discover
          </button>
        </div>
        <p className="text-muted-foreground text-sm mt-1">
          Extend Deep Research and Chat with external capabilities
        </p>
      </div>

      <div className="p-6 space-y-4">
        {/* Error */}
        {error && (
          <div className="flex items-center gap-2 text-xs text-red-400 p-2 rounded bg-red-500/10 border border-red-500/20">
            <AlertCircle className="w-3 h-3 shrink-0" />
            <span>{error}</span>
          </div>
        )}

        {/* Installed Skills */}
        <div>
          <h4 className="text-xs font-medium text-muted-foreground uppercase tracking-wider mb-2">
            Installed Skills
          </h4>

          {loading ? (
            <div className="flex items-center justify-center py-6">
              <Loader2 className="w-4 h-4 animate-spin text-muted-foreground" />
            </div>
          ) : skills.length === 0 ? (
            <p className="text-xs text-muted-foreground py-4 text-center">
              No skills installed. Install from a URL or browse the registry below.
            </p>
          ) : (
            <div className="space-y-1">
              {skills.map((skill) => (
                <div
                  key={skill.skill_id}
                  className="border border-border/30 rounded-lg overflow-hidden"
                >
                  {/* Skill row */}
                  <div className="flex items-center gap-2 px-3 py-2">
                    <button
                      onClick={() =>
                        setExpandedSkill(
                          expandedSkill === skill.skill_id ? null : skill.skill_id
                        )
                      }
                      className="text-muted-foreground hover:text-foreground transition-colors"
                    >
                      {expandedSkill === skill.skill_id ? (
                        <ChevronDown className="w-3 h-3" />
                      ) : (
                        <ChevronRight className="w-3 h-3" />
                      )}
                    </button>

                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="text-sm font-medium text-foreground truncate">
                          {skill.name}
                        </span>
                        {/* Type badge */}
                        <span
                          className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium ${
                            skill.skill_type === "tool"
                              ? "bg-emerald-500/10 text-emerald-400 border border-emerald-500/20"
                              : "bg-blue-500/10 text-blue-400 border border-blue-500/20"
                          }`}
                        >
                          {skill.skill_type === "tool" ? (
                            <Wrench className="w-2.5 h-2.5" />
                          ) : (
                            <BookOpen className="w-2.5 h-2.5" />
                          )}
                          {skill.skill_type}
                          {skill.tool_count > 0 && ` (${skill.tool_count})`}
                        </span>
                        {/* Source badge */}
                        <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] text-muted-foreground bg-muted border border-border/50">
                          {sourceIcon(skill.source)}
                          {skill.source}
                        </span>
                        {/* Config status badge */}
                        {skill.config_status === "needs_setup" && (
                          <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium bg-amber-500/10 text-amber-400 border border-amber-500/20">
                            Needs setup
                          </span>
                        )}
                      </div>
                      <p className="text-xs text-muted-foreground truncate mt-0.5">
                        {skill.description}
                      </p>
                    </div>

                    {/* Enable/disable toggle */}
                    <button
                      onClick={() => handleToggleSkill(skill.skill_id, skill.enabled)}
                      disabled={actionLoading === skill.skill_id}
                      className="shrink-0"
                      title={skill.enabled ? "Disable skill" : "Enable skill"}
                    >
                      {actionLoading === skill.skill_id ? (
                        <Loader2 className="w-5 h-5 animate-spin text-muted-foreground" />
                      ) : skill.enabled ? (
                        <ToggleRight className="w-6 h-6 text-[var(--accent)]" />
                      ) : (
                        <ToggleLeft className="w-6 h-6 text-muted-foreground" />
                      )}
                    </button>
                  </div>

                  {/* Expanded details */}
                  <AnimatePresence>
                    {expandedSkill === skill.skill_id && (
                      <motion.div
                        initial={{ height: 0, opacity: 0 }}
                        animate={{ height: "auto", opacity: 1 }}
                        exit={{ height: 0, opacity: 0 }}
                        transition={{ duration: 0.15 }}
                        className="overflow-hidden"
                      >
                        <div className="px-3 py-2 border-t border-border/30 bg-muted/30 space-y-2">
                          <div className="grid grid-cols-2 gap-2 text-xs">
                            {skill.version && (
                              <div>
                                <span className="text-muted-foreground">Version: </span>
                                <span className="text-foreground">{skill.version}</span>
                              </div>
                            )}
                            {skill.author && (
                              <div>
                                <span className="text-muted-foreground">Author: </span>
                                <span className="text-foreground">{skill.author}</span>
                              </div>
                            )}
                            {skill.license && (
                              <div>
                                <span className="text-muted-foreground">License: </span>
                                <span className="text-foreground">{skill.license}</span>
                              </div>
                            )}
                            {skill.tool_names.length > 0 && (
                              <div className="col-span-2">
                                <span className="text-muted-foreground">Tools: </span>
                                <span className="text-foreground">
                                  {skill.tool_names.join(", ")}
                                </span>
                              </div>
                            )}
                          </div>

                          {/* Actions */}
                          <div className="flex items-center gap-3">
                            <button
                              onClick={async () => {
                                if (viewingBodySkillId === skill.skill_id) {
                                  setViewingBodySkillId(null);
                                  return;
                                }
                                setLoadingBody(true);
                                try {
                                  const detail = await api.getSkill(skill.skill_id);
                                  setSkillBody(detail.body || "No content.");
                                  setViewingBodySkillId(skill.skill_id);
                                } catch {
                                  setSkillBody("Failed to load skill content.");
                                  setViewingBodySkillId(skill.skill_id);
                                } finally {
                                  setLoadingBody(false);
                                }
                              }}
                              className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors"
                            >
                              {loadingBody && viewingBodySkillId !== skill.skill_id ? (
                                <Loader2 className="w-3 h-3 animate-spin" />
                              ) : viewingBodySkillId === skill.skill_id ? (
                                <ChevronUp className="w-3 h-3" />
                              ) : (
                                <FileText className="w-3 h-3" />
                              )}
                              {viewingBodySkillId === skill.skill_id ? "Hide" : "View"} SKILL.md
                            </button>
                            <button
                              onClick={() => {
                                setConfigModalSkillId(skill.skill_id);
                                setConfigModalSkillName(skill.name);
                              }}
                              className="flex items-center gap-1 text-xs text-[var(--accent)]/70 hover:text-[var(--accent)] transition-colors"
                            >
                              <Settings className="w-3 h-3" />
                              Configure
                            </button>
                          </div>

                          {/* SKILL.md body viewer */}
                          {viewingBodySkillId === skill.skill_id && (
                            <div className="mt-2 p-3 rounded-lg bg-background border border-border/50 max-h-64 overflow-y-auto">
                              <pre className="text-xs text-foreground/80 whitespace-pre-wrap font-mono leading-relaxed">
                                {skillBody}
                              </pre>
                            </div>
                          )}

                          {deleteConfirm === skill.skill_id ? (
                            <div className="flex items-center gap-2">
                              <span className="text-xs text-red-400">Delete this skill?</span>
                              <button
                                onClick={() => handleDeleteSkill(skill.skill_id)}
                                className="px-2 py-0.5 text-xs rounded bg-red-500/20 text-red-400 hover:bg-red-500/30 transition-colors"
                              >
                                Confirm
                              </button>
                              <button
                                onClick={() => setDeleteConfirm(null)}
                                className="px-2 py-0.5 text-xs rounded bg-muted text-muted-foreground hover:text-foreground transition-colors"
                              >
                                Cancel
                              </button>
                            </div>
                          ) : (
                            <button
                              onClick={() => setDeleteConfirm(skill.skill_id)}
                              className="flex items-center gap-1 text-xs text-red-400/70 hover:text-red-400 transition-colors"
                            >
                              <Trash2 className="w-3 h-3" />
                              Uninstall
                            </button>
                          )}
                        </div>
                      </motion.div>
                    )}
                  </AnimatePresence>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Divider */}
        <div className="border-t border-border/30" />

        {/* Install from URL */}
        <div>
          <h4 className="text-xs font-medium text-muted-foreground uppercase tracking-wider mb-2">
            Install Skill
          </h4>
          <div className="flex gap-2">
            <input
              type="text"
              value={installUrl}
              onChange={(e) => setInstallUrl(e.target.value)}
              placeholder="SKILL.md URL (e.g. https://raw.githubusercontent.com/...)"
              className="flex-1 px-3 py-1.5 text-xs rounded-lg bg-background border border-border/50 text-foreground placeholder:text-muted-foreground focus:outline-none focus:border-[var(--accent)]/50"
              onKeyDown={(e) => e.key === "Enter" && handleInstallFromUrl()}
            />
            <button
              onClick={handleInstallFromUrl}
              disabled={installing || !installUrl.trim()}
              className="flex items-center gap-1 px-3 py-1.5 text-xs rounded-lg bg-[var(--accent)]/10 text-[var(--accent)] border border-[var(--accent)]/20 hover:bg-[var(--accent)]/20 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              {installing ? (
                <Loader2 className="w-3 h-3 animate-spin" />
              ) : (
                <Plus className="w-3 h-3" />
              )}
              Install
            </button>
          </div>
          {installError && (
            <p className="text-xs text-red-400 mt-1">{installError}</p>
          )}
        </div>

        {/* Divider */}
        <div className="border-t border-border/30" />

        {/* Registry Search */}
        <div>
          <div className="flex items-center justify-between mb-2">
            <h4 className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
              Browse Registry
            </h4>
            <a
              href="https://skills.sh"
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-1 text-[10px] text-[var(--accent)] hover:underline"
            >
              <Globe className="w-2.5 h-2.5" />
              skills.sh
            </a>
          </div>
          <div className="relative mb-2">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-3 h-3 text-muted-foreground" />
            <input
              type="text"
              value={registryQuery}
              onChange={(e) => handleRegistryQueryChange(e.target.value)}
              placeholder="Search skills..."
              className="w-full pl-8 pr-3 py-1.5 text-xs rounded-lg bg-background border border-border/50 text-foreground placeholder:text-muted-foreground focus:outline-none focus:border-[var(--accent)]/50"
            />
            {searching && (
              <Loader2 className="absolute right-3 top-1/2 -translate-y-1/2 w-3 h-3 animate-spin text-muted-foreground" />
            )}
          </div>

          {registryResults.length > 0 && (
            <div className="space-y-1 max-h-48 overflow-y-auto">
              {registryResults.map((item) => {
                const registryKey = `${item.namespace}/${item.name}`;
                const alreadyInstalled = skills.some(
                  (s) => s.skill_id === item.name || s.source_url?.includes(registryKey)
                );
                return (
                  <div
                    key={registryKey}
                    className="flex items-center gap-2 px-3 py-2 rounded border border-border/30 bg-muted/20"
                  >
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-1">
                        <span className="text-xs font-medium text-foreground">
                          {item.name}
                        </span>
                        <span className="text-[10px] text-muted-foreground">
                          by {item.namespace}
                        </span>
                      </div>
                      <p className="text-[11px] text-muted-foreground truncate">
                        {item.description}
                      </p>
                    </div>
                    {item.install_count != null && item.install_count > 0 && (
                      <span className="text-[10px] text-muted-foreground shrink-0">
                        <Download className="w-2.5 h-2.5 inline mr-0.5" />
                        {item.install_count >= 1000
                          ? `${(item.install_count / 1000).toFixed(1)}K`
                          : item.install_count}
                      </span>
                    )}
                    <button
                      onClick={() => handleInstallFromRegistry(item)}
                      disabled={
                        alreadyInstalled ||
                        actionLoading === `registry-${registryKey}`
                      }
                      className="shrink-0 px-2 py-1 text-[11px] rounded bg-[var(--accent)]/10 text-[var(--accent)] border border-[var(--accent)]/20 hover:bg-[var(--accent)]/20 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                    >
                      {actionLoading === `registry-${registryKey}` ? (
                        <Loader2 className="w-3 h-3 animate-spin" />
                      ) : alreadyInstalled ? (
                        "Installed"
                      ) : (
                        "Install"
                      )}
                    </button>
                  </div>
                );
              })}
            </div>
          )}

          {registryQuery && !searching && registryResults.length === 0 && (
            <p className="text-xs text-muted-foreground text-center py-2">
              No skills found for &ldquo;{registryQuery}&rdquo;.{" "}
              <a
                href={`https://skills.sh`}
                target="_blank"
                rel="noopener noreferrer"
                className="text-[var(--accent)] hover:underline"
              >
                Browse skills.sh
              </a>{" "}
              and install via URL.
            </p>
          )}
        </div>
      </div>

      {/* Config wizard modal */}
      <AnimatePresence>
        {configModalSkillId && (
          <SkillConfigModal
            skillId={configModalSkillId}
            skillName={configModalSkillName}
            onClose={() => setConfigModalSkillId(null)}
            onSaved={() => {
              setConfigModalSkillId(null);
              fetchSkills();
            }}
          />
        )}
      </AnimatePresence>
    </div>
  );
}
