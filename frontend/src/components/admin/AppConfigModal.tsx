"use client";

import { useState, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Settings,
  X,
  Loader2,
  AlertCircle,
  Eye,
  EyeOff,
  Check,
} from "lucide-react";
import { api } from "@/lib/api";
import { useBodyScrollLock } from "@/hooks/useBodyScrollLock";
import type { AppConfigVariable } from "@/types";

interface AppConfigModalProps {
  appId: string;
  appName: string;
  onClose: () => void;
  onSaved?: () => void;
}

export function AppConfigModal({
  appId,
  appName,
  onClose,
  onSaved,
}: AppConfigModalProps) {
  const [variables, setVariables] = useState<AppConfigVariable[] | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [visibleSecrets, setVisibleSecrets] = useState<Set<string>>(new Set());

  useBodyScrollLock(true);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [onClose]);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        setLoading(true);
        const config = await api.getAppConfig(appId);
        if (cancelled) return;
        setVariables(config.variables);
        setValues(config.values || {});
        if (config.variables.length === 0) {
          // No config declared — show a brief confirmation, then close.
          setSaved(true);
          setTimeout(() => {
            if (!cancelled) {
              onSaved?.();
              onClose();
            }
          }, 1200);
        }
      } catch (err) {
        if (!cancelled) {
          setError(
            err instanceof Error ? err.message : "Failed to load configuration"
          );
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [appId]);

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    try {
      // Masked secret values ("••••••••") are sent as-is — the backend
      // preserves the existing stored value for them.
      await api.saveAppConfig(appId, values);
      setSaved(true);
      setTimeout(() => {
        onSaved?.();
      }, 600);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save");
    } finally {
      setSaving(false);
    }
  };

  const toggleSecretVisibility = (name: string) => {
    setVisibleSecrets((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  };

  const canSave =
    variables &&
    variables
      .filter((v) => v.required)
      .every((v) => values[v.name]?.trim());

  return (
    <AnimatePresence>
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        className="fixed inset-0 bg-background/80 backdrop-blur-sm flex items-center justify-center z-50 p-4"
        onClick={onClose}
      >
        <motion.div
          initial={{ scale: 0.95, opacity: 0 }}
          animate={{ scale: 1, opacity: 1 }}
          exit={{ scale: 0.95, opacity: 0 }}
          className="bg-card rounded-xl border border-border p-6 max-w-lg w-full max-h-[80vh] overflow-y-auto"
          onClick={(e) => e.stopPropagation()}
        >
          {/* Header */}
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center gap-3">
              <div className="inline-flex items-center justify-center w-10 h-10 rounded-lg bg-[var(--accent)]/10">
                <Settings className="w-5 h-5 text-[var(--accent)]" />
              </div>
              <div>
                <h3 className="text-base font-semibold text-foreground">
                  Configure {appName}
                </h3>
                <p className="text-xs text-muted-foreground">App configuration</p>
              </div>
            </div>
            <button
              onClick={onClose}
              className="p-1.5 rounded-lg hover:bg-muted transition-colors text-muted-foreground hover:text-foreground"
            >
              <X className="w-4 h-4" />
            </button>
          </div>

          {/* Loading state */}
          {loading && (
            <div className="flex flex-col items-center justify-center py-8 gap-3">
              <Loader2 className="w-6 h-6 animate-spin text-[var(--accent)]" />
              <p className="text-sm text-muted-foreground">
                Loading configuration...
              </p>
            </div>
          )}

          {/* Error */}
          {error && (
            <div className="flex items-center gap-2 text-xs text-red-400 p-3 rounded-lg bg-red-500/10 border border-red-500/20 mb-4">
              <AlertCircle className="w-3.5 h-3.5 shrink-0" />
              <span>{error}</span>
            </div>
          )}

          {/* Success state */}
          {saved && !loading && (
            <div className="flex flex-col items-center justify-center py-6 gap-3">
              <div className="inline-flex items-center justify-center w-12 h-12 rounded-xl bg-emerald-500/10">
                <Check className="w-6 h-6 text-emerald-400" />
              </div>
              <p className="text-sm text-foreground font-medium">
                {variables && variables.length > 0
                  ? "Configuration saved"
                  : "No configuration needed"}
              </p>
              <p className="text-xs text-muted-foreground">
                {variables && variables.length > 0
                  ? "The app is ready to use."
                  : "This app works out of the box."}
              </p>
            </div>
          )}

          {/* Form fields */}
          {!loading && !saved && variables && variables.length > 0 && (
            <div className="space-y-4">
              <p className="text-xs text-muted-foreground">
                This app requires the following configuration to function.
                Saved secrets show as masked values — leave them untouched to
                keep the stored value.
              </p>

              {variables.map((variable) => (
                <div key={variable.name} className="space-y-1.5">
                  <label className="flex items-center gap-1.5 text-sm font-medium text-foreground">
                    {variable.name}
                    {variable.required && (
                      <span className="text-red-400 text-xs">*</span>
                    )}
                  </label>
                  {variable.description && (
                    <p className="text-xs text-muted-foreground">
                      {variable.description}
                    </p>
                  )}
                  <div className="relative">
                    <input
                      type={
                        variable.type === "secret" &&
                        !visibleSecrets.has(variable.name)
                          ? "password"
                          : "text"
                      }
                      value={values[variable.name] || ""}
                      onChange={(e) =>
                        setValues((prev) => ({
                          ...prev,
                          [variable.name]: e.target.value,
                        }))
                      }
                      placeholder={
                        variable.type === "secret"
                          ? "Enter secret value..."
                          : "Enter value..."
                      }
                      className="w-full px-3 py-2 text-sm rounded-lg bg-background border border-border/50 text-foreground placeholder:text-muted-foreground focus:outline-none focus:border-[var(--accent)]/50 pr-10"
                    />
                    {variable.type === "secret" && (
                      <button
                        type="button"
                        onClick={() => toggleSecretVisibility(variable.name)}
                        className="absolute right-2 top-1/2 -translate-y-1/2 p-1 text-muted-foreground hover:text-foreground transition-colors"
                      >
                        {visibleSecrets.has(variable.name) ? (
                          <EyeOff className="w-3.5 h-3.5" />
                        ) : (
                          <Eye className="w-3.5 h-3.5" />
                        )}
                      </button>
                    )}
                  </div>
                </div>
              ))}

              {/* Actions */}
              <div className="flex gap-3 pt-2">
                <button
                  onClick={onClose}
                  className="flex-1 py-2 text-sm bg-muted hover:bg-muted/80 text-foreground rounded-lg transition-colors"
                >
                  Cancel
                </button>
                <button
                  onClick={handleSave}
                  disabled={!canSave || saving}
                  className="flex-1 py-2 text-sm bg-[var(--accent)]/10 text-[var(--accent)] border border-[var(--accent)]/20 hover:bg-[var(--accent)]/20 disabled:opacity-50 disabled:cursor-not-allowed rounded-lg transition-colors flex items-center justify-center gap-2"
                >
                  {saving ? (
                    <>
                      <Loader2 className="w-3.5 h-3.5 animate-spin" />
                      Saving...
                    </>
                  ) : (
                    "Save Configuration"
                  )}
                </button>
              </div>
            </div>
          )}
        </motion.div>
      </motion.div>
    </AnimatePresence>
  );
}
