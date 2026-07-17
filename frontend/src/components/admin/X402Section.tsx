"use client";

import { useState, useEffect, useCallback } from "react";
import { motion } from "framer-motion";
import {
  Coins,
  Loader2,
  AlertCircle,
  CheckCircle2,
  XCircle,
  ShieldCheck,
  ShieldAlert,
  Lock,
  RotateCcw,
  Wallet,
} from "lucide-react";
import { api } from "@/lib/api";
import { useIsMounted } from "@/lib/hooks";
import type {
  X402ConfigResponse,
  X402ConfigUpdateRequest,
  X402VerifyResponse,
  X402EarningsResponse,
} from "@/types";

// =============================================================================
// Network / asset presets
// =============================================================================

interface NetworkPreset {
  id: string;
  label: string;
  network: string;
  /** Canonical USDC address/mint on this network (all 6 decimals) */
  usdc: string;
  /**
   * The token's on-chain EIP-712 domain name (contract `name()`), which
   * wallets sign against — NOT a display label. Circle's USDC differs per
   * deployment: "USD Coin" on Base/Avalanche mainnets, "USDC" on Base
   * Sepolia. A wrong value here makes every payment signature revert
   * on-chain (verified against the contracts 2026-07-17).
   */
  usdcName: string;
}

const NETWORK_PRESETS: NetworkPreset[] = [
  { id: "base", label: "Base", network: "eip155:8453", usdc: "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", usdcName: "USD Coin" },
  { id: "base-sepolia", label: "Base Sepolia (testnet)", network: "eip155:84532", usdc: "0x036CbD53842c5426634e7929541eC2318f3dCF7e", usdcName: "USDC" },
  { id: "avalanche", label: "Avalanche", network: "eip155:43114", usdc: "0xB97EF9Ef8734C71904D8002F8b6Bc66Dd9c48a6E", usdcName: "USD Coin" },
  // SPL token — no EIP-712 domain; the name is display-only on Solana.
  { id: "solana", label: "Solana", network: "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp", usdc: "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", usdcName: "USDC" },
];

const USDC_DEFAULTS = { name: "USDC", decimals: "6", eip712: "2" };

/** EVM (hex) addresses compare case-insensitively; Solana base58 is case-sensitive. */
function sameAsset(network: string, a: string, b: string): boolean {
  if (network.startsWith("eip155:")) return a.toLowerCase() === b.toLowerCase();
  return a === b;
}

const EVM_ADDRESS_RE = /^0x[0-9a-fA-F]{40}$/;

const inputCls =
  "w-full px-3 py-1.5 text-xs rounded-lg bg-background border border-border/50 text-foreground placeholder:text-muted-foreground focus:outline-none focus:border-[var(--accent)]/50 disabled:opacity-60 disabled:cursor-not-allowed";
const labelCls = "text-[10px] text-muted-foreground uppercase tracking-wider";

// =============================================================================
// x402 Payments section
// =============================================================================

export function X402Section() {
  const [config, setConfig] = useState<X402ConfigResponse | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const mounted = useIsMounted();

  // ---- form state -----------------------------------------------------------
  const [payTo, setPayTo] = useState("");
  const [facilitatorUrl, setFacilitatorUrl] = useState("");
  const [networkPreset, setNetworkPreset] = useState<string>("base"); // preset id or "custom"
  const [customNetwork, setCustomNetwork] = useState("");
  const [assetPreset, setAssetPreset] = useState<"usdc" | "custom">("usdc");
  const [assetAddress, setAssetAddress] = useState(NETWORK_PRESETS[0].usdc);
  const [assetName, setAssetName] = useState(NETWORK_PRESETS[0].usdcName);
  const [assetDecimals, setAssetDecimals] = useState(USDC_DEFAULTS.decimals);
  const [assetEip712, setAssetEip712] = useState(USDC_DEFAULTS.eip712);
  const [maxTimeout, setMaxTimeout] = useState("60");
  const [serviceName, setServiceName] = useState("");

  // Auth headers are a write-only secret: values are never returned by the API.
  // untouched = omit from the PUT body; "replace" = send parsed JSON; "clear" = send {}.
  const [headersMode, setHeadersMode] = useState<"untouched" | "replace" | "clear">("untouched");
  const [headersText, setHeadersText] = useState("");
  const [headersError, setHeadersError] = useState<string | null>(null);

  const [saving, setSaving] = useState(false);
  const [saveNote, setSaveNote] = useState<string | null>(null);
  const [verifying, setVerifying] = useState(false);
  const [verifyResult, setVerifyResult] = useState<X402VerifyResponse | null>(null);
  const [earnings, setEarnings] = useState<X402EarningsResponse | null>(null);

  /** Sync form fields from a server config response. */
  const applyConfig = useCallback((c: X402ConfigResponse) => {
    setPayTo(c.pay_to ?? "");
    setFacilitatorUrl(c.facilitator_url ?? "");

    const np = c.network ? NETWORK_PRESETS.find((p) => p.network === c.network) : undefined;
    if (np) {
      setNetworkPreset(np.id);
      setCustomNetwork("");
    } else if (c.network) {
      setNetworkPreset("custom");
      setCustomNetwork(c.network);
    } // else keep the default (Base) for a not-yet-configured instance

    const isPresetUsdc =
      !!np && !!c.asset_address && sameAsset(np.network, c.asset_address, np.usdc);
    if (c.asset_address) {
      setAssetPreset(isPresetUsdc ? "usdc" : "custom");
      setAssetAddress(c.asset_address);
      setAssetName(c.asset_name ?? USDC_DEFAULTS.name);
      setAssetDecimals(String(c.asset_decimals ?? 6));
      setAssetEip712(c.asset_eip712_version ?? USDC_DEFAULTS.eip712);
    }
    setMaxTimeout(String(c.max_timeout_seconds ?? 60));
    setServiceName(c.service_name ?? "");
    setHeadersMode("untouched");
    setHeadersText("");
    setHeadersError(null);
  }, []);

  const fetchEarnings = useCallback(async () => {
    try {
      const data = await api.getX402Earnings();
      if (mounted.current) setEarnings(data);
    } catch {
      // Earnings are informational; the config panel stays usable without them.
    }
  }, [mounted]);

  useEffect(() => {
    (async () => {
      try {
        const c = await api.getX402Config();
        if (!mounted.current) return;
        setConfig(c);
        applyConfig(c);
        if (c.enabled) fetchEarnings();
      } catch {
        // Feature gate fetch failed (older backend / flag off) — hide the section.
      } finally {
        if (mounted.current) setLoaded(true);
      }
    })();
  }, [applyConfig, fetchEarnings, mounted]);

  // ---- derived values -------------------------------------------------------
  const selectedNetworkPreset = NETWORK_PRESETS.find((p) => p.id === networkPreset);
  const network = selectedNetworkPreset ? selectedNetworkPreset.network : customNetwork.trim();
  const isEvm = network.startsWith("eip155:");
  const payToLooksOff = isEvm && payTo.trim() !== "" && !EVM_ADDRESS_RE.test(payTo.trim());

  const handleNetworkChange = (id: string) => {
    setNetworkPreset(id);
    const preset = NETWORK_PRESETS.find((p) => p.id === id);
    if (!preset) {
      // Custom network — there is no known USDC preset, so the asset is custom too.
      setAssetPreset("custom");
      return;
    }
    if (assetPreset === "usdc") {
      setAssetAddress(preset.usdc);
      setAssetName(preset.usdcName);
      setAssetDecimals(USDC_DEFAULTS.decimals);
      setAssetEip712(USDC_DEFAULTS.eip712);
    }
  };

  const handleAssetPresetChange = (value: "usdc" | "custom") => {
    setAssetPreset(value);
    if (value === "usdc" && selectedNetworkPreset) {
      setAssetAddress(selectedNetworkPreset.usdc);
      setAssetName(selectedNetworkPreset.usdcName);
      setAssetDecimals(USDC_DEFAULTS.decimals);
      setAssetEip712(USDC_DEFAULTS.eip712);
    }
  };

  /** Parse the auth-headers JSON textarea into a flat string→string record. */
  const parseHeaders = (): Record<string, string> | null => {
    const text = headersText.trim();
    if (!text) return {};
    try {
      const parsed: unknown = JSON.parse(text);
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
        setHeadersError("Must be a JSON object of header name → value");
        return null;
      }
      const record: Record<string, string> = {};
      for (const [k, v] of Object.entries(parsed as Record<string, unknown>)) {
        if (typeof v !== "string") {
          setHeadersError(`Header "${k}" must have a string value`);
          return null;
        }
        record[k] = v;
      }
      setHeadersError(null);
      return record;
    } catch {
      setHeadersError("Invalid JSON");
      return null;
    }
  };

  const decimalsNum = parseInt(assetDecimals, 10);
  const timeoutNum = parseInt(maxTimeout, 10);
  const canSave =
    payTo.trim() !== "" &&
    facilitatorUrl.trim() !== "" &&
    network !== "" &&
    assetAddress.trim() !== "" &&
    Number.isFinite(decimalsNum) &&
    decimalsNum >= 0 &&
    decimalsNum <= 18 &&
    Number.isFinite(timeoutNum) &&
    timeoutNum >= 10 &&
    timeoutNum <= 600 &&
    serviceName.trim().length <= 32 &&
    !saving;

  const handleSave = async () => {
    if (!canSave) return;
    setError(null);
    setSaveNote(null);
    setVerifyResult(null);

    const body: X402ConfigUpdateRequest = {
      pay_to: payTo.trim(),
      facilitator_url: facilitatorUrl.trim(),
      network,
      asset_address: assetAddress.trim(),
      asset_name: assetName.trim() || "USDC",
      asset_decimals: decimalsNum,
      asset_eip712_version: assetEip712.trim() || "2",
      max_timeout_seconds: timeoutNum,
      service_name: serviceName.trim() ? serviceName.trim() : null,
    };
    if (headersMode === "clear") {
      body.facilitator_auth_headers = {};
    } else if (headersMode === "replace") {
      const parsed = parseHeaders();
      if (parsed === null) return;
      body.facilitator_auth_headers = parsed;
    } // untouched → field omitted, stored headers stay as-is

    setSaving(true);
    try {
      const updated = await api.updateX402Config(body);
      if (!mounted.current) return;
      setConfig(updated);
      applyConfig(updated);
      setSaveNote(
        updated.verified
          ? "Configuration saved."
          : "Configuration saved — run verification to activate monetized keys."
      );
    } catch (err) {
      if (mounted.current) setError(err instanceof Error ? err.message : "Failed to save configuration");
    } finally {
      if (mounted.current) setSaving(false);
    }
  };

  const handleVerify = async () => {
    setError(null);
    setSaveNote(null);
    setVerifying(true);
    setVerifyResult(null);
    try {
      const result = await api.verifyX402Config();
      if (!mounted.current) return;
      setVerifyResult(result);
      setConfig((prev) =>
        prev ? { ...prev, verified: result.valid, verified_at: result.verified_at } : prev
      );
    } catch (err) {
      if (mounted.current) setError(err instanceof Error ? err.message : "Verification failed");
    } finally {
      if (mounted.current) setVerifying(false);
    }
  };

  // Hidden until the gating fetch resolves; hidden entirely when the env flag is off.
  if (!loaded || !config?.enabled) return null;

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: 0.105 }}
    >
      <div className="glass rounded-xl overflow-hidden">
        {/* Header */}
        <div className="px-6 py-4 border-b border-border/50">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <Coins className="w-5 h-5 text-accent" />
              <h2 className="text-lg font-semibold text-foreground">x402 Payments</h2>
              {config.verified ? (
                <span
                  title={
                    config.verified_at
                      ? `Verified ${new Date(config.verified_at).toLocaleString()}`
                      : undefined
                  }
                  className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs bg-emerald-500/10 text-emerald-400 border border-emerald-500/20"
                >
                  <ShieldCheck className="w-3 h-3" />
                  Verified
                </span>
              ) : (
                <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs bg-amber-500/10 text-amber-400 border border-amber-500/20">
                  <ShieldAlert className="w-3 h-3" />
                  Unverified
                </span>
              )}
            </div>
          </div>
          <p className="text-muted-foreground text-sm mt-1">
            Monetize retrieval with per-query micropayments on monetized API keys
          </p>
        </div>

        <div className="p-6 space-y-4">
          <p className="text-xs text-muted-foreground">
            Monetized public keys charge callers a price per query via the{" "}
            <span className="font-mono">x402</span> protocol. Payments go straight to the wallet
            configured below and are settled by any spec-compliant x402 facilitator — Cortex never
            holds funds.
          </p>

          {error && (
            <div className="flex items-center gap-2 text-xs text-red-400 p-2 rounded bg-red-500/10 border border-red-500/20">
              <AlertCircle className="w-3 h-3 shrink-0" />
              <span>{error}</span>
            </div>
          )}

          {/* Config form */}
          <div className="p-3 rounded-lg border border-border/30 bg-muted/20 space-y-3">
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
              <div>
                <label className={labelCls}>Network</label>
                <select
                  value={networkPreset}
                  onChange={(e) => handleNetworkChange(e.target.value)}
                  className={inputCls}
                >
                  {NETWORK_PRESETS.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.label}
                    </option>
                  ))}
                  <option value="custom">Custom…</option>
                </select>
              </div>
              <div className="sm:col-span-2">
                <label className={labelCls}>Recipient wallet (pay to)</label>
                <input
                  type="text"
                  value={payTo}
                  onChange={(e) => setPayTo(e.target.value)}
                  placeholder={isEvm || !network ? "0x…" : "wallet address"}
                  className={`${inputCls} font-mono`}
                />
                {payToLooksOff && (
                  <p className="text-[11px] text-amber-400 mt-1 flex items-center gap-1">
                    <AlertCircle className="w-3 h-3 shrink-0" />
                    Doesn&rsquo;t look like a 0x… EVM address — verification will confirm.
                  </p>
                )}
              </div>
            </div>

            {networkPreset === "custom" && (
              <div>
                <label className={labelCls}>Network identifier (CAIP-2)</label>
                <input
                  type="text"
                  value={customNetwork}
                  onChange={(e) => setCustomNetwork(e.target.value)}
                  placeholder="eip155:1"
                  className={`${inputCls} font-mono`}
                />
              </div>
            )}

            <div>
              <label className={labelCls}>Facilitator URL</label>
              <input
                type="text"
                value={facilitatorUrl}
                onChange={(e) => setFacilitatorUrl(e.target.value)}
                placeholder="https://x402.org/facilitator"
                className={inputCls}
              />
            </div>

            {/* Asset */}
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
              <div>
                <label className={labelCls}>Payment asset</label>
                <select
                  value={assetPreset}
                  onChange={(e) => handleAssetPresetChange(e.target.value as "usdc" | "custom")}
                  className={inputCls}
                >
                  {selectedNetworkPreset && <option value="usdc">USDC</option>}
                  <option value="custom">Custom…</option>
                </select>
              </div>
              <div className="sm:col-span-2">
                <label className={labelCls}>Asset address</label>
                <input
                  type="text"
                  value={assetAddress}
                  onChange={(e) => setAssetAddress(e.target.value)}
                  disabled={assetPreset === "usdc"}
                  placeholder="token contract / mint address"
                  className={`${inputCls} font-mono`}
                />
              </div>
            </div>

            <div className="grid grid-cols-3 gap-2">
              <div>
                <label className={labelCls}>Asset name</label>
                <input
                  type="text"
                  value={assetName}
                  onChange={(e) => setAssetName(e.target.value)}
                  disabled={assetPreset === "usdc"}
                  className={inputCls}
                />
              </div>
              <div>
                <label className={labelCls}>Decimals</label>
                <input
                  type="number"
                  min={0}
                  max={18}
                  value={assetDecimals}
                  onChange={(e) => setAssetDecimals(e.target.value)}
                  disabled={assetPreset === "usdc"}
                  className={inputCls}
                />
              </div>
              <div>
                <label className={labelCls}>EIP-712 version</label>
                <input
                  type="text"
                  value={assetEip712}
                  onChange={(e) => setAssetEip712(e.target.value)}
                  disabled={assetPreset === "usdc"}
                  className={inputCls}
                />
              </div>
            </div>

            <div className="grid grid-cols-2 gap-2">
              <div>
                <label className={labelCls}>Max timeout (seconds, 10–600)</label>
                <input
                  type="number"
                  min={10}
                  max={600}
                  value={maxTimeout}
                  onChange={(e) => setMaxTimeout(e.target.value)}
                  className={inputCls}
                />
              </div>
              <div>
                <label className={labelCls}>Service name (optional, max 32)</label>
                <input
                  type="text"
                  value={serviceName}
                  maxLength={32}
                  onChange={(e) => setServiceName(e.target.value)}
                  placeholder="shown to paying clients"
                  className={inputCls}
                />
              </div>
            </div>

            {/* Facilitator auth headers (write-only secret) */}
            <div>
              <label className={labelCls}>Facilitator auth headers (optional)</label>
              {headersMode === "untouched" && config.facilitator_auth_headers_set ? (
                <div className="flex items-center gap-2 mt-1 p-2 rounded-lg bg-muted/40 border border-border/40 text-xs text-muted-foreground">
                  <Lock className="w-3 h-3 text-emerald-400 shrink-0" />
                  <span className="flex-1">Auth headers configured — values are never shown.</span>
                  <button
                    onClick={() => {
                      setHeadersMode("replace");
                      setHeadersText("");
                    }}
                    className="px-2 py-0.5 rounded bg-muted text-foreground border border-border/50 hover:bg-muted/70 transition-colors"
                  >
                    Replace
                  </button>
                  <button
                    onClick={() => setHeadersMode("clear")}
                    className="px-2 py-0.5 rounded bg-red-500/10 text-red-400 border border-red-500/20 hover:bg-red-500/20 transition-colors"
                  >
                    Clear
                  </button>
                </div>
              ) : headersMode === "clear" ? (
                <div className="flex items-center gap-2 mt-1 p-2 rounded-lg bg-amber-500/5 border border-amber-500/15 text-xs text-muted-foreground">
                  <AlertCircle className="w-3 h-3 text-amber-400 shrink-0" />
                  <span className="flex-1">Stored auth headers will be removed on save.</span>
                  <button
                    onClick={() => setHeadersMode("untouched")}
                    className="flex items-center gap-1 px-2 py-0.5 rounded bg-muted text-foreground border border-border/50 hover:bg-muted/70 transition-colors"
                  >
                    <RotateCcw className="w-3 h-3" />
                    Undo
                  </button>
                </div>
              ) : (
                <>
                  <textarea
                    value={headersText}
                    onChange={(e) => {
                      setHeadersText(e.target.value);
                      setHeadersMode("replace");
                      setHeadersError(null);
                    }}
                    rows={2}
                    placeholder='{"Authorization": "Bearer …"}'
                    className={`${inputCls} font-mono resize-y`}
                  />
                  <p className="text-[10px] text-muted-foreground mt-0.5">
                    JSON object sent with facilitator requests. Treated as a secret — values are
                    never shown back.
                    {headersMode === "replace" && config.facilitator_auth_headers_set && (
                      <>
                        {" "}
                        <button
                          onClick={() => {
                            setHeadersMode("untouched");
                            setHeadersText("");
                            setHeadersError(null);
                          }}
                          className="text-[var(--accent)] hover:underline"
                        >
                          Keep existing headers
                        </button>
                      </>
                    )}
                  </p>
                  {headersError && (
                    <p className="text-[11px] text-red-400 mt-1 flex items-center gap-1">
                      <AlertCircle className="w-3 h-3 shrink-0" />
                      {headersError}
                    </p>
                  )}
                </>
              )}
            </div>

            {/* Actions */}
            <div className="flex items-center gap-2 pt-1">
              <button
                onClick={handleSave}
                disabled={!canSave}
                className="flex items-center gap-2 px-3 py-1.5 bg-accent hover:bg-accent/90 text-accent-foreground text-xs font-medium rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {saving && <Loader2 className="w-3 h-3 animate-spin" />}
                {saving ? "Saving..." : "Save configuration"}
              </button>
              <button
                onClick={handleVerify}
                disabled={verifying || saving || !config.configured}
                title={!config.configured ? "Save the configuration first" : undefined}
                className="flex items-center gap-2 px-3 py-1.5 text-xs rounded-lg bg-muted text-foreground border border-border/50 hover:bg-muted/70 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
              >
                {verifying ? (
                  <Loader2 className="w-3 h-3 animate-spin" />
                ) : (
                  <ShieldCheck className="w-3 h-3" />
                )}
                {verifying ? "Verifying..." : "Verify configuration"}
              </button>
              {saveNote && (
                <span className="text-[11px] text-muted-foreground">{saveNote}</span>
              )}
            </div>

            {/* Verification checklist */}
            {verifyResult && (
              <div
                className={`p-2.5 rounded-lg border space-y-1.5 ${
                  verifyResult.valid
                    ? "bg-emerald-500/5 border-emerald-500/15"
                    : "bg-red-500/5 border-red-500/15"
                }`}
              >
                <p
                  className={`text-xs font-medium flex items-center gap-1.5 ${
                    verifyResult.valid ? "text-emerald-400" : "text-red-400"
                  }`}
                >
                  {verifyResult.valid ? (
                    <>
                      <CheckCircle2 className="w-3.5 h-3.5" />
                      Configuration verified — monetized keys can now be created.
                    </>
                  ) : (
                    <>
                      <XCircle className="w-3.5 h-3.5" />
                      Verification failed
                    </>
                  )}
                </p>
                <ul className="space-y-1">
                  {verifyResult.checks.map((check) => (
                    <li key={check.check} className="flex items-start gap-1.5 text-[11px]">
                      {check.passed ? (
                        <CheckCircle2 className="w-3 h-3 text-emerald-400 shrink-0 mt-0.5" />
                      ) : (
                        <XCircle className="w-3 h-3 text-red-400 shrink-0 mt-0.5" />
                      )}
                      <span>
                        <span className="text-foreground">{check.label}</span>
                        {check.detail && (
                          <span className={check.passed ? "text-muted-foreground" : "text-red-400/80"}>
                            {" "}
                            — {check.detail}
                          </span>
                        )}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>

          {/* Earnings */}
          <div>
            <h3 className="text-sm font-medium text-foreground mb-2 flex items-center gap-2">
              <Wallet className="w-4 h-4 text-muted-foreground" />
              Earnings
            </h3>
            {earnings && earnings.payment_count > 0 ? (
              <div className="space-y-3">
                <div className="grid grid-cols-2 gap-3">
                  <div className="p-3 bg-muted/30 rounded-lg text-center">
                    <p className="text-xl font-semibold text-foreground">
                      {earnings.total_amount}
                      {earnings.asset_name && (
                        <span className="text-sm font-normal text-muted-foreground ml-1">
                          {earnings.asset_name}
                        </span>
                      )}
                    </p>
                    <p className="text-xs text-muted-foreground">Total earned</p>
                  </div>
                  <div className="p-3 bg-muted/30 rounded-lg text-center">
                    <p className="text-xl font-semibold text-foreground">
                      {earnings.payment_count.toLocaleString()}
                    </p>
                    <p className="text-xs text-muted-foreground">Paid queries</p>
                  </div>
                </div>
                {earnings.by_key.length > 0 && (
                  <div className="border border-border/30 rounded-lg overflow-hidden">
                    <div className="grid grid-cols-3 gap-2 px-3 py-1.5 bg-muted/30 text-[10px] text-muted-foreground uppercase tracking-wider">
                      <span>Key</span>
                      <span className="text-right">Payments</span>
                      <span className="text-right">Amount</span>
                    </div>
                    {earnings.by_key.map((row) => (
                      <div
                        key={row.key_id}
                        className="grid grid-cols-3 gap-2 px-3 py-1.5 text-xs border-t border-border/30"
                      >
                        <span className="text-foreground truncate">{row.key_name}</span>
                        <span className="text-right font-mono text-muted-foreground">
                          {row.payment_count.toLocaleString()}
                        </span>
                        <span className="text-right font-mono text-foreground">
                          {row.total_amount}
                          {earnings.asset_name ? ` ${earnings.asset_name}` : ""}
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            ) : (
              <p className="text-xs text-muted-foreground">
                No payments received yet. Earnings from monetized keys will appear here.
              </p>
            )}
          </div>
        </div>
      </div>
    </motion.div>
  );
}
