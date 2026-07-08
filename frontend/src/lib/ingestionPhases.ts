/**
 * Derives a user-facing phase timeline from a document's processing state.
 *
 * The backend reports raw `progress_message` strings and a coarse percent.
 * This maps them onto the stable text-pipeline phases so the UI can show
 * WHERE in the journey a document is, not just a moving string:
 *
 *   Convert → Chunk & Embed → Store → Extract
 *
 * Image analysis intentionally is NOT a linear step — it runs in the
 * background concurrently with extraction and has its own counters
 * (image_progress_*), so callers render it as a separate parallel row.
 */

export interface IngestionDocLike {
  processing_status: string;
  progress_current?: number;
  progress_total?: number;
  progress_message?: string;
  error_message?: string;
  image_progress_current?: number;
  image_progress_total?: number;
  image_progress_message?: string;
}

export type PhaseKey = "convert" | "chunk" | "store" | "extract";
export type PhaseState = "done" | "active" | "todo" | "failed";

export interface IngestionPhase {
  key: PhaseKey;
  label: string;
  state: PhaseState;
  /** Short live detail for the active phase, e.g. "812/1457 chunks" */
  detail?: string;
  /** 0..1 within-phase completion when the message carries counts */
  fraction?: number;
}

export interface IngestionPhaseInfo {
  phases: IngestionPhase[];
  /** Index of the active (or failed) phase; -1 when queued, phases.length when done */
  activeIndex: number;
  /** Cleaned one-line description of what is happening right now */
  statusLine: string;
  /** Overall percent 0..100 (backend progress, clamped) */
  percent: number;
  /** True while the text pipeline is running (not pending/terminal) */
  running: boolean;
  /** True when the document is queued and waiting for a processing slot */
  queued: boolean;
}

const PHASE_ORDER: PhaseKey[] = ["convert", "chunk", "store", "extract"];

const PHASE_LABELS: Record<PhaseKey, string> = {
  convert: "Convert",
  chunk: "Chunk & Embed",
  store: "Store",
  extract: "Extract",
};

interface ParsedMessage {
  phase: PhaseKey;
  detail?: string;
  fraction?: number;
}

/** Map a backend progress_message onto a phase + optional live counts. */
function parseMessage(message: string, status: string): ParsedMessage {
  const msg = message || "";

  let m = msg.match(/^Storing chunks: (\d+)\/(\d+)/i);
  if (m) {
    const done = parseInt(m[1], 10);
    const total = parseInt(m[2], 10);
    return {
      phase: "store",
      detail: `${done}/${total} chunks`,
      fraction: total > 0 ? done / total : undefined,
    };
  }
  if (/^Storing chunks in database/i.test(msg)) {
    return { phase: "store", detail: "Writing chunks" };
  }

  m = msg.match(/per-chunk relationships: (\d+)\/(\d+) chunks \((\d+) found\)/i);
  if (m) {
    const done = parseInt(m[1], 10);
    const total = parseInt(m[2], 10);
    return {
      phase: "extract",
      detail: `Relations: ${done}/${total} chunks · ${m[3]} found`,
      fraction: total > 0 ? done / total : undefined,
    };
  }
  if (/per-chunk relationships/i.test(msg)) {
    return { phase: "extract", detail: "Discovering relations" };
  }

  m = msg.match(/^Storing entity (\d+)\/(\d+)/i);
  if (m) {
    const done = parseInt(m[1], 10);
    const total = parseInt(m[2], 10);
    return {
      phase: "extract",
      detail: `Storing entities ${done}/${total}`,
      fraction: total > 0 ? done / total : undefined,
    };
  }
  if (/^Storing \d+ entities/i.test(msg)) {
    return { phase: "extract", detail: "Storing entities" };
  }
  m = msg.match(/^Finding entities: (\d+)\/(\d+) chunks/i);
  if (m) {
    const done = parseInt(m[1], 10);
    const total = parseInt(m[2], 10);
    return {
      phase: "extract",
      detail: `Finding entities ${done}/${total} chunks`,
      fraction: total > 0 ? done / total : undefined,
    };
  }
  if (/^Extracting (knowledge graph|entities)/i.test(msg)) {
    return { phase: "extract", detail: "Finding entities" };
  }

  m = msg.match(/^Generating embeddings for (\d+) chunks/i);
  if (m) {
    return { phase: "chunk", detail: `Embedding ${m[1]} chunks` };
  }
  if (/^Splitting into chunks/i.test(msg)) {
    return { phase: "chunk", detail: "Splitting text" };
  }

  m = msg.match(/^Converting document: page (\d+)\/(\d+)/i);
  if (m) {
    const done = parseInt(m[1], 10);
    const total = parseInt(m[2], 10);
    return {
      phase: "convert",
      detail: `Page ${done}/${total}`,
      fraction: total > 0 ? done / total : undefined,
    };
  }
  if (/^Waiting for a conversion slot/i.test(msg)) {
    return { phase: "convert", detail: "Waiting for a conversion slot" };
  }
  if (/^Converting/i.test(msg) || /^Starting|^Queued for reprocessing|^Reprocess/i.test(msg)) {
    return { phase: "convert", detail: "Reading & converting file" };
  }

  // Unknown message: infer from status so new backend strings degrade gracefully.
  if (status === "extracting") return { phase: "extract" };
  return { phase: "convert" };
}

export function deriveIngestionPhases(doc: IngestionDocLike): IngestionPhaseInfo {
  const status = doc.processing_status;
  const message = doc.progress_message || "";

  const percent =
    doc.progress_current && doc.progress_total
      ? Math.min(100, Math.max(0, Math.round((doc.progress_current / doc.progress_total) * 100)))
      : 0;

  if (status === "pending") {
    // A pending doc with a progress_message was queued by the system (bulk
    // reprocess, restart recovery); a bare pending doc simply hasn't been
    // processed yet and waits for the user to trigger extraction.
    return {
      phases: PHASE_ORDER.map((key) => ({ key, label: PHASE_LABELS[key], state: "todo" })),
      activeIndex: -1,
      statusLine: message
        ? `${message} — waiting for a processing slot`
        : "Unprocessed",
      percent: 0,
      running: false,
      queued: true,
    };
  }

  if (status === "completed") {
    return {
      phases: PHASE_ORDER.map((key) => ({ key, label: PHASE_LABELS[key], state: "done" })),
      activeIndex: PHASE_ORDER.length,
      statusLine: "Text pipeline complete",
      percent: 100,
      running: false,
      queued: false,
    };
  }

  const parsed = parseMessage(message, status);
  const activeIndex = PHASE_ORDER.indexOf(parsed.phase);
  const failed = status === "failed";

  const phases: IngestionPhase[] = PHASE_ORDER.map((key, i) => {
    const state: PhaseState =
      i < activeIndex ? "done" : i === activeIndex ? (failed ? "failed" : "active") : "todo";
    return {
      key,
      label: PHASE_LABELS[key],
      state,
      detail: i === activeIndex ? parsed.detail : undefined,
      fraction: i === activeIndex ? parsed.fraction : undefined,
    };
  });

  const statusLine = failed
    ? `Failed during ${PHASE_LABELS[parsed.phase]}`
    : parsed.detail
      ? `${PHASE_LABELS[parsed.phase]} — ${parsed.detail}`
      : message || "Processing...";

  return {
    phases,
    activeIndex,
    statusLine,
    percent,
    running: !failed,
    queued: false,
  };
}

/** Image analysis runs parallel to the text pipeline; separate accessor. */
export function deriveImageProgress(doc: IngestionDocLike) {
  const total = doc.image_progress_total ?? 0;
  const current = doc.image_progress_current ?? 0;
  const active = total > 0 && current < total;
  return {
    hasImages: total > 0,
    active,
    done: total > 0 && current >= total,
    current,
    total,
    percent: total > 0 ? Math.round((current / total) * 100) : 0,
    label: active ? `Analyzing images ${current}/${total}` : `${total} images analyzed`,
  };
}
