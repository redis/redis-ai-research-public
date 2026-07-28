"""CLI for writing cohort requests into the Redis Stream."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from redis import Redis
from rich.console import Console

from service.cohort_job import generate_job_id, normalize_session_ids, record_job_status
from service.config import load_config


CONSOLE = Console()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Enqueue a cohort request into Redis Streams.")
    parser.add_argument(
        "--payload",
        help='JSON object with "cohort_name" and "session_ids" fields.',
    )
    parser.add_argument(
        "--payload-file",
        type=Path,
        help='Path to a JSON object with "cohort_name" and "session_ids" fields.',
    )
    parser.add_argument("--cohort-name", help="Cohort name for this job.")
    parser.add_argument(
        "--session-id",
        action="append",
        default=[],
        help="Session ID to include. Repeat for multiple sessions.",
    )
    parser.add_argument(
        "--session-file",
        type=Path,
        help="Optional text file containing one session ID per line.",
    )
    parser.add_argument("--redis-url", default=None, help="Redis URL. Defaults to REDIS_URL.")
    parser.add_argument("--stream", default=None, help="Redis Stream name.")
    return parser


def _read_session_file(path: Path) -> list[str]:
    if not path.is_file():
        raise FileNotFoundError(f"Session file not found: {path}")
    session_ids = []
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if line and not line.startswith("#"):
            session_ids.append(line)
    return session_ids


def _load_payload(args: argparse.Namespace) -> tuple[str, list[str]] | None:
    """Load a cohort request from --payload or --payload-file."""
    if args.payload and args.payload_file:
        raise ValueError("Use only one of --payload or --payload-file.")
    if not args.payload and not args.payload_file:
        return None

    raw_payload = args.payload or args.payload_file.read_text()
    payload = json.loads(raw_payload)
    if not isinstance(payload, dict):
        raise ValueError("Payload must be a JSON object.")

    cohort_name = str(payload.get("cohort_name", "")).strip()
    if not cohort_name:
        raise ValueError('Payload field "cohort_name" is required.')
    return cohort_name, normalize_session_ids(payload.get("session_ids", []))


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    config = load_config()
    try:
        payload_request = _load_payload(args)
        if payload_request is not None:
            cohort_name, session_ids = payload_request
        else:
            if not args.cohort_name:
                parser.error("Provide --cohort-name unless using --payload or --payload-file.")
            session_ids = list(args.session_id)
            if args.session_file:
                session_ids.extend(_read_session_file(args.session_file))
            cohort_name = args.cohort_name
            session_ids = normalize_session_ids(session_ids)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))

    redis_client = Redis.from_url(args.redis_url or config.redis_url)
    job_id = generate_job_id()
    record_job_status(
        redis_client,
        job_id,
        status="submitted",
        cohort_name=cohort_name,
        session_ids=session_ids,
        session_count=len(session_ids),
    )
    message_id = redis_client.xadd(
        args.stream or config.stream_name,
        {
            "job_id": job_id,
            "cohort_name": cohort_name,
            "session_ids": json.dumps(session_ids),
        },
    )
    CONSOLE.print(
        f"[green]Enqueued[/green] message={message_id.decode()} "
        f"job={job_id} cohort={cohort_name} sessions={len(session_ids)}"
    )


if __name__ == "__main__":
    main()
