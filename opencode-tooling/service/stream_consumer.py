"""Redis Stream consumer that forwards cohort requests to Celery."""

from __future__ import annotations

import argparse
import os
import socket
import time

from redis import Redis
from redis.exceptions import ResponseError, TimeoutError
from rich.console import Console

from service.cohort_job import generate_job_id, parse_stream_fields, record_job_status
from service.config import load_config
from service.tasks import run_cohort


CONSOLE = Console()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Consume cohort requests from a Redis Stream and enqueue Celery jobs."
    )
    parser.add_argument("--redis-url", default=None, help="Redis URL. Defaults to REDIS_URL.")
    parser.add_argument("--stream", default=None, help="Redis Stream name.")
    parser.add_argument("--group", default=None, help="Redis consumer group name.")
    parser.add_argument("--consumer", default=None, help="Redis consumer name.")
    parser.add_argument("--block-ms", type=int, default=5000, help="XREADGROUP block timeout.")
    parser.add_argument("--count", type=int, default=10, help="Max messages per read.")
    parser.add_argument(
        "--socket-timeout",
        type=float,
        default=None,
        help="Redis socket timeout in seconds. Defaults to block timeout plus 5 seconds.",
    )
    return parser


def _ensure_group(redis_client: Redis, stream_name: str, group_name: str) -> None:
    try:
        redis_client.xgroup_create(stream_name, group_name, id="0", mkstream=True)
        CONSOLE.print(f"[green]Created consumer group[/green] {group_name} on {stream_name}")
    except ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise


def consume_forever(
    redis_url: str,
    stream_name: str,
    group_name: str,
    consumer_name: str,
    block_ms: int,
    count: int,
    socket_timeout: float | None,
) -> None:
    """Continuously consume stream entries and enqueue Celery tasks."""
    effective_socket_timeout = socket_timeout or (block_ms / 1000.0) + 5.0
    redis_client = Redis.from_url(redis_url, socket_timeout=effective_socket_timeout)
    _ensure_group(redis_client, stream_name, group_name)
    CONSOLE.print(
        f"[cyan]Listening[/cyan] stream={stream_name} group={group_name} consumer={consumer_name}"
    )

    while True:
        try:
            messages = redis_client.xreadgroup(
                group_name,
                consumer_name,
                {stream_name: ">"},
                count=count,
                block=block_ms,
            )
        except TimeoutError:
            continue
        if not messages:
            continue

        for _, entries in messages:
            for message_id, fields in entries:
                try:
                    cohort_name, session_ids, job_id = parse_stream_fields(fields)
                    job_id = job_id or generate_job_id()
                    result = run_cohort.delay(cohort_name, session_ids, job_id)
                    record_job_status(
                        redis_client,
                        job_id,
                        status="queued",
                        cohort_name=cohort_name,
                        session_ids=session_ids,
                        session_count=len(session_ids),
                        task_id=result.id,
                        stream_message_id=message_id.decode(),
                    )
                    redis_client.xack(stream_name, group_name, message_id)
                    CONSOLE.print(
                        f"[green]Queued[/green] job={job_id} task={result.id} cohort={cohort_name} "
                        f"sessions={len(session_ids)} message={message_id.decode()}"
                    )
                except Exception as exc:
                    redis_client.xack(stream_name, group_name, message_id)
                    CONSOLE.print(f"[red]Rejected[/red] message={message_id!r}: {exc}")


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    config = load_config()
    consumer_name = args.consumer or f"{socket.gethostname()}-{os.getpid()}"
    while True:
        try:
            consume_forever(
                redis_url=args.redis_url or config.redis_url,
                stream_name=args.stream or config.stream_name,
                group_name=args.group or config.stream_group,
                consumer_name=consumer_name,
                block_ms=args.block_ms,
                count=args.count,
                socket_timeout=args.socket_timeout,
            )
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            CONSOLE.print(f"[red]Consumer error[/red]: {exc}. Retrying in 5s.")
            time.sleep(5)


if __name__ == "__main__":
    main()
