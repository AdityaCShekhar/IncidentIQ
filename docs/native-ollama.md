# Native Ollama on Apple Silicon

IncidentIQ's model adapter calls the native Ollama HTTP API on `127.0.0.1:11434`. Run the benchmark on the host with the existing Python environment. The local server and model weights run outside Docker.

This machine has an Apple M4 Pro and 24 GB unified memory. Native Ollama is installed at `/Applications/Ollama.app`, with its command linked at `/opt/homebrew/bin/ollama`. The tested runtime is Ollama 0.40.7. We selected `qwen3:4b-instruct` (Q4_K_M, approximately 2.5 GB of weights); runtime memory includes additional context and inference allocations.

Start and inspect the local runtime:

```sh
open -a Ollama
ollama list
ollama ps
curl -fsS http://127.0.0.1:11434/api/version
```

If setting up a different machine, install the [official macOS app](https://ollama.com/download/mac), then download the model:

```sh
ollama pull qwen3:4b-instruct
```

Run the four strategies on the frozen development split:

```sh
.venv/bin/python scripts/run_benchmark.py \
  --strategy all --model qwen3:4b-instruct \
  --timeout 120 --max-completion-tokens 1536 \
  --output-dir evals/reports/dev-comparison-qwen3-4b-native
```

The CLI accepts `--max-completion-tokens` from 64 through 4096. Its default remains 384 so previous configurations are reproducible. The explicit 1536 setting allows more room for structured reports and UUID citations; it is a different generation budget from the historical 0.5B comparison. Model and budget changes must both be disclosed when comparing results. The agent's overall 90-second deadline remains in force even when the per-request timeout is 120 seconds.

Successful reports include generation settings, raw model output, and token counts. Rejected diagnosis responses now retain their final bounded raw output, completed-call usage, and Ollama's `done_reason`; `length` indicates the generation limit was reached. Transport failures can still have unknown usage. Benchmark-process CPU/RSS excludes Ollama; inspect `ollama ps` or `/api/ps` for a separate loaded-model memory observation.

Only development cases are used by this command. Held-out scoring still requires an explicit `--split held_out --final-report`. A locally running model does not by itself establish diagnosis quality.

The benchmark uses the host loopback endpoint. The Compose API runs in a container, where `127.0.0.1` identifies the container; this setup does not add a Docker-to-host route or relax the adapter's loopback-only endpoint validation.

The [completed native comparison](../evals/reports/dev-comparison-qwen3-4b-native/comparison.md) scored rules 16/16, summary 4/16, RAG 1/16, and agent 1/16. All valid model reports abstained, so these results verify native execution but do not demonstrate useful root-cause diagnosis. See the [failure analysis](comparative-evaluation.md).
