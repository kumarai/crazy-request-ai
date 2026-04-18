# Crazy AI

Development setup for the backend, frontend, and local dependencies.

## Prerequisites

- Python 3.12 recommended
- Docker and Docker Compose
- An OpenAI API key

The backend Docker image uses Python 3.12, so using Python 3.12 locally will match the container environment more closely.

## Backend Setup With `.venv`

Copy the example config first:

```bash
cp backend/config.toml.example backend/config.toml
```

If `backend/.venv` already exists, you can reuse it. Otherwise create it:

```bash
cd backend
python3.12 -m venv .venv
```

If your existing `.venv` was created with a different Python version, recreate it with Python 3.12 to match Docker more closely.

Activate the environment and install dependencies:

```bash
cd backend
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
```

If you do not want to activate the environment, you can install directly with:

```bash
backend/.venv/bin/pip install -r backend/requirements.txt
```

## Database Migrations

Alembic under `backend/alembic/` is the source of truth for schema changes.

Upgrade to the latest revision:

```bash
cd backend
source .venv/bin/activate
python -m app.db.migrations upgrade
```

Direct Alembic equivalents:

```bash
cd backend
alembic upgrade head
alembic downgrade -1
alembic downgrade 004
alembic downgrade base
alembic current
alembic history
```

## Run The Dev Stack

The root `docker-compose.yml` is set up for development.

Start everything:

```bash
docker compose up --build
```

Services:

- Backend API: `http://localhost:8000`
- Frontend: `http://localhost:3000`
- Flower: `http://localhost:5555`

Notes:

- The `migrate` service runs `python -m app.db.migrations upgrade` before app services start.
- Backend code is bind-mounted and runs with Uvicorn `--reload`.
- Frontend code is bind-mounted and runs with the Vite dev server.
- `celery-worker` and `celery-beat` use the live backend code mount, but they do not automatically restart on code changes.
- Sample RAG fixtures in `sample-data/` are mounted into backend containers at `/data`.

## Telecom JSON Fixture For RAG Testing

There is now a telecom support dataset under `sample-data/telecom-support/`.

Use it with a source like:

1. Open `http://localhost:3000/sources`
2. Create a source with:
   - Name: `telecom-support-json`
   - Type: `JSON files`
   - Path: `/data/telecom-support`
3. Click `Full Sync`
4. Run queries against just that source from the chat UI

If you do not want to use the mounted fixture path, you can also create a `JSON files` source with no path, open its `Files` panel, and click `Add Telecom Sample`. The frontend will generate dummy telecom JSON files and upload them through the normal object-storage flow.

The dataset is split into a few JSON files so chunk previews are easier to inspect:

- `mobile-network.json`
- `device-and-messaging.json`
- `billing-porting-and-roaming.json`
- `home-internet.json`

Good retrieval smoke tests:

- `why does my phone say SOS only after switching devices`
- `how do I activate esim on iphone with a qr code`
- `why am I getting roaming charges after a cruise`
- `my bank verification texts are not coming through`
- `fiber modem has a red los light`
- `internet gets slow every evening but only on wifi`

## Config File

Main backend settings live in:

- `backend/config.toml.example`
- `backend/config.toml`

At minimum, update:

- `openai_api_key`
- `security_encryption_key`
- `security_api_keys`
- `security_admin_api_keys`

## Query Flow — End to End

From the user's search input to the final streamed answer. Entry point: [backend/app/api/routes/query.py](backend/app/api/routes/query.py). SSE events are emitted throughout.

````text
POST /query  (QueryRequest: query, language, source_ids, top_k, …)
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│ A. Setup                                                        │
│   • Resolve LLM provider + agent models                         │
│   • Instantiate Retriever (chunks_repo, sources_repo, redis)    │
│   • SSE: THINKING "retrieving"                                  │
└─────────────────────────────────────────────────────────────────┘
    │
    ▼
╔═════════════════════════════════════════════════════════════════╗
║ B. RETRIEVE  (backend/app/rag/retriever.py::Retriever.retrieve) ║
╠═════════════════════════════════════════════════════════════════╣
║  1. Query expansion (parallel)   [model slot: summary]          ║
║       • HyDE — hypothetical code ANSWER  (HyDECache → LLM)      ║
║       • HyDE — N alternative QUESTIONS   (HyDECache → LLM)      ║
║  2. Embed all texts              [model slot: embedding]        ║
║       (EmbeddingCache → Embedder on miss)                       ║
║  3. Parallel multi-strategy search (source-scoped, no LLM):     ║
║       • Vector — content embeddings, per query                  ║
║       • Vector — summary embeddings, per query                  ║
║       • Vector — HyDE answer embedding                          ║
║       • BM25 — per query                                        ║
║       • Symbol — identifier-shaped tokens from query            ║
║       • Domain tag — keyword tokens                             ║
║  4. Reciprocal Rank Fusion (k=60)                               ║
║  5. Graph neighbor expansion + RRF merge (if use_graph)         ║
║  6. Trigram dedup  →  filter wiki/code                          ║
║  7. LLM rerank + diversity       [model slot: rerank]           ║
║  8. Build RetrievedChunk list                                   ║
║  ↳ returns: chunks, scope_confidence, total_searched, top_score ║
╚═════════════════════════════════════════════════════════════════╝
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│ C. Scope gate                                                   │
│   top_score < rag_scope_threshold?                              │
│     → SSE: SCOPE_WARN + DONE (faithfulness_passed=false)  [END] │
└─────────────────────────────────────────────────────────────────┘
    │ passes
    ▼
┌─────────────────────────────────────────────────────────────────┐
│ D. Emit sources                                                 │
│   SSE: SOURCES (ChunkPreview[] + total_searched)                │
└─────────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│ E. Generation                    [model slot: generation]       │
│   • SSE: THINKING "generating"                                  │
│   • PromptBuilder.assemble(query, chunks)                       │
│     → (prompt, rendered_chunks[])                               │
│   • generation_agent.run(prompt)  → full_response               │
└─────────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│ F. Parse & stream response                                      │
│   • Extract ```code``` blocks  → SSE: CODE (one per block)      │
│   • Extract [wiki:…](…) refs   → SSE: WIKI                      │
│   • Remainder (plain text)     → SSE: TEXT                      │
└─────────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│ G. Validation  (parallel via asyncio.gather)                    │
│   • SSE: THINKING "checking"                                    │
│   ┌────────────────────────────────┬────────────────────────┐   │
│   │ faithfulness_agent [followup]  │ followup_agent         │   │
│   │  query + rendered_chunks + resp│  [model slot: followup]│   │
│   │  → passed: bool                │  query + response +    │   │
│   │  (checks the SAME text the     │  chunk names           │   │
│   │   generator saw — truncated)   │  → questions[]         │   │
│   └────────────────────────────────┴────────────────────────┘   │
│   • If not passed → SSE: SCOPE_WARN                             │
│   • If questions  → SSE: FOLLOWUPS                              │
└─────────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│ H. Done                                                         │
│   SSE: DONE (chunks_used, sources_used, faithfulness_passed,    │
│              retrieval_ms, generation_ms, validation_ms,        │
│              latency_ms)                                        │
└─────────────────────────────────────────────────────────────────┘
````

Notes:

- **HyDE** generates both a hypothetical **answer** (classic HyDE) and multiple hypothetical **questions** (multi-query expansion). See [backend/app/rag/hyde.py](backend/app/rag/hyde.py).
- All Step 3 searches run concurrently via `asyncio.gather` and are scoped by `source_ids`.
- Fusion uses RRF with `k=60` ([backend/app/rag/fusion.py](backend/app/rag/fusion.py)).
- **Faithfulness** runs _after_ generation ([backend/app/agents/faithfulness_agent.py](backend/app/agents/faithfulness_agent.py)) and verifies every technical claim traces to retrieved context; it runs in parallel with followup-question generation to hide its latency.
- Transport is Server-Sent Events; early exits (retrieval error, scope miss, generation error) still emit a terminal `DONE` event.

### Model slots

Each LLM step in the flow uses a named **slot**, resolved per active provider by `LLMClient.resolve_model(slot)` ([backend/app/llm/client.py](backend/app/llm/client.py)). Switch providers via `llm_provider` in `backend/config.toml` or per-request `provider` override.

| Flow step          | Slot         | OpenAI (default)         | Anthropic                                  | Google               | Ollama             |
| ------------------ | ------------ | ------------------------ | ------------------------------------------ | -------------------- | ------------------ |
| B.1 HyDE expansion | `summary`    | `gpt-4o-mini`            | `claude-haiku-4-5`                         | `gemini-2.0-flash`   | `llama3.1:8b`      |
| B.2 Embedding      | `embedding`  | `text-embedding-3-small` | `text-embedding-3-small` (OpenAI fallback) | `text-embedding-004` | `nomic-embed-text-v2-moe` |
| B.7 Rerank         | `rerank`     | `gpt-4o`                 | `claude-haiku-4-5`                         | `gemini-2.0-flash`   | `llama3.1:8b`      |
| E. Generation      | `generation` | `gpt-4o`                 | `claude-sonnet-4`                          | `gemini-2.0-flash`   | `llama3.1:8b`      |
| G. Faithfulness    | `followup`   | `gpt-4o-mini`            | `claude-haiku-4-5`                         | `gemini-2.0-flash`   | `llama3.1:8b`      |
| G. Followups       | `followup`   | `gpt-4o-mini`            | `claude-haiku-4-5`                         | `gemini-2.0-flash`   | `llama3.1:8b`      |

Recommended picks:

- **Accuracy-first:** OpenAI (`gpt-4o` for generation + rerank, `gpt-4o-mini` for everything else) or Anthropic (`claude-sonnet-4` for generation, `claude-haiku-4-5` for rest).
- **Cost-first / local:** Ollama with `llama3.1:8b` + `nomic-embed-text-v2-moe` (note: embedding dim is 768, update `llm_embedding_dimensions`).
- **Faithfulness** currently borrows the `followup` slot — if you want stricter verification, consider giving it its own slot pointed at a stronger model (e.g. `gpt-4o`).

### Caching

Both caches live in [backend/app/rag/cache.py](backend/app/rag/cache.py), are Redis-backed, optional (silent fallthrough if Redis is unavailable), and fail-safe (write errors are swallowed).

| Cache            | Scope                                                   | Key                                      | TTL | Where used                                                                                             |
| ---------------- | ------------------------------------------------------- | ---------------------------------------- | --- | ------------------------------------------------------------------------------------------------------ |
| `HyDECache`      | HyDE LLM outputs — hypothetical code + expanded queries | `sha256(language/count + query + model)` | 24h | Step 1 — [backend/app/rag/hyde.py](backend/app/rag/hyde.py)                                            |
| `EmbeddingCache` | Query / HyDE-output embeddings                          | `sha256(model + text)`                   | 24h | Step 2 — `Retriever._embed_with_cache` in [backend/app/rag/retriever.py](backend/app/rag/retriever.py) |

Both caches are **index-independent** — keys depend only on `(query, model, params)`, never on source content, so they need no invalidation when sources re-index. Not currently cached: retrieval results (`RetrievedChunk[]`), generation output, faithfulness/followup outputs.
