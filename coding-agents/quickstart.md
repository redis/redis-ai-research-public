# Quickstart

## 0. Start the server

```bash
cd test-openai-agents

# Simplest — no Postgres (sessions endpoints 503; workspace state in a JSON file)
uv run uvicorn app.main:app --reload

# Full setup — Postgres 16 in Docker + uvicorn, one command
./startup.sh
```

`OPENAI_API_KEY` must be exported in your shell (it is if it's in `~/.zshrc`).
The server listens on `http://127.0.0.1:8000`.

## 1. Sanity-check it's alive (no auth)

```bash
curl http://127.0.0.1:8000/healthz
# {"status":"ok"}
```

## 2. Confirm your tenant key works

The built-in demo registry ships with `demo-key-acme` → tenant `acme`:

```bash
curl http://127.0.0.1:8000/v1/me \
  -H "Authorization: Bearer demo-key-acme"
# {"tenant_id":"acme","name":"Acme Corp","workspace_kind":"local_dir"}
```

## 3. Point the agent at a project folder

Stored server-side per tenant; survives restarts. Every task after this uses it.

```bash
curl -X PUT http://127.0.0.1:8000/v1/workspace \
  -H "Authorization: Bearer demo-key-acme" \
  -H "Content-Type: application/json" \
  -d '{"path": "/absolute/path/to/your/project"}'
```

Check or reset anytime:

```bash
curl http://127.0.0.1:8000/v1/workspace -H "Authorization: Bearer demo-key-acme"
curl -X DELETE http://127.0.0.1:8000/v1/workspace -H "Authorization: Bearer demo-key-acme"
```

Skip this step to use the tenant's default workspace
(`./_workspaces/acme/`, starts empty). The agent works in the folder
DIRECTLY — shell commands run there, and file edits are real and persistent.

## 4. Send a coding task

The agent runs end-to-end in your workspace folder and returns the final output:

```bash
curl http://127.0.0.1:8000/v1/tasks \
  -H "Authorization: Bearer demo-key-acme" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Read the README and main source files. What does this project do?"}'
```

Response shape: `{tenant_id, model, status: "completed", output: "..."}`.

Per-request overrides: `"model": "gpt-5.1"` or `"workspace_path": "/some/dir"`
(one-off, beats the stored workspace).

## 5. Watch it work live (SSE)

`-N` is required — without it curl buffers everything:

```bash
curl -N http://127.0.0.1:8000/v1/tasks/stream \
  -H "Authorization: Bearer demo-key-acme" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "List the Python files and summarize each in one line."}'
```

Events: `agent_updated` → `run_item` (tool calls, outputs, messages) → `done`
(or `error` on timeout/failure).

## 6. Try parallel subagents

```bash
curl -N http://127.0.0.1:8000/v1/tasks/stream \
  -H "Authorization: Bearer demo-key-acme" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Use invoke_subagents with two subtasks in one call: subtask 1 runs `uname`, subtask 2 runs `whoami`. Then summarize both."}'
```

The fan-out shows as one `invoke_subagents` tool call whose output contains
`[subagent 0]` and `[subagent 1]` blocks.

## 7. Have a conversation (needs Postgres — use ./startup.sh)

Just talk to `/v1/chat` — no session bookkeeping needed. The server resumes
the latest open conversation for the current folder, or starts one:

```bash
# Turn 1 — creates a session for the active workspace ("resumed": false)
curl http://127.0.0.1:8000/v1/chat \
  -H "Authorization: Bearer demo-key-acme" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "What is the most important file here?"}'

# Turn 2 — same folder, auto-resumes ("resumed": true); follow-ups just work
curl http://127.0.0.1:8000/v1/chat \
  -H "Authorization: Bearer demo-key-acme" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Tell me more about that file."}'

# Done with this thread — close it (grab session_id from any response)
curl -X POST http://127.0.0.1:8000/v1/sessions/<session_id>/close \
  -H "Authorization: Bearer demo-key-acme"

# See all your conversations (newest activity first, with folder + status)
curl http://127.0.0.1:8000/v1/sessions -H "Authorization: Bearer demo-key-acme" | jq .
```

Every response includes `session_id` and `resumed`. Pass `"session_id": "..."`
to pin a specific conversation, or `"workspace_path": "/some/dir"` to chat
about a different folder (each folder gets its own thread). Streamed variant:
`POST /v1/chat/stream` (first SSE event is `session`).

## 8. Browse the auto-generated docs

Swagger UI at `http://127.0.0.1:8000/docs` — click "Authorize", paste
`demo-key-acme`, and fire requests interactively.

## Things to know

- **Synchronous tasks.** Requests block until the agent finishes or hits
  `TASK_TIMEOUT_SECONDS` (600s). Long task → long curl.
- **No sandbox.** The agent executes directly on your host in the workspace
  folder — edits are real. Only run it for users you trust with the machine.
- **Workspace overrides are dev-open.** `ALLOW_ANY_WORKSPACE_PATH=true` is set
  in `.env`, so any existing directory is accepted. In prod, configure
  `allowed_roots` per tenant instead.
- **Adding tenants.** Copy `tenants.example.json` → `tenants.json`, edit, set
  `TENANTS_FILE=./tenants.json` in `.env`.
- **Watching the agent.** uvicorn console shows requests; full traces at
  <https://platform.openai.com/traces> (`tenant:acme`, workers under
  `tenant:acme:worker:N`).

More detail in [README.md](README.md); verified test results in [checks.md](checks.md).
