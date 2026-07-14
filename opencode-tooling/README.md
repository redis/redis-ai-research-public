# opencode-agent-tooling

Utilities for exporting local OpenCode session logs from `opencode.db` into [Opik](https://www.comet.com/docs/opik/) or a local file-backed project layout.

## What This Repo Does

`construct-session-log` reads one session from the local OpenCode SQLite database and can write to either Opik or local storage.
Scripts support multi-turn OpenCode sessions by grouping each user prompt plus the following assistant activity into an ordered turn, all under the same thread keyed by the OpenCode `session_id`.

The current script assumes the OpenCode database is stored at:

```text
/Users/<username>/.local/share/opencode/opencode.db
```

It scans `/Users/` for a single matching database file and raises an error if none or multiple are found.

## Requirements

- Python 3.12+ recommended
- Local access to the OpenCode SQLite database at the path described above
- An Opik API key plus an existing Opik workspace/project if you want to write to Opik
- Or a local workspace/project name if you want to export to local storage only

## Quickstart

1. Install Python 3.12+ and [`uv`](https://docs.astral.sh/uv/).
2. Create the project environment and install dependencies:

```bash
uv sync --dev
```

3. If you plan to write to Opik, set the Opik API key in your shell:

```bash
export OPIK_API_KEY=your_opik_api_key_here
```

4. Verify that OpenCode has a local database at:

```text
/Users/<username>/.local/share/opencode/opencode.db
```

5. Run one of the scripts:

```bash
uv run construct-session-log \
  ses_34face659ffevDvFeJTgkMTytP \
  "$OPIK_API_KEY" \
  chriscoleman \
  opencode-demo
```

```bash
uv run construct-session-log \
  ses_34face659ffevDvFeJTgkMTytP \
  --workspace chriscoleman \
  --project opencode-demo \
  --local-only
```

```bash
uv run compute-agent-metrics \
  --opik-workspace chriscoleman \
  --opik-project opencode-demo
```

Optional formatting and linting commands:

```bash
uv run black .
uv run isort .
uv run pylint .
```

## CLI Usage

The CLI entry points are defined in `pyproject.toml` and implemented under `src/`.

## Worker Service

The `service/` folder contains a Redis Streams + Celery worker that accepts cohort requests asynchronously and runs `session-cohort` in the background.

For service setup, request submission, dashboard usage, status checks, and troubleshooting, refer to [`service/README.md`](service/README.md).

Start Redis, the Celery worker, the Redis Stream consumer, and the HTTP enqueue API:

```bash
uv run stream-setup --start-redis
```

Submit a cohort with curl:

```bash
curl -X POST http://127.0.0.1:8000/cohorts \
  -H 'Content-Type: application/json' \
  -d '{"cohort_name":"experiment-a","session_ids":["ses_109ba0c4cffeKSJexyndKG9t8T"]}'
```

Run it like this for Opik:

```bash
uv run construct-session-log SESSION_ID OPIK_API_KEY OPIK-WORKSPACE OPIK-PROJECT
```

Or like this for a local-only export:

```bash
uv run construct-session-log SESSION_ID --workspace WORKSPACE --project PROJECT --local-only
```

### Positional Arguments

- `session_id`: the OpenCode session ID to extract from `opencode.db`
- `opik_key`: your Opik API key
- `opik_workspace`: Opik workspace name
- `opik_project`: Opik project name

### Optional Arguments

- `--save_as_json`: saves the session trajectory to `session_trajectories/<session_id>_export.json`.
- `--workspace`: workspace name override, useful for local-only exports
- `--project`: project name override, useful for local-only exports
- `--local-only`: write the reconstructed session bundle to local storage instead of Opik
- `--local-logs-root`: root folder for local exports. Defaults to `local_logs/`.
- `--verbose`: prints the raw and transformed log structures while processing

## Example

```bash
uv run construct-session-log \
  ses_34face659ffevDvFeJTgkMTytP \
  "$OPIK_API_KEY" \
  chriscoleman \
  opencode-demo \
  --save_as_json
```

## Non-Opik File Flow

Use this flow when you want local JSON files and local metrics without writing to Opik.

### Single-Session Trajectory Flow

`construct-session-log` reads the local OpenCode SQLite database at `/Users/<username>/.local/share/opencode/opencode.db` and reconstructs one session into a trajectory JSON file.

```bash
uv run construct-session-log \
  SESSION_ID \
  --workspace WORKSPACE \
  --project PROJECT \
  --local-only \
  --save_as_json
```

| Script | Reads | Writes | Next Consumer |
| :---- | :---- | :---- | :---- |
| `construct-session-log` | `opencode.db` rows for `SESSION_ID` | `session_trajectories/<session_id>_export.json` | `compute-agent-metrics --trajectory-file ...` |

The same command also writes a local backend copy because `--local-only` is set:

| Script | Reads | Writes | Next Consumer |
| :---- | :---- | :---- | :---- |
| `construct-session-log` | `opencode.db` rows for `SESSION_ID` | `local_logs/<workspace>/<project>/sessions/<session_id>.json` | `compute-agent-metrics --read-local-logs ...` |

To compute metrics from just that one exported trajectory file:

```bash
uv run compute-agent-metrics \
  --trajectory-file session_trajectories/<session_id>_export.json
```

| Script | Reads | Writes |
| :---- | :---- | :---- |
| `compute-agent-metrics` | `session_trajectories/<session_id>_export.json` | `metrics/<session_id>_export/` |

### Session Cohort Flow

Use `session-cohort` when you have one or more cohorts of session IDs from repeated runs and want aggregate statistics for each cohort.

Create `cohort-analysis/session-cohort-input.json`:

```json
{
  "cohorts": [
    {
      "cohort_name": "baseline-runs",
      "session_ids": [
        "ses_10e4657e4ffewTTxUXtkVLjDWH",
        "ses_10e119296ffevugnygoML8x4f3"
      ]
    },
    {
      "cohort_name": "experiment-runs",
      "session_ids": [
        "ses_3917a3400ffePiPUw8ikGLvZ44"
      ]
    }
  ]
}
```

Then run:

```bash
uv run session-cohort
```

By default, `session-cohort` reads `cohort-analysis/session-cohort-input.json` and writes `cohort-analysis/session-cohort-output.json`. You can override those paths with `--input` and `--output`.

| Script | Reads | Writes | Next Consumer |
| :---- | :---- | :---- | :---- |
| `session-cohort` | `cohort-analysis/session-cohort-input.json` and `opencode.db` | `session_trajectories/<session_id>_export.json` for each session | `compute-agent-metrics --trajectory-file ...` |
| `session-cohort` | per-session trajectory files | `cohort-analysis/metrics/<cohort-name>/<session_id>/` | `cohort-analysis/session-cohort-output.json` |
| `session-cohort` | per-session `trajectory_metrics_summary.json` files | `cohort-analysis/session-cohort-output.json` | downstream cohort analysis |

The `cohort-analysis/metrics/<cohort-name>/<session_id>/` folders are generated artifacts. They contain the full per-session metrics artifacts generated by `compute-agent-metrics`, and `session-cohort` uses each session's `trajectory_metrics_summary.json` from those folders to build the aggregate cohort report.

The cohort input file contains:

| `cohort-analysis/session-cohort-input.json` Field | Contents |
| :---- | :---- |
| `cohorts` | list of cohort objects to process |
| `cohorts[].cohort_name` | cohort label used under `cohort-analysis/metrics/<cohort-name>/` |
| `cohorts[].session_ids` | list of session IDs in that cohort |

The cohort output report contains:

| `cohort-analysis/session-cohort-output.json` Field | Contents |
| :---- | :---- |
| `input_file` | input JSON file path used for the run |
| `cohort_count` | number of cohort sets processed |
| `session_count` | total sessions requested |
| `succeeded_count` | sessions that exported and computed metrics successfully |
| `failed_count` | sessions that failed export or metrics computation |
| `cohorts` | per-cohort aggregate report blocks |
| `cohorts[].trajectory_files` | list of generated trajectory JSON files for that cohort |
| `cohorts[].metric_statistics` | count, mean, standard deviation, min, and max for each numeric per-session metric in that cohort, including token usage fields such as `usage_total_tokens` and `usage_cache_read_tokens` |
| `cohorts[].sessions` | per-session status, trajectory path, metrics directory, and metrics summary for that cohort |

### Generated Metrics Files

`--trajectory-file` writes the following metrics artifacts for one exported session file.

| Metrics Script Output | Contents |
| :---- | :---- |
| `trajectory_metrics_summary.json` | aggregate turn-level trajectory metrics |
| `trajectory_metrics_per_turn.json` | one trajectory-metric record per conversation turn |
| `agent_behavior_statistics.json` | tool, subagent, bash, document-read, and custom-tool counters |
| `agent_usage_statistics.json` | duration and token usage grouped by inferred source file |
| `token_consumption_per_category.json` | aggregate token counts by token category |
| `duration_per_source_file.json` | raw duration records used for the duration chart |
| `subagent_outputs.json` | captured subagent outputs grouped by source file and subagent type |
| `agent_python_code.json` | captured `python` and `python3` bash invocations |
| `unmatched_turn_inputs.json` | turn prompts where the source-file regex did not find a source file |
| `duration_per_source_file.png` | duration chart |
| `total_tokens_per_source_file.png` | total-token chart |
| `token_consumption_per_category.png` | token-category chart |

## Metrics Usage

Use `compute-agent-metrics` to aggregate behavior and usage metrics from an Opik project, local session exports, or one trajectory file. When reading directly from Opik, it accepts `--opik-workspace` and `--opik-project` and reads the API key from `--opik-key` or `OPIK_API_KEY`.

```bash
uv run compute-agent-metrics \
  --opik-workspace chriscoleman \
  --opik-project opencode-demo
```

To compute metrics for a single exported session trajectory file, first export a session with `construct-session-log --save_as_json`, which writes to `session_trajectories/<session_id>_export.json` by default. Then run:

```bash
uv run compute-agent-metrics \
  --trajectory-file session_trajectories/ses_34face659ffevDvFeJTgkMTytP_export.json
```

In `--trajectory-file` mode, the script reads the top-level `traces` array from that one exported session file and writes metrics under `metrics/<trajectory-file-stem>/` by default.

Custom tool tracking is inferred automatically from tool types that are not in the builtin tool set.
If your agent uses additional builtin tool names, add them with repeated `--builtin-tool-type` flags.

## Tracked Metrics

`compute-agent-metrics` currently writes JSON artifacts and plots under `metrics/<project>/`.

### Behavior Counters

- `subagent_calls`: count of `task` tool invocations grouped by `subagent_type`.
- `subagent_outputs`: raw subagent outputs grouped by source file and `subagent_type`.
- `document_reads`: count of `read` tool targets, including whether a read hit the source file itself or a tool-generated output path.
- `bash_calls`: count of bash invocations grouped by the command name.
- `builtin_tool_calls`: count of builtin tool invocations such as `read`, `glob`, `grep`, `bash`, and `task`.
- `custom_tool_calls`: count of non-builtin tool invocations, inferred from tool types not in the builtin tool set.
- `agent_python_code`: captured `python` and `python3` bash invocations with their input/output payloads.

### Usage Metrics

- `agent_usage_statistics`: per-source-file turn usage records, including `duration`, `total_tokens`, and `cache_read_tokens`.
- `token_consumption_per_category`: aggregate token totals across all turns for tracked token categories.
- `duration_per_source_file`: per-turn duration records used to build the duration chart.
- `duration_per_source_file.png`: bar chart of turn duration by source file.
- `total_tokens_per_source_file.png`: bar chart of total tokens by source file.
- `token_consumption_per_category.png`: bar chart of aggregate token totals by category.

### Trajectory Metrics

These are emitted in both `trajectory_metrics_per_turn.json` and the aggregate `trajectory_metrics_summary.json`.

For a single exported session, these metrics summarize behavior across all turns in that session. In this data model, one turn maps to one Opik trace, so `turn_count` is the number of user turns in the session.

#### Scope

- `turn_count`: number of turns in the session or turns in the analyzed dataset.

#### Outcome Quality

- `first_pass_success_rate`: fraction of turns whose final answer appears to succeed without asking for missing context or explicitly failing.
- `final_answer_specificity`: average 0-1 score based on answer length, structure, numbers, file/path mentions, and overlap with gathered evidence.
- `final_answer_grounding_rate`: fraction of turns whose final answer overlaps meaningfully with tool-derived evidence or supported path mentions.
- `hallucinated_path_rate`: fraction of final-answer path mentions that are unsupported by the source file, turn input, or tool evidence gathered in the turn.

#### Exploration Pattern

- `exploration_breadth`: average number of unique action targets visited per turn.
- `exploration_depth`: average number of tool actions taken per turn.
- `context_switch_count`: average number of action-target switches per turn.
- `backtracking_rate`: average rate of revisiting previously seen action targets after exploring elsewhere.
- `redundant_read_rate`: average rate of repeated reads to the same file path within a turn.

#### Execution Style

- `planning_to_execution_ratio`: average ratio of reasoning spans to tool spans.

#### Failure Signals

- `dead_end_rate`: fraction of tool calls that appear to terminate in empty results, no matches, or other non-useful outcomes.
- `tool_error_rate`: fraction of tool calls whose outputs look like explicit errors or failures.
- `invalid_command_rate`: fraction of bash invocations that appear malformed or fail with command/usage/syntax-style errors.

### Diagnostics

- `trajectory_metrics_per_turn`: per-turn raw values for all trajectory metrics plus supporting counts such as `tool_call_count`, `reasoning_span_count`, and `hallucinated_path_mentions`.
- `trajectory_metrics_summary`: aggregate summary of the trajectory metrics listed above.
- `unmatched_turn_inputs`: turn prompts where the source-file regex could not extract a source file key.

## Understanding Conversation Mapping In Opik

OpenCode conversation data is represented in Opik with the following structure:

- The entire conversation for a session is logged as a Thread in Opik, using the OpenCode `session_id` as the stable thread identifier.
- Each user turn is logged as a Trace in Opik.
- Each Trace contains parent Span objects, marked as type `general` in Opik, which correspond to individual reasoning steps at the assistant-message level.
- Each parent Span contains child Span objects that correspond to granular steps such as tool calls, file reads, internal reasoning, and text generation.

At a lower level, the script currently maps OpenCode parts roughly like this:

- `reasoning` parts become child spans tagged as `reasoning`.
- `text` parts become child spans tagged as `text`.
- `tool` parts become child spans of type `tool` and are additionally tagged with the tool name.
