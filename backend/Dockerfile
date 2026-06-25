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

# Copy requirements first for better caching (requirements.txt -r's the split files)
COPY requirements.txt requirements-base.txt requirements-ml.txt ./

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

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
