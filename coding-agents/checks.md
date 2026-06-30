# End-to-end checks

Results from a live run of the service against `gpt-5.4` and Postgres 16 in
Docker. Performed on 2026-06-24.

## Environment

- Python 3.12.10 via `uv` (`pyproject.toml` / `uv.lock`)
- `openai-agents==0.17.7`, `openai==2.43.0`, `fastapi==0.138.0`, `asyncpg==0.31.0`
- `OPENAI_API_KEY` sourced from `~/.zshrc`
- `DEFAULT_MODEL=gpt-5.4`
- Postgres 16 in Docker on `127.0.0.1:5433`, database `agent`
- `SANDBOX_BACKEND=unix_local` (dev — no isolation)

## Stateless flow (no `DATABASE_URL`)

| Check | Expected | Actual |
|---|---|---|
| Server boots, logs `DATABASE_URL not set; /v1/sessions endpoints will return 503` | startup ok | ✅ |
| `GET /healthz` (no auth) | 200 `{"status":"ok"}` | ✅ |
| `GET /v1/me` no key | 401 | ✅ |
| `GET /v1/me` with `demo-key-acme` | 200 `{tenant_id, name, workspace_kind}` | ✅ |
| `POST /v1/sessions` (no DB) | 503 | ✅ |
| `POST /v1/tasks` with prompt `"echo hello from sandbox"` | 200, output `"hello from sandbox"`, ~6s | ✅ |
| `POST /v1/tasks/stream` SSE | live events: `agent_updated` → `tool_call_item` (exec_command) → `message_output_item` → `tool_call_output_item` → `message_output_item` → `done` | ✅ |
| Parallel subagent fan-out via `invoke_subagents` with two subtasks in one call (`uname` + `whoami`) | one `tool_call_item` for `invoke_subagents`, one `tool_call_output_item` containing both `[subagent 0]` and `[subagent 1]` blocks, orchestrator summarizes both | ✅ |

### Bug found and fixed in this run

`message_output_item.text` was being serialized as the raw
`[ResponseOutputText(annotations=[], text='...', ...)]` repr instead of the
inner string. `_summarize_event` in [app/agent.py](app/agent.py) was calling
`str()` on a list of content blocks instead of extracting `.text` from each
one. Patched to iterate the list and pull `.text` (or `dict.get("text")` if the
block is dict-shaped). After fix, the SSE stream emits clean strings
(`"text": "READY"`).

## Session flow (`DATABASE_URL` set)

| Check | Expected | Actual |
|---|---|---|
| Server boots, schema auto-created | `agent_sessions` + `agent_session_items` tables exist | ✅ |
| `POST /v1/sessions` | 201, returns `{session_id}` (uuid4 hex) | ✅ |
| Turn 1: write `repo/note.txt` containing `ALPHA` | 200, output `"done"` | ✅ |
| Turn 2: ask "What word did you write in the previous turn? Don't use tools." | 200, output `"ALPHA"` — recalled purely from history, no tool calls | ✅ |
| `GET /v1/sessions/{id}/messages` | returns 7 items: user msg → assistant msg → function_call → function_call_output → assistant msg → user msg → assistant msg | ✅ |
| Direct `SELECT` on `agent_sessions` | one row, `tenant_id='acme'`, correct `created_at` | ✅ |
| Direct `SELECT count(*)` on `agent_session_items` | 7 | ✅ |
| Cross-tenant access (`demo-key-globex` → acme's session) | 404 (not 403 — prevents id enumeration) | ✅ |
| `DELETE /v1/sessions/{id}` | 204 | ✅ |
| `GET /v1/sessions/{id}/messages` after delete | 404 | ✅ |
| Cascade: items rows removed | `agent_sessions` and `agent_session_items` both 0 rows | ✅ |

## Things worth knowing (not failures)

- **`gpt-5.4` works against this account** — ~6s round-trip on a trivial
  prompt with one `exec_command` call.
- **`unix_local` sandbox does not write through to the host workspace dir.**
  The agent wrote `repo/note.txt` successfully *inside* the sandbox, but
  `./_workspaces/acme/note.txt` on the host was empty after the run. The agent
  operates on a sandbox-internal copy. For per-tenant persistence to host
  disk, switch `SANDBOX_BACKEND=docker` with a bind mount, or use a hosted
  provider with snapshot writeback. This is a property of `unix_local`, not a
  bug.
- **Tenant isolation is enforced by a `tenant_id` column check** (in
  `assert_session_owned_by`), not by id prefixing. Stored ids are bare uuid4
  hex strings.

## How to reproduce

```bash
# 1. Start Postgres
docker run -d --name agent-pg \
  -e POSTGRES_USER=agent -e POSTGRES_PASSWORD=agent -e POSTGRES_DB=agent \
  -p 5433:5432 postgres:16

# 2. Set DATABASE_URL in .env (or export inline) and run
export DATABASE_URL=postgresql://agent:agent@127.0.0.1:5433/agent
uv run uvicorn app.main:app --host 127.0.0.1 --port 8765

# 3. Smoke test
curl -s localhost:8765/healthz
curl -s localhost:8765/v1/me -H 'Authorization: Bearer demo-key-acme'

# 4. Stateless task
curl -s localhost:8765/v1/tasks \
  -H 'Authorization: Bearer demo-key-acme' \
  -H 'Content-Type: application/json' \
  -d '{"prompt": "Use exec_command to run `echo hello`, then return the output."}'

# 5. Streamed task
curl -sN localhost:8765/v1/tasks/stream \
  -H 'Authorization: Bearer demo-key-acme' \
  -H 'Content-Type: application/json' \
  -d '{"prompt": "Use exec_command to run `ls /` and say done."}'

# 6. Session round-trip
SID=$(curl -s -X POST localhost:8765/v1/sessions \
  -H 'Authorization: Bearer demo-key-acme' | jq -r .session_id)
curl -s localhost:8765/v1/sessions/$SID/messages \
  -H 'Authorization: Bearer demo-key-acme' \
  -H 'Content-Type: application/json' \
  -d '{"prompt": "Remember the word ALPHA."}'
curl -s localhost:8765/v1/sessions/$SID/messages \
  -H 'Authorization: Bearer demo-key-acme' \
  -H 'Content-Type: application/json' \
  -d '{"prompt": "What word did I ask you to remember? Just the word, no tools."}'

# 7. Cleanup
docker rm -f agent-pg
```
