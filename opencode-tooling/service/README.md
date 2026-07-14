# Cohort Worker Service

This service runs cohort analysis jobs asynchronously. A producer writes a cohort request into a Redis Stream, the stream consumer forwards that request to Celery, and the Celery worker runs the existing `session-cohort` pipeline.

## Quickstart

From the repository base folder, start Redis, the Celery worker, the Redis Stream consumer, and the HTTP API:

```bash
uv run stream-setup --start-redis
```

In another terminal, submit a cohort request:

```bash
curl -X POST http://127.0.0.1:8000/cohorts \
  -H 'Content-Type: application/json' \
  -d '{"cohort_name":"experiment-a","session_ids":["ses_109ba0c4cffeKSJexyndKG9t8T"]}'
```

Open the dashboard:

```text
http://127.0.0.1:8000/
```

Outputs are written under:

```text
cohort-analysis/worker-outputs/
```

Stop the service with `Ctrl-C` in the `stream-setup` terminal.

## Architecture

```text
enqueue-cohort -> Redis Stream -> cohort-stream-consumer -> Celery -> src.session_cohort
```

## Request Shape

Each request needs a cohort name and one or more OpenCode session IDs:

```json
{
  "cohort_name": "experiment-a",
  "session_ids": [
    "ses_abc123",
    "ses_def456"
  ]
}
```

## Configuration

Defaults:

| Variable | Default |
| :-- | :-- |
| `REDIS_URL` | `redis://localhost:6379/0` |
| `COHORT_STREAM` | `cohort-requests` |
| `COHORT_STREAM_GROUP` | `cohort-workers` |
| `COHORT_WORKER_INPUT_DIR` | `cohort-analysis/worker-inputs/` |
| `COHORT_WORKER_OUTPUT_DIR` | `cohort-analysis/worker-outputs/` |

Override example:

```bash
export REDIS_URL=redis://localhost:6379/0
export COHORT_STREAM=cohort-requests
export COHORT_STREAM_GROUP=cohort-workers
```

## Start Everything

From the repository base folder:

```bash
uv run stream-setup --start-redis
```

This starts Redis, the Celery worker, the Redis Stream consumer, and an HTTP enqueue API at:

```text
http://127.0.0.1:8000
```

If Redis is already running:

```bash
uv run stream-setup
```

From `base_folder/service/`:

```bash
uv run --project .. stream-setup --start-redis
```

To only create/check the Redis Stream consumer group:

```bash
uv run stream-setup --no-start
```

## Start Pieces Manually

Start Redis:

```bash
redis-server
```

Start Celery worker:

```bash
uv run celery -A service.celery_app worker --loglevel=INFO
```

Start Redis Stream consumer:

```bash
uv run cohort-stream-consumer
```

## Enqueue Jobs

Using repeated session IDs:

```bash
uv run enqueue-cohort \
  --cohort-name experiment-a \
  --session-id ses_abc123 \
  --session-id ses_def456
```

Using a JSON object:

```bash
uv run enqueue-cohort \
  --payload '{"cohort_name":"experiment-a","session_ids":["ses_abc123","ses_def456"]}'
```

Using a JSON file:

```bash
uv run enqueue-cohort --payload-file cohort-request.json
```

Using a text file with one session ID per line:

```bash
uv run enqueue-cohort --cohort-name experiment-a --session-file sessions.txt
```

Direct Redis CLI equivalent:

```bash
redis-cli XADD cohort-requests '*' \
  cohort_name experiment-a \
  session_ids '["ses_abc123","ses_def456"]'
```

HTTP equivalent when `stream-setup` is running:

```bash
curl -X POST http://127.0.0.1:8000/cohorts \
  -H 'Content-Type: application/json' \
  -d '{"cohort_name":"experiment-a","session_ids":["ses_abc123","ses_def456"]}'
```

Health check:

```bash
curl http://127.0.0.1:8000/health
```

Open the lightweight status dashboard:

```text
http://127.0.0.1:8000/
```

The dashboard shows job status and recent `cohort-analysis/` files. File paths are clickable, so you can open generated JSON, log, and text files directly in the browser.
JSON files are pretty-printed with lightweight syntax coloring, and worker logs are split into `COMMAND`, `STDOUT`, `TRAJECTORY_TIMELINE_SVGS`, `FINAL_OUTPUT_RESPONSES`, and `STDERR` sections for easier scanning.
The dashboard also has a notes column for annotating individual files. Notes are stored locally at `cohort-analysis/worker-notes.json` and ignored by git.

JSON status endpoints:

```bash
curl http://127.0.0.1:8000/jobs
curl http://127.0.0.1:8000/files
```

`/jobs` shows submitted, queued, running, succeeded, and failed jobs tracked in Redis. `/files` lists recent files under `cohort-analysis/`, excluding per-session metric artifact folders.

## Outputs

The worker writes a generated one-cohort input file:

```text
cohort-analysis/worker-inputs/<cohort-name>_<minute>_<hour>_<date>_input.json
```

The worker writes the cohort report:

```text
cohort-analysis/worker-outputs/<cohort-name>_<job-id>_<minute>_<hour>_<date>.json
```

The captured stdout/stderr from `session-cohort` is written next to it:

```text
cohort-analysis/worker-outputs/<cohort-name>_<job-id>_<minute>_<hour>_<date>.log
```

Example:

```text
cohort-analysis/worker-outputs/experiment-a_7f3a..._42_15_20260624.json
cohort-analysis/worker-outputs/experiment-a_7f3a..._42_15_20260624.log
```

Worker-generated input/output folders are ignored by git.

## Check Status

Check Redis:

```bash
redis-cli ping
```

Check pending stream messages:

```bash
redis-cli XPENDING cohort-requests cohort-workers
```

Inspect stream messages:

```bash
redis-cli XRANGE cohort-requests - +
```

Check Celery workers:

```bash
uv run celery -A service.celery_app status
```

Check output files:

```bash
ls cohort-analysis/worker-outputs
```

Or use the HTTP status dashboard:

```bash
open http://127.0.0.1:8000/
```

If you have a Celery task ID:

```bash
uv run celery -A service.celery_app result <task_id>
```

## Troubleshooting

If `stream-setup` says Redis is unavailable, either start Redis separately:

```bash
redis-server
```

or let setup start it:

```bash
uv run stream-setup --start-redis
```

If commands are run from `base_folder/service/`, include `--project ..`:

```bash
uv run --project .. enqueue-cohort --help
```

If a job fails, check the Celery worker terminal. Task failures include captured stdout/stderr from `src.session_cohort`.

After changing service code, restart `stream-setup` so the HTTP API, stream consumer, and Celery worker load the latest code.
