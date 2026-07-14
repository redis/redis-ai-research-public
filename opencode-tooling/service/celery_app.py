"""Celery application for cohort worker jobs."""

from __future__ import annotations

from celery import Celery

from service.config import load_config


config = load_config()

celery_app = Celery(
    "opencode_tooling_cohorts",
    broker=config.redis_url,
    backend=config.redis_url,
    include=["service.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    task_track_started=True,
)
