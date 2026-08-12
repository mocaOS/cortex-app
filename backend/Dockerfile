FROM python:3.11-slim

WORKDIR /app

# Install system dependencies for Docling (PDF/image processing requires X11 libs, OCR)
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    git \
    libxcb1 \
    libx11-6 \
    libxext6 \
    libxrender1 \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

# torch flavor (mirrors Dockerfile.prod):
#   cuda — PyPI wheels, bundles ~2.5GB of nvidia-* deps
#   cpu  — CPU-only wheels, ~2.5GB smaller
# Nothing in this image installs the CUDA runtime or requests GPU devices, so
# docker-compose.yml defaults the build to `cpu`. The ARG default stays `cuda`
# so a bare `docker build` is unchanged.
ARG TORCH_VARIANT=cuda

# Copy requirements first for better caching (requirements.txt -r's the split files)
COPY requirements.txt requirements-base.txt requirements-ml.txt ./

# Install Python dependencies. CPU torch must be installed BEFORE
# requirements-ml.txt: with --extra-index-url pip resolves the highest version
# across indexes and would still take the CUDA wheel, so we satisfy the
# dependency from the CPU-only index up front.
RUN pip install --no-cache-dir -r requirements-base.txt && \
    if [ "$TORCH_VARIANT" = "cpu" ]; then \
        pip install --no-cache-dir \
            --index-url https://download.pytorch.org/whl/cpu \
            torch torchvision; \
    fi && \
    pip install --no-cache-dir -r requirements-ml.txt

# Deterministic HF cache location, independent of container user.
# Mount a volume here in production to keep the cache across container restarts.
ENV HF_HOME=/app/.cache/huggingface

# Customer stacks must not phone home (haystack ships posthog telemetry).
ENV HAYSTACK_TELEMETRY_ENABLED=False

# Pre-download sentence-transformers + cross-encoder during build so a cold image
# (no cache volume) still works without a runtime HF download.
RUN python -c "from sentence_transformers import SentenceTransformer, CrossEncoder; \
SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2'); \
CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2')"

# Copy application code
COPY . .

# Create uploads and custom_inputs directories
RUN mkdir -p /app/uploads /app/custom_inputs

# Expose port
EXPOSE 8000

# Run the application
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload", "--timeout-graceful-shutdown", "30"]
