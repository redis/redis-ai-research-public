"""Convenience command for setting up and running stream workers."""

from __future__ import annotations

import argparse
import os
import signal
import socket
import subprocess
import sys
import time
from typing import Sequence

from redis import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import ResponseError
from rich.console import Console

from service.config import PROJECT_ROOT, load_config


CONSOLE = Console()


def _build_parser() -> argparse.ArgumentParser:
    """Create the stream setup CLI parser."""
    parser = argparse.ArgumentParser(
        description=(
            "Create the Redis Stream consumer group and optionally start the "
            "Celery worker plus stream consumer."
        )
    )
    parser.add_argument("--redis-url", default=None, help="Redis URL. Defaults to REDIS_URL.")
    parser.add_argument("--stream", default=None, help="Redis Stream name.")
    parser.add_argument("--group", default=None, help="Redis consumer group name.")
    parser.add_argument("--consumer", default=None, help="Redis consumer name.")
    parser.add_argument(
        "--worker-loglevel",
        default="INFO",
        help="Celery worker log level. Defaults to INFO.",
    )
    parser.add_argument(
        "--consumer-block-ms",
        type=int,
        default=5000,
        help="Redis Stream consumer block timeout in milliseconds.",
    )
    parser.add_argument(
        "--consumer-socket-timeout",
        type=float,
        default=None,
        help="Redis consumer socket timeout in seconds. Defaults to block timeout plus 5 seconds.",
    )
    parser.add_argument("--http-host", default="127.0.0.1", help="HTTP API bind host.")
    parser.add_argument("--http-port", type=int, default=8000, help="HTTP API bind port.")
    parser.add_argument(
        "--start-redis",
        action="store_true",
        help="Start a local redis-server process before setting up workers.",
    )
    parser.add_argument(
        "--no-start",
        action="store_true",
        help="Only create/check the stream group; do not start worker processes.",
    )
    parser.add_argument(
        "--skip-celery",
        action="store_true",
        help="Do not start the Celery worker process.",
    )
    parser.add_argument(
        "--skip-consumer",
        action="store_true",
        help="Do not start the Redis Stream consumer process.",
    )
    parser.add_argument(
        "--skip-http",
        action="store_true",
        help="Do not start the HTTP enqueue endpoint.",
    )
    return parser


def _check_redis(redis_url: str) -> None:
    """Raise a clear error if Redis is unavailable."""
    try:
        Redis.from_url(redis_url).ping()
    except RedisConnectionError as exc:
        raise RuntimeError(
            f"Unable to connect to Redis at {redis_url}. Start Redis first with `redis-server`, "
            "or rerun this command with `--start-redis`, or set REDIS_URL/--redis-url."
        ) from exc


def _ensure_group(redis_url: str, stream_name: str, group_name: str) -> None:
    """Create the stream consumer group if it does not already exist."""
    redis_client = Redis.from_url(redis_url)
    try:
        redis_client.xgroup_create(stream_name, group_name, id="0", mkstream=True)
        CONSOLE.print(f"[green]Created[/green] stream={stream_name} group={group_name}")
    except ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise
        CONSOLE.print(f"[cyan]Exists[/cyan] stream={stream_name} group={group_name}")


def _start_process(command: Sequence[str], name: str) -> subprocess.Popen[str]:
    """Start one worker subprocess."""
    CONSOLE.print(f"[green]Starting[/green] {name}: [dim]{' '.join(command)}[/dim]")
    return subprocess.Popen(
        list(command),
        cwd=PROJECT_ROOT,
        text=True,
    )


def _start_redis() -> subprocess.Popen[str]:
    """Start a local redis-server subprocess."""
    return _start_process(["redis-server"], "redis-server")


def _wait_for_redis(redis_url: str, timeout_seconds: int = 10) -> None:
    """Wait until Redis accepts connections or timeout."""
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            Redis.from_url(redis_url).ping()
            return
        except RedisConnectionError as exc:
            last_error = exc
            time.sleep(0.25)
    raise RuntimeError(f"Redis did not become ready at {redis_url}: {last_error}")


def _terminate_processes(processes: list[subprocess.Popen[str]]) -> None:
    """Terminate all running child processes."""
    for process in processes:
        if process.poll() is None:
            process.send_signal(signal.SIGTERM)
    for process in processes:
        if process.poll() is None:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()


def _wait_for_processes(processes: list[subprocess.Popen[str]]) -> int:
    """Wait until one process exits, then stop the rest."""
    try:
        while True:
            for process in processes:
                return_code = process.poll()
                if return_code is not None:
                    _terminate_processes(processes)
                    return return_code
            time.sleep(0.5)
    except (KeyboardInterrupt, InterruptedError):
        CONSOLE.print("[yellow]Stopping worker processes...[/yellow]")
        _terminate_processes(processes)
        return 0


def main() -> None:
    """Set up Redis Stream resources and optionally start worker processes."""
    parser = _build_parser()
    args = parser.parse_args()
    config = load_config()

    redis_url = args.redis_url or config.redis_url
    stream_name = args.stream or config.stream_name
    group_name = args.group or config.stream_group
    consumer_name = args.consumer or f"{socket.gethostname()}-{os.getpid()}"

    processes: list[subprocess.Popen[str]] = []
    if args.start_redis:
        processes.append(_start_redis())
        _wait_for_redis(redis_url)
    else:
        try:
            _check_redis(redis_url)
        except RuntimeError as exc:
            CONSOLE.print(f"[red]{exc}[/red]")
            raise SystemExit(1) from exc

    _ensure_group(redis_url, stream_name, group_name)
    if args.no_start:
        CONSOLE.print("[green]Stream setup complete.[/green]")
        _terminate_processes(processes)
        return

    if not args.skip_celery:
        processes.append(
            _start_process(
                [
                    sys.executable,
                    "-m",
                    "celery",
                    "-A",
                    "service.celery_app",
                    "worker",
                    f"--loglevel={args.worker_loglevel}",
                ],
                "celery-worker",
            )
        )
    if not args.skip_consumer:
        consumer_command = [
            sys.executable,
            "-m",
            "service.stream_consumer",
            "--redis-url",
            redis_url,
            "--stream",
            stream_name,
            "--group",
            group_name,
            "--consumer",
            consumer_name,
            "--block-ms",
            str(args.consumer_block_ms),
        ]
        if args.consumer_socket_timeout is not None:
            consumer_command.extend(["--socket-timeout", str(args.consumer_socket_timeout)])
        processes.append(_start_process(consumer_command, "stream-consumer"))

    if not args.skip_http:
        processes.append(
            _start_process(
                [
                    sys.executable,
                    "-m",
                    "service.http_api",
                    "--host",
                    args.http_host,
                    "--port",
                    str(args.http_port),
                    "--redis-url",
                    redis_url,
                    "--stream",
                    stream_name,
                ],
                "http-api",
            )
        )

    if not processes:
        CONSOLE.print("[yellow]No worker processes selected.[/yellow]")
        return

    raise SystemExit(_wait_for_processes(processes))


if __name__ == "__main__":
    main()
