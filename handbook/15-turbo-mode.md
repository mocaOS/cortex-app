# Chapter 15: Turbo Mode (GPU Acceleration)

> ⚠️ **On hold — not currently available.** Turbo Mode is a Compute3 partnership prepared in 2025. The Compute3 service is not yet in production, so this feature is non-functional today. The integration code and `COMPUTE3_*` env vars remain in the codebase against future activation; you can ignore this chapter for production setups. If you've set `COMPUTE3_API_KEY` you'll see the toggle in the UI, but no live endpoint is reachable.

Turbo Mode enables GPU-accelerated LLM inference using Compute3, providing significantly faster response times, higher throughput, and potentially lower costs than cloud API providers for sustained workloads.

## What Is Compute3?

Compute3 is a distributed GPU compute platform that lets you rent dedicated GPUs and run open-source LLMs via vLLM. Key features:

- **Dedicated GPUs** — H100 and A100 GPUs allocated to your workload
- **vLLM inference** — High-performance model serving with tensor parallelism
- **Pay-per-use** — Billed by the second (or hour) for GPU time
- **Auto-scaling** — Adjust GPU count and runtime as needed

## Performance Comparison

| Metric | Standard (Cloud API) | Turbo (Compute3 GPU) |
|--------|---------------------|---------------------|
| Latency | 1-2 seconds | 300-500 milliseconds |
| Throughput | ~50 req/min (rate-limited) | ~200 req/min |
| Cost model | Per-token | Per-hour GPU rental |
| Best for | Low volume, variable load | Batch processing, sustained load |

## How Turbo Mode Works

1. You configure your Compute3 API key and GPU preferences
2. Start a GPU job — Compute3 spins up a vLLM instance on dedicated GPUs
3. The Library detects the running job and routes **all LLM requests** through the GPU endpoint
4. Both the primary model and graph extraction model are overridden to use Turbo
5. When done, stop the job to save costs

### Automatic Override

When Turbo Mode is active, it overrides:
- `get_llm_config()` — Primary model for Q&A, chat, research
- `get_extraction_llm_config()` — Entity extraction and relationship analysis model

This ensures maximum performance during batch operations. When Turbo is inactive, the Library falls back to your standard LLM configuration seamlessly.

### Health Checking

The Library continuously monitors the vLLM server health:
1. `GET /health` — Primary health check
2. `GET /v1/models` — OpenAI-compatible model listing
3. `POST /v1/chat/completions` — Probe with invalid request (any non-404 response means server is up)

The header indicator turns green only when the vLLM server is confirmed ready.

## Setup

### Step 1: Get a Compute3 Account

Sign up at compute3.ai and obtain an API key.

### Step 2: Configure Environment

```env
COMPUTE3_API_KEY=your-c3-api-key
COMPUTE3_API_BASE=https://api.compute3.ai
COMPUTE3_GPU_TYPE=h100              # h100 (recommended) or a100
COMPUTE3_GPU_COUNT=4                # Number of GPUs
COMPUTE3_MODEL=MiniMaxAI/MiniMax-M2.1  # Model to serve
COMPUTE3_DOCKER_IMAGE=vllm/vllm-openai:latest
COMPUTE3_DEFAULT_RUNTIME=3600       # 1 hour default
```

### Step 3: Start a GPU Job

**Via the web interface**: Settings > Turbo Mode > Start

**Via the API:**

```bash
curl -X POST http://localhost:8000/api/turbo/start \
  -H "X-API-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{"runtime_seconds": 3600}'
```

**With custom GPU configuration:**

```bash
curl -X POST "http://localhost:8000/api/turbo/start?runtime=7200&gpu_type=h100&gpu_count=4" \
  -H "X-API-Key: your-api-key"
```

The vLLM server starts with:
```bash
vllm serve {model} --host 0.0.0.0 --port 8000 \
  --tensor-parallel-size {gpu_count} \
  --trust-remote-code \
  --max-model-len 65536 \
  --gpu-memory-utilization 0.95
```

### Step 4: Wait for Readiness

The job takes 1-5 minutes to start (model download + initialization). The Library polls health every 5 seconds for up to 5 minutes.

Status progression:
1. **Queued** → Job submitted to Compute3
2. **Pending** → GPUs being allocated
3. **Running** → vLLM server starting
4. **Ready** → Health check passed, Turbo Mode enabled

## Managing Turbo Mode

```bash
# Check status (cached, fast)
curl http://localhost:8000/api/turbo/status \
  -H "X-API-Key: your-api-key"

# Check balance
curl http://localhost:8000/api/turbo/balance \
  -H "X-API-Key: your-api-key"

# Extend runtime
curl -X POST "http://localhost:8000/api/turbo/extend?additional_seconds=1800" \
  -H "X-API-Key: your-api-key"

# Stop (save costs!)
curl -X POST http://localhost:8000/api/turbo/stop \
  -H "X-API-Key: your-api-key"

# List all jobs
curl http://localhost:8000/api/turbo/jobs \
  -H "X-API-Key: your-api-key"

# Get job details
curl http://localhost:8000/api/turbo/jobs/{job_id} \
  -H "X-API-Key: your-api-key"

# Get job logs (useful for debugging)
curl http://localhost:8000/api/turbo/jobs/{job_id}/logs \
  -H "X-API-Key: your-api-key"
```

## Supported Models

| Model | Parameters | Speed | Quality | Use Case |
|-------|-----------|-------|---------|----------|
| `MiniMaxAI/MiniMax-M2.1` | Large | Fast | High | Default, general purpose |
| `Meta-Llama/Llama-3.1-70B` | 70B | Medium | High | Complex reasoning |
| `Meta-Llama/Llama-3.1-8B` | 8B | Very Fast | Good | High throughput |
| `Mistral-7B` | 7B | Very Fast | Good | Cost-effective |

## Fallback Behavior

If the GPU job is not running, becomes unavailable, or fails health checks:
- Turbo Mode is automatically disabled
- All requests fall back to your standard LLM provider
- No configuration change needed — failover is seamless
- The Library re-checks for running jobs on each status request

## Cost Optimization

1. **Batch your work** — Start Turbo, process all documents, run relationship analysis, then stop
2. **Set appropriate runtimes** — Don't leave jobs running overnight unless needed
3. **Right-size GPUs** — 4x H100 for large models, 2x A100 for medium, 1x for small
4. **Monitor balance** — Check `/api/turbo/balance` regularly
5. **Use the web indicator** — Green = active, Yellow = warming up, Not visible = not running

## Status Indicator

The web interface header shows a Turbo Mode indicator when `COMPUTE3_API_KEY` is configured:

| Color | Meaning |
|-------|---------|
| **Green** | GPU job active and vLLM server ready |
| **Yellow (pulsing)** | Job starting, vLLM warming up |
| **Not visible** | Turbo Mode not configured or no active job |

The indicator refreshes every 5-30 seconds depending on the current state.
