# Session Pipeline

## Cohort Analysis Input

`session-cohort` reads this input file by default:

```text
cohort-analysis/session-cohort-input.json
```

The input file contains one or more cohorts:

```json
{
  "cohorts": [
    {
      "cohort_name": "cohort-name",
      "session_ids": [
        "ses_...",
        "ses_..."
      ]
    }
  ]
}
```

Each `cohorts[]` entry is one set of runs. `session-cohort` loops over each cohort, then loops over each `session_id` in that cohort.

## Cohort Analysis Flow

Run:

```bash
uv run session-cohort
```

For each session ID, `session-cohort` does the following:

1. Reads the session ID from `cohort-analysis/session-cohort-input.json`.
2. Exports the session trajectory from `opencode.db` into:

```text
session_trajectories/<session_id>_export.json
```

3. Calls `compute-agent-metrics` for that exported trajectory:

```bash
compute-agent-metrics \
  --trajectory-file session_trajectories/<session_id>_export.json \
  --metrics-dir cohort-analysis/metrics/<cohort-name>/<session_id>
```

4. `compute-agent-metrics` writes per-session metrics into:

```text
cohort-analysis/metrics/<cohort-name>/<session_id>/
```

5. `session-cohort` reads these per-session metric files:

```text
cohort-analysis/metrics/<cohort-name>/<session_id>/trajectory_metrics_summary.json
cohort-analysis/metrics/<cohort-name>/<session_id>/token_consumption_per_category.json
```

6. `session-cohort` aggregates those metrics across all sessions in the cohort and writes:

```text
cohort-analysis/session-cohort-output.json
```

## Do You Need To Run compute-agent-metrics Separately?

No. For cohort analysis, you do not need to call `compute-agent-metrics` separately.

`session-cohort` calls `compute-agent-metrics` once per session, immediately after that session's trajectory JSON is exported.

Call `compute-agent-metrics` directly only if you want metrics for one already-exported trajectory file outside the cohort flow.
