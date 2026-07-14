"""Celery tasks for running cohort analysis."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from service.celery_app import celery_app
from service.cohort_job import (
    cohort_input_path,
    cohort_output_path,
    generate_job_id,
    normalize_session_ids,
    record_job_status,
    write_single_cohort_input,
)
from service.config import PROJECT_ROOT, load_config


def _split_timeline_svgs(stdout: str) -> tuple[str, str, str]:
    """Separate generated SVG timelines and final responses from regular stdout."""
    final_response_heading = "Final Output Responses"
    final_response_text = ""
    final_response_index = stdout.find(final_response_heading)
    if final_response_index != -1:
        final_response_text = stdout[final_response_index:].strip()
        stdout = stdout[:final_response_index].rstrip()

    start_marker = "<!-- cohort-trajectory-svg-start -->"
    end_marker = "<!-- cohort-trajectory-svg-end -->"
    remaining_stdout = []
    svg_blocks = []
    cursor = 0

    while True:
        start_index = stdout.find(start_marker, cursor)
        if start_index == -1:
            remaining_stdout.append(stdout[cursor:])
            break
        end_index = stdout.find(end_marker, start_index)
        if end_index == -1:
            remaining_stdout.append(stdout[cursor:])
            break

        remaining_stdout.append(stdout[cursor:start_index])
        svg_blocks.append(stdout[start_index : end_index + len(end_marker)])
        cursor = end_index + len(end_marker)

    return "".join(remaining_stdout).strip(), "\n\n".join(svg_blocks).strip(), final_response_text


@celery_app.task(name="service.run_cohort")
def run_cohort(cohort_name: str, session_ids: list[str], job_id: str | None = None) -> dict[str, str | int]:
    """Run `src.session_cohort` for one queued cohort request."""
    config = load_config()
    effective_job_id = job_id or generate_job_id()
    normalized_session_ids = normalize_session_ids(session_ids)
    input_path = cohort_input_path(cohort_name, config.input_dir, effective_job_id)
    output_path = cohort_output_path(cohort_name, config.output_dir, effective_job_id)
    log_path = output_path.with_suffix(".log")
    redis_client = None
    if job_id:
        from redis import Redis

        redis_client = Redis.from_url(config.redis_url)
        record_job_status(
            redis_client,
            effective_job_id,
            status="running",
            input_file=str(input_path),
            output_file=str(output_path),
            log_file=str(log_path),
        )
    write_single_cohort_input(input_path, cohort_name, normalized_session_ids)

    command = [
        sys.executable,
        "-m",
        "src.session_cohort",
        "--input",
        str(input_path),
        "--output",
        str(output_path),
        "--cohort-name",
        cohort_name,
    ]
    completed_process = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    stdout_text, timeline_svg_text, final_response_text = _split_timeline_svgs(completed_process.stdout)
    timeline_section = (
        "\n\nTRAJECTORY_TIMELINE_SVGS:\n"
        f"{timeline_svg_text}\n"
        if timeline_svg_text
        else ""
    )
    final_response_section = (
        "\n\nFINAL_OUTPUT_RESPONSES:\n"
        f"{final_response_text}\n"
        if final_response_text
        else ""
    )
    log_path.write_text(
        "COMMAND:\n"
        f"{' '.join(command)}\n\n"
        "STDOUT:\n"
        f"{stdout_text}\n"
        f"{timeline_section}\n"
        f"{final_response_section}\n"
        "STDERR:\n"
        f"{completed_process.stderr}\n"
    )
    if completed_process.returncode != 0:
        if job_id and redis_client:
            record_job_status(
                redis_client,
                effective_job_id,
                status="failed",
                input_file=str(input_path),
                output_file=str(output_path),
                log_file=str(log_path),
                error="session_cohort failed",
            )
        raise RuntimeError(
            "session_cohort failed:\n"
            f"LOG:{log_path}\n"
            f"STDOUT:\n{completed_process.stdout}\n"
            f"STDERR:\n{completed_process.stderr}"
        )

    if job_id and redis_client:
        record_job_status(
            redis_client,
            effective_job_id,
            status="succeeded",
            input_file=str(input_path),
            output_file=str(output_path),
            log_file=str(log_path),
        )

    return {
        "job_id": effective_job_id,
        "cohort_name": cohort_name,
        "session_count": len(normalized_session_ids),
        "input_file": str(input_path),
        "output_file": str(output_path),
        "log_file": str(log_path),
    }


def run_cohort_sync(cohort_name: str, session_ids: list[str]) -> dict[str, str | int]:
    """Run the cohort task synchronously, useful for local debugging."""
    return run_cohort.run(cohort_name, session_ids)
