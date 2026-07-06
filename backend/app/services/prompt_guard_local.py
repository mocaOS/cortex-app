"""In-process prompt-guard classifier — the local fallback for the query-time
prompt-injection gate, mirroring the local cross-encoder reranker fallback.

Used ONLY when no `prompt_guard_service_url` is configured AND
`prompt_guard_local` is on (local dev / self-hosters without a cortex-helper).
The AaaS cloud always sets the service URL, so this never loads there — keeping
the per-instance footprint priority intact for the fleet.

Loads PIGuard (leolee99/PIGuard, MIT, deberta-v3) lazily on first use — the first
guarded question pays the download + load (persisted in the HF cache volume),
subsequent ones are fast. Binary classifier: id2label {0: benign, 1: injection};
we return the injection-class probability (index 1) as `score`.

Supply-chain note: PIGuard loads with trust_remote_code=True (custom modeling
code), so we pin the revision from settings (`prompt_guard_revision`).
"""

from __future__ import annotations

import logging
import threading

from app.config import get_settings

logger = logging.getLogger(__name__)

_model = None
_tokenizer = None
_device = None
_load_lock = threading.Lock()
_MAX_LENGTH = 512


def _get_model():
    """Lazy-load the classifier once (thread-safe). Returns None if torch /
    transformers are unavailable (e.g. the slim INSTALL_LOCAL_ML=false image)."""
    global _model, _tokenizer, _device
    if _model is not None:
        return _model, _tokenizer, _device
    with _load_lock:
        if _model is not None:
            return _model, _tokenizer, _device
        try:
            from transformers import AutoModelForSequenceClassification, AutoTokenizer
            import torch
        except Exception as exc:  # noqa: BLE001 — slim image has no torch
            logger.warning(
                "prompt_guard_local: torch/transformers unavailable, local guard "
                f"disabled ({exc}). Use a cortex-helper (PROMPT_GUARD_SERVICE_URL) instead."
            )
            return None, None, None
        settings = get_settings()
        model_id = settings.prompt_guard_model
        revision = settings.prompt_guard_revision or None
        device = "cuda" if torch.cuda.is_available() else "cpu"
        logger.info(
            f"prompt_guard_local: loading {model_id} (rev={revision or 'main'}) on {device}"
        )
        tokenizer = AutoTokenizer.from_pretrained(
            model_id, revision=revision, trust_remote_code=True
        )
        model = (
            AutoModelForSequenceClassification.from_pretrained(
                model_id, revision=revision, trust_remote_code=True
            )
            .to(device)
            .eval()
        )
        _model, _tokenizer, _device = model, tokenizer, device
        logger.info("prompt_guard_local: model loaded")
        return _model, _tokenizer, _device


def is_loaded() -> bool:
    return _model is not None


def classify(texts: list[str], threshold: float) -> list[dict] | None:
    """Classify texts in-process. Returns [{label, score, flagged}] in input
    order, or None if the model can't be loaded (fail-open, like the remote path)."""
    if not texts:
        return []
    model, tokenizer, device = _get_model()
    if model is None:
        return None
    try:
        import torch

        inputs = tokenizer(
            texts,
            return_tensors="pt",
            padding=True,
            truncation=True,
            max_length=_MAX_LENGTH,
        ).to(device)
        with torch.no_grad():
            probs = torch.softmax(model(**inputs).logits, dim=-1)
        id2label = getattr(model.config, "id2label", None)
        results = []
        for row in probs:
            row_list = [float(p) for p in row]
            injection = row_list[1] if len(row_list) > 1 else row_list[0]
            top_idx = int(max(range(len(row_list)), key=lambda i: row_list[i]))
            label = (
                id2label.get(top_idx, str(top_idx))
                if isinstance(id2label, dict)
                else str(top_idx)
            )
            results.append(
                {"label": label, "score": injection, "flagged": injection >= threshold}
            )
        return results
    except Exception as exc:  # noqa: BLE001 — never break the ask on a guard error
        logger.warning(f"prompt_guard_local: classify failed, failing open: {exc}")
        return None
