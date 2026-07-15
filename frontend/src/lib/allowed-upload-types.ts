/**
 * File extensions accepted for document upload.
 *
 * Single source of truth for the frontend — mirrors the backend's
 * `allowed_extensions` in `backend/app/config.py`. Keep the two in sync:
 * a type listed here but not in the backend fails upload with a 400; a
 * type only in the backend is silently filtered out by the upload UI.
 */
export const ALLOWED_UPLOAD_TYPES = [
  // Office documents
  ".pdf", ".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt",
  // Web pages
  ".html", ".htm",
  // Text files
  ".txt", ".md", ".mdx", ".markdown", ".rst",
  // Images (OCR)
  ".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp",
  // Audio (ASR)
  ".wav", ".mp3", ".webvtt", ".vtt",
  // LaTeX
  ".tex", ".latex",
  // XML schemas
  ".xml",
  // E-books
  ".epub",
];
