"""Configuration helpers for the cohort worker service."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class ServiceConfig:
    """Runtime configuration for Redis Streams and Celery."""

    redis_url: str
    stream_name: str
    stream_group: str
    input_dir: Path
    output_dir: Path


def load_config() -> ServiceConfig:
    """Load service configuration from environment variables."""
    return ServiceConfig(
        redis_url=os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
        stream_name=os.environ.get("COHORT_STREAM", "cohort-requests"),
        stream_group=os.environ.get("COHORT_STREAM_GROUP", "cohort-workers"),
        input_dir=Path(
            os.environ.get(
                "COHORT_WORKER_INPUT_DIR",
                str(PROJECT_ROOT / "cohort-analysis" / "worker-inputs"),
            )
        ),
        output_dir=Path(
            os.environ.get(
                "COHORT_WORKER_OUTPUT_DIR",
                str(PROJECT_ROOT / "cohort-analysis" / "worker-outputs"),
            )
        ),
    )
