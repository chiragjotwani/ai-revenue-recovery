# Running a real reasoning model locally

Phase 4 works with **no model at all** — the default `mock` provider is
deterministic and covers dev, CI, and tests. This guide is for when you
want a real language model to produce diagnoses.

You do **not** load the model inside the backend. You run a separate local
server that hosts the model over HTTP, and point the backend at it. Any
server that speaks the OpenAI chat format works.

## Option A — small model via Ollama (fits a 6 GB GPU)

The contract's headline candidates (Qwen3-30B-A3B, Nemotron 3 Nano 30B) do
**not** fit in 6 GB of VRAM — a 30B model is ~17–18 GB even at 4-bit. A
3–4B model does. `qwen3:4b` is a reasonable local stand-in for development;
final model selection is deferred to the benchmark (KI-002).

1. Install Ollama: <https://ollama.com/download> (Windows installer).
2. Pull the model (~3 GB download):
   ```
   ollama pull qwen3:4b
   ```
   Ollama serves on `http://localhost:11434` and exposes an
   OpenAI-compatible API at `http://localhost:11434/v1`.
3. Point the backend at it. In `backend/.env` (running the backend
   directly):
   ```
   REASONING_PROVIDER=qwen
   AI_QWEN_BASE_URL=http://localhost:11434/v1
   AI_QWEN_MODEL=qwen3:4b
   ```
   Running the backend in Docker instead, use the host gateway:
   ```
   AI_QWEN_BASE_URL=http://host.docker.internal:11434/v1
   ```
   and add to the `backend` service env in `docker-compose.yml` (or an
   `.env` consumed by it).
4. Restart the backend. `POST /recovery/cases/{id}/diagnose` now calls the
   real model. Expect a few seconds per diagnosis on a laptop GPU.

## Option B — the 30B-A3B candidate on CPU + RAM

The 30B-A3B models are "mixture of experts": 30B total parameters but only
~3B active per token, so `llama.cpp` on CPU is viable if you have ~20 GB of
free system RAM. Slower than a GPU, but diagnosis is a background call.

1. Get a GGUF build of the model (e.g. a `Q4_K_M` quant, ~18 GB on disk).
2. Run `llama.cpp`'s server:
   ```
   llama-server -m qwen3-30b-a3b-Q4_K_M.gguf --host 0.0.0.0 --port 8081 \
     -c 8192 -ngl 20        # -ngl offloads some layers to the 6 GB GPU
   ```
3. Set:
   ```
   REASONING_PROVIDER=qwen
   AI_QWEN_BASE_URL=http://localhost:8081/v1
   AI_QWEN_MODEL=qwen3-30b-a3b
   ```

## Option C — rented cloud GPU (for the real benchmark)

To actually compare Qwen vs. Nemotron (Section 52) you want both 30B models
at usable speed. Rent a 24–80 GB GPU by the hour (RunPod / Vast / Lambda),
run vLLM or Ollama there, expose the endpoint, set `AI_QWEN_BASE_URL` /
`AI_NEMOTRON_BASE_URL` to it, run
`python backend/scripts/benchmark_diagnosis.py --provider qwen` and
`--provider nemotron`, then shut the box down.

## Verifying

```
curl -s $AI_QWEN_BASE_URL/models          # server is up
# then, with a diagnosed-able case:
curl -s -X POST localhost:8000/recovery/cases/<id>/diagnose | jq
```

`GET /recovery/cases/<id>` will show the `diagnosis` block with
`model_name` = `qwen` and the real `model_version`.
