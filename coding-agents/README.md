# Multi-tenant Coding Agent

A small FastAPI service that exposes a per-tenant coding agent built on the
**OpenAI Agents SDK** sandbox harness. Each request is authenticated to a
tenant, given its own isolated sandbox workspace, and run to completion (or
streamed turn-by-turn).

```
client --(API key)--> FastAPI --> per-tenant SandboxAgent --> fresh sandbox session
                         |                ^                     (Unix-local | Docker | hosted)
                     auth -> Tenant       |
                                  invoke_subagents -> N worker sandbox sessions (parallel)
```

## What the SDK gives you (so you don't build it)

`SandboxAgent` + `Manifest` + `SandboxRunConfig` provide the agent loop,
Codex-style filesystem tools (`apply_patch`), a shell tool, sandbox lifecycle,
and snapshots. You supply the product logic: auth, tenant->workspace mapping,
limits, sandbox backend choice — and the extra tools (subagents, webfetch,
todos) layered on top.

## Layout

| File | Responsibility |
|------|----------------|
| `app/config.py`  | Settings from env / `.env` |
| `app/tenancy.py` | Tenant model, API-key registry, per-tenant Manifest, sandbox-client factory |
| `app/agent.py`   | Orchestrator + worker `SandboxAgent`s, function tools, run/stream entry points |
| `app/auth.py`    | `Authorization: Bearer` / `X-API-Key` -> `Tenant` |
| `app/main.py`    | FastAPI endpoints |

## Run it

```bash
uv sync                                   # installs deps from pyproject.toml / uv.lock
cp .env.example .env                      # set OPENAI_API_KEY (or export it) + DEFAULT_MODEL
uv run uvicorn app.main:app --reload
```

`OPENAI_API_KEY` is read straight from the environment; you don't need to put
it in `.env` if it's already exported. `DEFAULT_MODEL` defaults to `gpt-5.4`.

See [quickstart.md](quickstart.md) for a guided first request.

## Endpoints

| Method | Path                | Body                              | Returns |
|--------|---------------------|-----------------------------------|---------|
| GET    | `/healthz`          | —                                 | `{"status":"ok"}` (no auth) |
| GET    | `/v1/me`            | —                                 | The authenticated tenant |
| POST   | `/v1/tasks`         | `{prompt, model?}`                | Runs synchronously, returns final output |
| POST   | `/v1/tasks/stream`  | `{prompt, model?}`                | Streams events as SSE |

The demo tenant key `demo-key-acme` works out of the box:

```bash
curl -s localhost:8000/v1/me -H "Authorization: Bearer demo-key-acme"

curl -s localhost:8000/v1/tasks \
  -H "Authorization: Bearer demo-key-acme" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Create hello.py that prints the current date, then run it."}'
```

## Live event streaming (`/v1/tasks/stream`)

Use `curl -N` so events arrive unbuffered:

```bash
curl -N localhost:8000/v1/tasks/stream \
  -H "Authorization: Bearer demo-key-acme" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "List files in repo/ and create hello.txt with the count."}'
```

Each line is an SSE frame: `event: <type>\ndata: <json>\n\n`. Types:

| Event | Payload | Meaning |
|-------|---------|---------|
| `agent_updated`   | `{agent}`                                | A new agent took control (first event is the orchestrator) |
| `run_item`        | `{item, tool?, arguments?, output?, text?}` | A tool call, tool output, message, or reasoning step |
| `done`            | `{model, output}`                        | Terminal success event |
| `error`           | `{error, detail}`                        | Terminal failure (timeout, exception) |

`raw_response_event` (token-level deltas) is filtered out by default; flip
`_summarize_event` in `app/agent.py` to forward them if you want token streaming.
Tool arguments and outputs are truncated to 500 chars per event for stream
volume — adjust the slices in the same function.

The orchestrator's name on `agent_updated` is `coding-agent[<tenant_id>]`;
workers (if you wire per-worker streaming) are `worker[<tenant_id>]`.

## Agent architecture

Two `SandboxAgent`s, one orchestrator + ephemeral workers.

* **Orchestrator** keeps the long-running conversation, decomposes work, and
  integrates results. It has the SDK's built-in sandbox tools plus four
  function tools (see below).
* **Workers** are spawned by `invoke_subagents`. Each runs in its own fresh
  sandbox session sharing the tenant's workspace, gets a fresh context window,
  does one focused chunk of work, and returns a short report.

Why: long coding tasks blow the orchestrator's token budget if every file read
and command output sits in its context. Subagents absorb that detail and return
only conclusions.

## Tools on the orchestrator

| Tool | Source | What it does |
|------|--------|--------------|
| `apply_patch`       | SDK (`Filesystem` capability) | Edit/create/delete files via Codex-style patches |
| `view_image`        | SDK (`Filesystem` capability) | Open an image in the workspace |
| `exec_command`      | SDK (`Shell` capability)      | Run a shell command in the sandbox |
| `write_stdin`       | SDK (`Shell` capability)      | Write to a still-running process's stdin |
| `invoke_subagents`  | this repo                     | Fan out N worker subagents (parallel if you pass multiple subtasks) |
| `webfetch`          | this repo                     | GET an http(s) URL, return body (≤200 KB, 20s timeout) |
| `todo_write`        | this repo                     | Replace per-task todo list (id/content/status) |
| `todo_read`         | this repo                     | Read current todo list |

Plus the `Compaction` capability is on by default — automatic conversation
summarization when context fills. It's not a tool the model calls.

### Two caveats on the extra tools

* **`webfetch` runs in the FastAPI process**, not the sandbox. That means it
  uses your server's network identity and bypasses sandbox network policy. For
  per-tenant network isolation, replace it with a sandbox `curl` call instead.
* **Todo state is per-task** — it lives in a closure created in
  `build_sandbox_agent` and resets when a new request comes in. For
  cross-task persistence, use the SDK's opt-in `Memory` sandbox capability.

### Parallel subagents

When the orchestrator calls `invoke_subagents` with multiple subtasks:

* They run concurrently via `asyncio.gather`, each in its own sandbox session.
* They **share the same tenant workspace** (no file-level locking) — the
  orchestrator's instructions tell it to keep parallel work read-only or
  partition by file.
* Worker turn budget is half the orchestrator's (floor 5) so a runaway worker
  can't burn the whole allowance.
* Failed subagents come back as `[subagent N] FAILED: ...` strings rather than
  crashing the run.

On `/v1/tasks/stream` today, the fan-out shows up as **one** `tool_call_item`
for `invoke_subagents` followed (after the workers finish) by **one**
`tool_call_output_item` with the combined report. Per-worker events are not
forwarded inline; see "Where to watch what the agent is doing" below for how to
see them.

## Where to watch what the agent is doing

Three independent options, mostly already on:

1. **OpenAI traces dashboard** (zero code). Every `Runner.run` auto-uploads to
   <https://platform.openai.com/traces>. Orchestrator runs appear under workflow
   name `tenant:<id>`; workers under `tenant:<id>:worker:<n>`. Full turn-by-turn
   timeline, model I/O, tool calls. Disable with `set_tracing_disabled(True)` if
   you don't want it.
2. **Verbose stdout**. Add `from agents import enable_verbose_stdout_logging;
   enable_verbose_stdout_logging()` near the top of `app/main.py` and every
   LLM request, tool call, and tool output prints to the uvicorn console
   tagged with the agent name.
3. **SSE stream** (`/v1/tasks/stream`). Filtered, JSON-friendly events for the
   client — best for UIs and tailing.

## How tenants are isolated

* **Auth -> Tenant.** API keys are matched by SHA-256 hash (`app/tenancy.py`).
  The demo registry is in-memory; point `TENANTS_FILE` at `tenants.json`
  (see `tenants.example.json`) or swap `TenantRegistry` for a DB lookup.
* **Workspace.** `build_manifest` mounts *only* that tenant's project into the
  sandbox — either a local directory (`local_dir`) or a cloned repo
  (`git_repo`). The agent sees it as `repo/`.
* **Execution.** `make_sandbox_client` returns a **fresh** client per task, so
  every run gets its own session. With the `docker` backend that's one
  ephemeral container per request.

### Choosing a sandbox backend (this is the real security boundary)

| Backend | Isolation | Use for |
|---------|-----------|---------|
| `unix_local` | none — runs on the host fs | local dev only |
| `docker` | container per task | self-hosted prod |
| hosted (E2B / Modal / Daytona / Cloudflare / Runloop / Vercel) | micro-VM / managed container per task | scale-out prod |

The agent runs arbitrary shell, so **do not** serve real tenants on
`unix_local`. Use `docker` (`pip install "openai-agents[docker]"`, requires a
Docker daemon) or a hosted provider. To use a hosted provider, swap the client
constructed in `make_sandbox_client` for that provider's sandbox client.

> If you run the `docker` backend from inside the service container, the
> container needs access to a Docker daemon (mounted socket or DinD), which has
> its own security tradeoffs. Running the service on a host with Docker, or
> using a hosted sandbox provider, is usually cleaner.

## Per-tenant OpenAI keys (optional)

Set `openai_api_key` on a tenant to bill that tenant's runs to their own key.
`app/agent.py` then builds a dedicated `OpenAIResponsesModel` bound to that key
for that agent instance (concurrency-safe — no global client mutation).

## Production notes

* **Long tasks.** Both endpoints are bounded by `TASK_TIMEOUT_SECONDS`. At
  scale, enqueue the task (Celery / RQ / Arq), return `202` + a task id, and
  let clients poll `GET /v1/tasks/{id}`. Don't keep job state in process memory
  if you run multiple workers/replicas.
* **Cost.** Coding agents are token-heavy. `MAX_TURNS` caps a runaway loop; the
  orchestrator + subagent split keeps the main context small. Worker
  `max_turns` is auto-derived as half the orchestrator's (floor 5).
* **Sessions / multi-turn.** The current endpoints are one-shot. For follow-up
  turns, plug an `agents.Session` (e.g. `SQLiteSession`) into `Runner.run` and
  add session-scoped endpoints. The SDK auto-loads prior turns when a session
  is supplied.
* **Mid-run clarification.** Either gate `/v1/tasks` with a lightweight
  "clarifier" agent (one structured-output turn that returns
  `{ok, questions}`), or give the orchestrator an `ask_user` function tool
  backed by an SSE event + reply endpoint. The first is simpler and handles
  most cases.
* **Secrets.** Keep tenant keys in a secrets manager; store only hashes
  (`api_key_sha256`) in your tenants file.

## Version note

Sandbox Agents are a **beta** feature of `openai-agents` (>= 0.14.0; this repo
locks to a recent 0.17.x). Import paths and option fields may shift before GA —
if something doesn't match, check the SDK docs under `ref/sandbox/`. The Docker
option fields in `make_sandbox_client` in particular are worth confirming
against your installed version.
