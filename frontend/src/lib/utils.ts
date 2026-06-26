import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(1))} ${sizes[i]}`;
}

export function formatDate(dateString: string): string {
  return new Date(dateString).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/**
 * Clean a provider-prefixed model identifier for display.
 * Providers like Cloudflare Workers AI namespace models behind a path
 * (e.g. "workers-ai/@cf/google/gemma-4-26b-a4b-it") — show just the model id.
 */
export function formatModelName(model: string): string {
  if (!model) return model;
  const segments = model.split("/").filter(Boolean);
  return segments.length ? segments[segments.length - 1] : model;
}

/**
 * Clean an OpenAI-compatible API base for display.
 * Cloudflare AI Gateway URLs embed the account id, gateway name, and provider
 * path (".../v1/<account>/<gateway>/compat") — show just the gateway origin + /v1/.
 * Non-Cloudflare bases are returned unchanged.
 */
export function formatApiBase(apiBase: string): string {
  if (!apiBase) return apiBase;
  const match = apiBase.match(/^(https?:\/\/gateway\.ai\.cloudflare\.com\/v1\/)/i);
  return match ? match[1] : apiBase;
}

export function getFileTypeIcon(fileType: string): string {
  const types: Record<string, string> = {
    // Office documents
    ".pdf": "📄",
    ".docx": "📑",
    ".doc": "📑",
    ".xlsx": "📊",
    ".xls": "📊",
    ".pptx": "📽️",
    ".ppt": "📽️",
    // Web pages
    ".html": "🌐",
    ".htm": "🌐",
    // Text files
    ".txt": "📝",
    ".md": "📋",
    ".markdown": "📋",
    ".rst": "📋",
    // Images (OCR)
    ".png": "🖼️",
    ".jpg": "🖼️",
    ".jpeg": "🖼️",
    ".tiff": "🖼️",
    ".tif": "🖼️",
    ".bmp": "🖼️",
    // Audio (ASR)
    ".wav": "🎵",
    ".mp3": "🎵",
    ".webvtt": "💬",
    ".vtt": "💬",
    // LaTeX
    ".tex": "📐",
    ".latex": "📐",
    // XML schemas
    ".xml": "📋",
  };
  return types[fileType] || "📁";
}
