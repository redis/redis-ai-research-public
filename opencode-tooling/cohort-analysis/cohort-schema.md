# session-cohort-input.json Schema

Create this file at:

```text
cohort-analysis/session-cohort-input.json
```

The file must be a JSON object with one top-level field: `cohorts`.

## Required Shape

```json
{
  "cohorts": [
    {
      "cohort_name": "baseline-runs",
      "session_ids": [
        "ses_10e4657e4ffewTTxUXtkVLjDWH",
        "ses_10e119296ffevugnygoML8x4f3"
      ]
    }
  ]
}
```

## Fields

| Field | Required | Type | Description |
| :---- | :---- | :---- | :---- |
| `cohorts` | yes | array | List of cohort sets to process. Must contain at least one cohort object. |
| `cohorts[].cohort_name` | no | string | Human-readable name for this cohort. If omitted, a default indexed name is used. |
| `cohorts[].session_ids` | yes | array of strings | Session IDs included in this cohort. Must contain at least one session ID. |

## Session ID Rules

Each session ID must match this pattern:

```text
ses_[A-Za-z0-9]+
```

Valid examples:

```text
ses_10e4657e4ffewTTxUXtkVLjDWH
ses_10e119296ffevugnygoML8x4f3
```

Invalid examples:

```text
10e4657e4ffewTTxUXtkVLjDWH
ses_10e4657e4ffe-vugnygo
ses_10e4657e4ffe vugnygo
```

## Multiple Cohorts

To compare multiple sets of runs, add multiple objects to `cohorts`:

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

## Notes

- Duplicate session IDs within the same cohort are allowed but will be deduplicated while preserving first occurrence.
- Empty strings are ignored.
- JSON comments are not allowed.
- Use double quotes for all JSON strings.
