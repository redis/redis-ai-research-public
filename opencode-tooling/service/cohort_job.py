"""Shared parsing and file naming helpers for cohort jobs."""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SESSION_ID_PATTERN = re.compile(r"ses_[A-Za-z0-9]+")
JOB_KEY_PREFIX = "cohort-job:"


def utc_now_iso() -> str:
    """Return an ISO-8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


def generate_job_id() -> str:
    """Return a unique worker job id."""
    return uuid.uuid4().hex


def job_key(job_id: str) -> str:
    """Return the Redis key used for one job status hash."""
    return f"{JOB_KEY_PREFIX}{job_id}"


def record_job_status(redis_client: Any, job_id: str, **fields: Any) -> None:
    """Update a job status hash in Redis."""
    normalized_fields = {
        key: json.dumps(value) if isinstance(value, (list, dict)) else str(value)
        for key, value in fields.items()
        if value is not None
    }
    normalized_fields["updated_at"] = utc_now_iso()
    redis_client.hset(job_key(job_id), mapping=normalized_fields)


def decode_job_hash(job_id: str, payload: dict[Any, Any]) -> dict[str, Any]:
    """Decode one Redis job status hash."""
    decoded = {"job_id": job_id}
    for key, value in payload.items():
        text_key = key.decode() if isinstance(key, bytes) else str(key)
        text_value = value.decode() if isinstance(value, bytes) else str(value)
        if text_key == "session_ids":
            try:
                decoded[text_key] = json.loads(text_value)
            except json.JSONDecodeError:
                decoded[text_key] = text_value
        elif text_key == "session_count":
            decoded[text_key] = int(text_value)
        else:
            decoded[text_key] = text_value
    return decoded


def list_jobs(redis_client: Any, limit: int = 100) -> list[dict[str, Any]]:
    """Return recent job status hashes from Redis."""
    jobs = []
    for raw_key in redis_client.scan_iter(f"{JOB_KEY_PREFIX}*"):
        key = raw_key.decode() if isinstance(raw_key, bytes) else str(raw_key)
        current_job_id = key.removeprefix(JOB_KEY_PREFIX)
        jobs.append(decode_job_hash(current_job_id, redis_client.hgetall(raw_key)))
    jobs.sort(key=lambda job: job.get("updated_at", ""), reverse=True)
    return jobs[:limit]


def sanitize_filename_component(value: str) -> str:
    """Return a filesystem-safe name component."""
    sanitized = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip())
    return sanitized.strip("-") or "cohort"


def timestamp_for_filename(now: datetime | None = None) -> str:
    """Return the minute-hour-date suffix requested for worker output files."""
    current_time = now or datetime.now()
    return current_time.strftime("%M_%H_%Y%m%d")


def cohort_output_path(cohort_name: str, output_dir: Path, job_id: str) -> Path:
    """Build a unique cohort output path for one worker job."""
    output_dir.mkdir(parents=True, exist_ok=True)
    safe_name = sanitize_filename_component(cohort_name)
    return output_dir / f"{safe_name}_{job_id}_{timestamp_for_filename()}.json"


def cohort_input_path(cohort_name: str, input_dir: Path, job_id: str) -> Path:
    """Build a unique one-cohort input file path for one worker job."""
    input_dir.mkdir(parents=True, exist_ok=True)
    safe_name = sanitize_filename_component(cohort_name)
    return input_dir / f"{safe_name}_{job_id}_{timestamp_for_filename()}_input.json"


def normalize_session_ids(session_ids: Any) -> list[str]:
    """Validate and normalize session IDs from JSON/list/comma-delimited input."""
    if isinstance(session_ids, str):
        stripped = session_ids.strip()
        if stripped.startswith("["):
            session_ids = json.loads(stripped)
        else:
            session_ids = [value.strip() for value in stripped.split(",")]

    if not isinstance(session_ids, list):
        raise ValueError("session_ids must be a list, JSON list string, or comma-delimited string.")

    normalized = []
    seen = set()
    for value in session_ids:
        if not isinstance(value, str):
            raise ValueError("session_ids must contain only strings.")
        session_id = value.strip()
        if not session_id:
            continue
        if not SESSION_ID_PATTERN.fullmatch(session_id):
            raise ValueError(
                f"Invalid session_id value: {session_id}. Expected ses_-prefixed letters/digits."
            )
        if session_id not in seen:
            seen.add(session_id)
            normalized.append(session_id)

    if not normalized:
        raise ValueError("At least one session ID is required.")
    return normalized


def parse_stream_fields(fields: dict[Any, Any]) -> tuple[str, list[str], str | None]:
    """Parse Redis Stream fields into a cohort job request."""
    decoded = {
        key.decode() if isinstance(key, bytes) else str(key): (
            value.decode() if isinstance(value, bytes) else value
        )
        for key, value in fields.items()
    }
    cohort_name = str(decoded.get("cohort_name", "")).strip()
    if not cohort_name:
        raise ValueError("cohort_name is required.")
    job_id = str(decoded.get("job_id", "")).strip() or None
    return cohort_name, normalize_session_ids(decoded.get("session_ids", [])), job_id


def write_single_cohort_input(path: Path, cohort_name: str, session_ids: list[str]) -> None:
    """Write the one-cohort input file consumed by `src.session_cohort`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "cohorts": [
            {
                "cohort_name": cohort_name,
                "session_ids": session_ids,
            }
        ]
    }
    path.write_text(json.dumps(payload, indent=2))
