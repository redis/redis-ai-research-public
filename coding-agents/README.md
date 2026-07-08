# Multi-tenant Coding Agent

A FastAPI service that exposes a per-tenant coding agent built on the
**OpenAI Agents SDK**. Each request is authenticated to a tenant and runs
**directly in a real host folder** — one-shot or as a multi-turn
conversation, synchronously or streamed turn-by-turn.

```
client --(API key)--> FastAPI --> per-tenant Agent --> tools run in this process,
                         |               ^              cwd = tenant's workspace folder
                     auth -> Tenant      |
                                 invoke_subagents -> N worker agents (parallel)

state: Postgres (sessions, active workspace)  — or file fallbacks for dev
```

**Execution model: no sandbox.** Shell commands run with the workspace as
their working directory; file tools are confined to paths under the workspace
root; edits write through to the real files. Process-level isolation, if you
need it, is handled outside this service (containerize the service itself,
run it in a VM, etc.).

## Layout

| File | Responsibility |
|------|----------------|
| `app/config.py`          | Settings from env / `.env` |
| `app/tenancy.py`         | Tenant model, API-key registry, workspace resolution + override authorization |
| `app/agent.py`           | Orchestrator + worker `Agent`s, host-execution tools, MCP discovery/connection, run/stream entry points |
| `app/auth.py`            | `Authorization: Bearer` / `X-API-Key` -> `Tenant` |
| `app/sessions.py`        | Postgres-backed conversation sessions (SDK `SessionABC`) |
| `app/workspace_state.py` | Per-tenant "active workspace" store (Postgres or JSON file) |
| `app/main.py`            | FastAPI endpoints |
| `startup.sh`             | One command: Postgres in Docker + uvicorn |

## Run it

```bash
uv sync                     # installs deps from pyproject.toml / uv.lock

# Simplest — no Postgres. Sessions endpoints return 503; workspace state
# falls back to a local JSON file (_workspace_state.json).
uv run uvicorn app.main:app --reload

# Full setup — starts (or reuses) a Postgres 16 container and launches the app
# with DATABASE_URL set. Requires Docker and OPENAI_API_KEY in your shell.
./startup.sh
```

`OPENAI_API_KEY` is read from the environment. `DEFAULT_MODEL` defaults to
`gpt-5.4` (see `.env`). `startup.sh` accepts `PG_PORT`, `APP_PORT`,
`CONTAINER` env overrides.

See [quickstart.md](quickstart.md) for a guided first request and
[checks.md](checks.md) for verified end-to-end test results.

**Interactive chat:** `uv run python chat.py` opens a terminal conversation
(fresh session, streamed tool activity; `/end` to quit, `/listmcp` for the
live MCP inventory). `API_URL`/`API_KEY` env overrides.

## Endpoints

| Method | Path | Body | Purpose |
|--------|------|------|---------|
| GET    | `/healthz`          | — | Liveness (no auth) |
| GET    | `/v1/me`            | — | Echo the authenticated tenant |
| POST   | `/v1/tasks`         | `{prompt, model?, workspace_path?}` | Run one task to completion |
| POST   | `/v1/tasks/stream`  | `{prompt, model?, workspace_path?}` | Same, streamed as SSE |
| PUT    | `/v1/workspace`     | `{path}` | Set the folder the agent works in for all subsequent tasks |
| GET    | `/v1/workspace`     | — | Show the active workspace (null = tenant default) |
| DELETE | `/v1/workspace`     | — | Reset to the tenant default |
| PUT    | `/v1/mcp`           | `{path}` | REPLACE all MCP sources with this one (path or URL) |
| POST   | `/v1/mcp/sources`   | `{path}` | ADD a source alongside existing ones |
| DELETE | `/v1/mcp/sources`   | `?path=...` | Remove one source |
| GET    | `/v1/mcp`           | — | Launch all sources, list live (deduped) tools |
| DELETE | `/v1/mcp`           | — | Remove all per-tenant MCP sources |
| POST   | `/v1/chat`          | `{prompt, model?, session_id?, workspace_path?}` | Conversational turn with folder-scoped auto-resume (needs Postgres) |
| POST   | `/v1/chat/stream`   | same | Same, streamed as SSE (first event: `session`) |
| POST   | `/v1/sessions`      | `{workspace_path?}` | Create a session bound to a folder (default: active workspace) |
| POST   | `/v1/sessions/{id}/close` | — | Close a conversation (history kept; won't auto-resume) |
| GET    | `/v1/sessions`      | — | List sessions (newest activity first, with folder + status) |
| DELETE | `/v1/sessions/{id}` | — | Delete a session + its history |
| GET    | `/v1/sessions/{id}/messages` | — | Read conversation history |
| POST   | `/v1/sessions/{id}/messages` | `{prompt, model?, workspace_path?}` | Run one turn against a specific session |
| POST   | `/v1/sessions/{id}/messages/stream` | same | Same, streamed as SSE |

The demo tenant key `demo-key-acme` works out of the box. Swagger UI at
`/docs` (click Authorize, paste the key).

## Typical workflow

```bash
# 1. Point the agent at a project folder (stored server-side, survives restarts)
curl -X PUT localhost:8000/v1/workspace \
  -H 'Authorization: Bearer demo-key-acme' \
  -H 'Content-Type: application/json' \
  -d '{"path": "/absolute/path/to/your/project"}'

# 2. Ask about it — no path needed in the request
curl localhost:8000/v1/tasks \
  -H 'Authorization: Bearer demo-key-acme' \
  -H 'Content-Type: application/json' \
  -d '{"prompt": "Read the README and main source files. What does this project do?"}'

# 3. Or watch it work live
curl -N localhost:8000/v1/tasks/stream \
  -H 'Authorization: Bearer demo-key-acme' \
  -H 'Content-Type: application/json' \
  -d '{"prompt": "List the Python files and summarize each in one line."}'

# 4. Reset when done
curl -X DELETE localhost:8000/v1/workspace -H 'Authorization: Bearer demo-key-acme'
```

## Choosing where the agent runs

Per task, the workspace is picked in this order:

1. `workspace_path` in the request body (one-off override),
2. the stored active workspace (`PUT /v1/workspace`),
3. the tenant's default from the registry (`./_workspaces/<tenant>/` for the
   demo tenants).

The agent works in that folder **directly**: shell commands run with it as
cwd, file tools resolve paths under it (and refuse paths outside it), and
edits are real and persistent.

**AGENTS.md.** If the workspace root contains an `AGENTS.md`, the agent loads
it automatically: on **every one-shot task** (appended to the system
instructions, re-read each run), and on the **first message of each chat
session** (prepended to that message, so it's stored once in history and
replayed on later turns without re-adding). Subagent workers get it in their
instructions too. Edits to the file apply to new tasks/sessions; existing
sessions keep the copy from their first turn. Truncated at 30k chars.

**Authorization.** A requested path must resolve (symlinks followed) under one
of the tenant's `allowed_roots` (a list in the tenant record). With
`ALLOW_ANY_WORKSPACE_PATH=true` (dev only — on in the checked-in `.env`) any
existing directory is accepted. Stored paths are re-validated on every use.

**The trust model, stated plainly:** the agent executes arbitrary shell on
this host as the service's user. Path checks confine the *file tools* to the
workspace, but `run_shell` is only confined by the prompt. Run this service
only for users you trust with the machine, or wrap the whole service in a
container/VM.

## Conversations (`/v1/chat`) — folder-scoped auto-resume

The simplest way to have a multi-turn conversation. Just send prompts; the
server tracks which conversation you're in based on the folder:

```bash
# Turn 1 — no open session for this folder yet, so one is created
curl -s localhost:8000/v1/chat \
  -H 'Authorization: Bearer demo-key-acme' -H 'Content-Type: application/json' \
  -d '{"prompt": "What is the most important file here?"}'
# -> {..., "session_id": "a49a...", "resumed": false}

# Turn 2 — same folder, latest open session auto-resumes; history replayed
curl -s localhost:8000/v1/chat \
  -H 'Authorization: Bearer demo-key-acme' -H 'Content-Type: application/json' \
  -d '{"prompt": "Tell me more about that file."}'
# -> {..., "session_id": "a49a...", "resumed": true}

# Done with this thread — close it (history kept; won't auto-resume)
curl -s -X POST localhost:8000/v1/sessions/a49a.../close \
  -H 'Authorization: Bearer demo-key-acme'
```

Resolution rules per `/v1/chat` call:

1. `session_id` in the body → that exact conversation (409 if closed).
2. Otherwise resolve the folder (`workspace_path` in body > active workspace >
   tenant default), then resume the **latest open session bound to that
   folder** — or create one automatically.

Each folder gets its own conversation thread; switching the active workspace
(or passing a different `workspace_path`) switches threads. The response always
carries `session_id` and `resumed`. The streamed variant (`/v1/chat/stream`)
emits an initial `session` SSE event before the run events.

**Clarifying questions (elicitation).** The agent is instructed to ask rather
than guess when a request is ambiguous — it ends its turn with 1-2 pointed
questions (listing the options it found), and your next `/v1/chat` message is
the answer; auto-resume routes it into the same conversation. When it proceeds
on a reasonable default instead, it states the assumption. Note this works at
turn boundaries, not mid-run, and `/v1/tasks` (stateless) can't complete the
loop — send ambiguous work to `/v1/chat`.

Because execution is direct, file changes persist across turns: what the agent
edits in turn 1 is on disk in turn 2 (and in your editor).

Storage: `agent_sessions` (id, tenant_id, workspace_path, status,
last_active_at) and `agent_session_items` (JSONB per item, cascade delete),
auto-created on startup. Every endpoint verifies tenant ownership and returns
404 (not 403) on mismatch so ids can't be enumerated. `PostgresSession`
implements the SDK's `SessionABC` (~4 methods), so swapping in Redis or
another store is a small adapter.

## Live event streaming (SSE)

Use `curl -N` so events arrive unbuffered. Frame types:

| Event | Payload | Meaning |
|-------|---------|---------|
| `workspace_instructions` | `{source, loaded, mode?, chars?, reason?}` | Whether AGENTS.md was loaded and how (`instructions`, `first_message`, or `replayed_from_session_history`) |
| `agent_updated`   | `{agent}` | A new agent took control (first event = orchestrator, `coding-agent[<tenant>]`) |
| `run_item`        | `{item, tool?, arguments?, output?, text?}` | Tool call, tool output, or assistant message |
| `done`            | `{model, output}` | Terminal success |
| `error`           | `{error, detail}` | Terminal failure (timeout, exception) |

Token-level deltas (`raw_response_event`) are filtered out; flip
`_summarize_event` in `app/agent.py` to forward them. Arguments/outputs are
truncated to 500 chars per event.

## Agent architecture

Two plain `Agent`s: an **orchestrator** that keeps the conversation and
integrates results, and ephemeral **workers** spawned by `invoke_subagents` —
each with a fresh context window and half the orchestrator's turn budget
(floor 5). Passing multiple subtasks in one call runs them concurrently
(`asyncio.gather`); failures come back as `[subagent N] FAILED: ...` strings
instead of crashing the run. Workers operate on the SAME real folder with no
file locking — the orchestrator is instructed to keep parallel work read-only
or partitioned by file.

### Tools on the orchestrator

All implemented in this repo (`app/agent.py`), all running in the service
process:

| Tool | What it does |
|------|--------------|
| `run_shell`         | Run a shell command with the workspace as cwd (120s timeout, ~20k char output cap) |
| `read_file`         | Read a file with line numbers (offset/limit windowing) |
| `write_file`        | Create/overwrite a file (parents auto-created) |
| `edit_file`         | Exact-string replace, must match exactly once |
| `invoke_subagents`  | Fan out N parallel worker subagents |
| `webfetch`          | GET an http(s) URL (≤200 KB, 20s) for general web lookups |
| `todo_write` / `todo_read` | Per-task todo list (resets each task) |
| *(MCP tools)*       | Whatever the tenant's registered MCP servers expose (see below) |

File tools accept paths relative to the workspace root (absolute paths under
it also work) and refuse anything outside it. Tool errors are returned to the
model as text so it can correct itself instead of killing the run.

## MCP servers (`/v1/mcp`)

Attach any number of MCP tool sources to the agent at runtime — one API call
per server, no restart, no central config file. Scriptable: loop over your
list of sources and POST each.

```bash
AUTH='Authorization: Bearer demo-key-acme'
CT='Content-Type: application/json'

# ADD a source (repeat per server; each call is independent)
curl -X POST localhost:8000/v1/mcp/sources -H "$AUTH" -H "$CT" \
  -d '{"path": "/path/to/source"}'

curl localhost:8000/v1/mcp -H "$AUTH"                             # live inventory
curl -X DELETE 'localhost:8000/v1/mcp/sources?path=...' -H "$AUTH"  # remove one
curl -X PUT localhost:8000/v1/mcp -H "$AUTH" -H "$CT" -d '{"path": "..."}'  # replace all
curl -X DELETE localhost:8000/v1/mcp -H "$AUTH"                   # remove all
```

### Recipes — the three kinds of source

**1. Your own FastMCP server (a `.py` file, or a folder of them):**

```bash
curl -X POST localhost:8000/v1/mcp/sources -H "$AUTH" -H "$CT" \
  -d '{"path": "/path/to/my-project/mcp-servers/my_server.py"}'
```

Launched via `uv run --project <nearest-pyproject> python <file>`, so it uses
its own project's dependencies. A folder registers every `*.py` inside that
has an `mcp.run()` entrypoint (or the folder's `mcp.json` if present).

**2. A published npm/npx server** — write a small json file (OpenCode configs
work verbatim, including `"mcp"` key, command-as-array, and `//` comments):

```bash
cat > ~/.mcp-extra/flight-search.json << 'EOF'
{
  "mcp": {
    "flight-search": {
      "type": "local",
      "command": ["npx", "-y", "google-flights-mcp-server"]
    }
  }
}
EOF
curl -X POST localhost:8000/v1/mcp/sources -H "$AUTH" -H "$CT" \
  -d '{"path": "'$HOME'/.mcp-extra/flight-search.json"}'
```

`{"servers"}` and Claude-style `{"mcpServers"}` keys are accepted too, with
`command`/`args`/`env`/`cwd` fields.

**3. A remote MCP server — just the URL:**

```bash
curl -X POST localhost:8000/v1/mcp/sources -H "$AUTH" -H "$CT" \
  -d '{"path": "https://mcp.example.com/sse"}'
```

Connects over streamable-http, or SSE when the URL ends in `/sse`. For auth
headers or an explicit transport, use a json file with
`{"url": ..., "transport": "sse"|"http", "headers": {...}}`.

### How it works

* **Sources compose.** Each tenant has a list of sources; every task/chat turn
  gets the merged toolset from all of them.
* **Validation on PUT/POST.** Each server is actually launched and its tools
  listed in the response; a broken source fails the request instead of
  silently failing later.
* **Persistent server processes (MCPPool).** Servers launch once and stay
  alive across tasks and chat turns. They relaunch only when the source list
  or a config file changes, a server stops answering pings, or the service
  restarts. This is required for stateful servers — e.g. BrowserMCP, whose
  Chrome extension connects to one specific server process — and removes
  per-turn startup latency. Consequence: after a relaunch (including uvicorn
  `--reload` on code edits), stateful servers need their external side
  reconnected (click Connect in the BrowserMCP extension again).
* **Editing a server's code** applies on its next launch: change the source
  list (or restart the service) to force it, and use `GET /v1/mcp` to confirm
  the live tool list.
* **Paths just work.** Everything runs on the host, so MCP tools and the
  agent's own tools see the same filesystem. Relative paths the model passes
  to MCP tools are absolutized against the workspace root at the call
  boundary (including legacy `repo/...` paths from old sessions).
* **Duplicate tool names** across servers are a hard SDK error, so they're
  deduped: the first source to expose a name keeps it; later duplicates are
  filtered (logged).
* **Static fallback.** `MCP_CONFIG=./mcp.json` in `.env` applies to tenants
  that haven't registered any sources.

### Known limitations

* **MCP-initiated elicitation is not supported.** If an MCP server calls the
  MCP `elicit` capability mid-tool-call (e.g. FastMCP's `ctx.elicit()`) to ask
  the user a question, no callback is wired to surface it — the tool call
  fails instead of reaching the user. Agent-initiated clarification works fine
  (see Conversations below); this only affects servers that themselves prompt
  the user.
* The model sees MCP tools as a flat tool list — it has no notion of "which
  servers are attached." Use `GET /v1/mcp`, or ask it to *list its tools*;
  asking it "what servers do you see" will send it grepping the workspace
  instead.

## Where to watch what the agent is doing

1. **OpenAI traces dashboard** — every run auto-uploads to
   <https://platform.openai.com/traces>; orchestrator under `tenant:<id>`,
   workers under `tenant:<id>:worker:<n>`.
2. **Verbose stdout** — `from agents import enable_verbose_stdout_logging` in
   `app/main.py` prints every LLM call and tool call to the uvicorn console.
3. **SSE stream** — filtered JSON events for clients/UIs.

## Tenancy model

* **Auth -> Tenant.** API keys matched by SHA-256 hash. Demo registry is
  in-memory; point `TENANTS_FILE` at `tenants.json` (see
  `tenants.example.json`) or swap `TenantRegistry` for a DB lookup.
* **Workspace.** Each tenant has a default folder; overrides are validated
  against `allowed_roots`.
* **Sessions & workspace state.** Tenant-scoped by column checks in Postgres.
* **No execution isolation between tenants.** All agents run as the same OS
  user in the same process. This service trusts its callers — treat it as a
  personal/team tool, not a hostile-multi-tenant platform, unless you add
  isolation around it.

## Configuration reference (.env)

| Var | Default | Meaning |
|-----|---------|---------|
| `OPENAI_API_KEY` | — | Read from environment |
| `DEFAULT_MODEL` | `gpt-5.4` | Model unless tenant/request overrides |
| `MAX_TURNS` | `30` | Orchestrator turn cap (workers get half) |
| `TASK_TIMEOUT_SECONDS` | `600` | Per-task wall clock |
| `WORKSPACES_ROOT` | `./_workspaces` | Root for tenant default workspaces |
| `TENANTS_FILE` | unset | JSON tenant registry (else demo tenants) |
| `DATABASE_URL` | unset | Postgres; enables sessions + shared workspace state |
| `DB_POOL_MAX_SIZE` | `10` | asyncpg pool size |
| `ALLOW_ANY_WORKSPACE_PATH` | `false` | DEV ONLY — accept any dir as workspace override |
| `MCP_CONFIG` | unset | Static mcp.json fallback; per-tenant `PUT /v1/mcp` takes precedence |

## Production notes

* **Long tasks.** Bounded by `TASK_TIMEOUT_SECONDS`. At scale, enqueue and
  return `202` + a task id clients poll.
* **Cost.** `MAX_TURNS` caps runaway loops; the subagent split keeps the
  orchestrator context small.
* **Isolation.** Handled outside this service by design. The straightforward
  recipe: run the whole service in a container with only the workspace
  folders mounted.
* **Secrets.** Keep tenant keys in a secrets manager; store only
  `api_key_sha256` in the tenants file.
