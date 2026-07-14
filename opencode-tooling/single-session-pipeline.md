# Single Session Pipeline

Use this flow when you want to export and analyze one OpenCode session outside the cohort workflow.

## Step 1: Export The Session Trajectory

Run:

```bash
uv run construct-session-log \
  SESSION_ID \
  --workspace WORKSPACE \
  --project PROJECT \
  --local-only \
  --save_as_json
```

This reads the session from:

```text
~/.local/share/opencode/opencode.db
```

and writes:

```text
session_trajectories/<session_id>_export.json
```

## Step 2: Compute Metrics For That Trajectory

Run:

```bash
uv run compute-agent-metrics \
  --trajectory-file session_trajectories/<session_id>_export.json
```

This writes metrics to:

```text
metrics/<session_id>_export/
```

## Summary

For one session, run:

```text
construct-session-log -> compute-agent-metrics
```

For a cohort, use `session-cohort` instead. It runs both steps internally for each session in the cohort input file.
