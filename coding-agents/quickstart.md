# Quickstart

Once the server is running (`uv run uvicorn app.main:app --reload`), it listens on `http://127.0.0.1:8000`.

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

## 3. Send a coding task

The main endpoint — the agent runs end-to-end in the tenant's sandbox and returns the final output:

```bash
curl http://127.0.0.1:8000/v1/tasks \
  -H "Authorization: Bearer demo-key-acme" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Create hello.py that prints Hello from the sandbox, then run it and show the output."}'
```

Response shape: `{tenant_id, model, status: "completed", output: "..."}`.

Files the agent creates live under `./_workspaces/acme/` (the `local_dir` workspace mounted into the sandbox). Run another task and it'll see those files — that's the per-tenant persistence.

## 4. Override the model per request (optional)

```bash
curl http://127.0.0.1:8000/v1/tasks \
  -H "Authorization: Bearer demo-key-acme" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Refactor hello.py to take a name argument.", "model": "gpt-5.1"}'
```

## 5. Browse the auto-generated docs

FastAPI exposes Swagger UI at `http://127.0.0.1:8000/docs` — you can fire requests interactively from there (click "Authorize" and paste `demo-key-acme`).

## Things to know

- **Synchronous tasks.** The HTTP request blocks until the agent finishes or hits `TASK_TIMEOUT_SECONDS` (600s). Long task → long curl. The README sketches the async/job-queue version for later.
- **Sandbox backend.** Backend is `unix_local` per `.env` — the agent's shell/file tools run on **your host**, not isolated. Fine for testing; for real tenants flip `SANDBOX_BACKEND=docker` (and have Docker running).
- **Adding tenants.** Copy `tenants.example.json` → `tenants.json`, edit it, and set `TENANTS_FILE=./tenants.json` in `.env`. Each entry maps an API key to a tenant id + workspace.
- **Watching the agent work.** Server logs (in the uvicorn terminal) show each turn's tool calls — useful for watching what the agent is actually doing in the sandbox.
