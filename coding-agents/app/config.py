"""Application settings, loaded from environment / .env file.

Env var names match the field names (case-insensitive), e.g. OPENAI_API_KEY,
DEFAULT_MODEL, SANDBOX_BACKEND, etc.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Literal, Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # The OpenAI key used by the Agents SDK by default. The SDK reads
    # OPENAI_API_KEY from the environment, so exporting it is enough; we also
    # surface it here so we can validate it is present at startup.
    openai_api_key: str = ""

    # Model every tenant uses unless they override it. Set this to a model your
    # account can access (model names change over time).
    default_model: str = "gpt-5.4"

    # Where the agent's tools actually execute.
    #   unix_local -> runs on this host's filesystem. Fine for local dev,
    #                 NOT isolation. Do not use for real multi-tenant prod.
    #   docker     -> one ephemeral container per task. Requires
    #                 `pip install "openai-agents[docker]"` and a Docker daemon.
    sandbox_backend: Literal["unix_local", "docker"] = "unix_local"

    # Base image for the docker sandbox backend.
    docker_image: str = "python:3.12-slim"

    # Safety rails per task.
    max_turns: int = 30
    task_timeout_seconds: int = 600

    # Root directory under which each tenant gets an isolated local workspace
    # (used by the "local_dir" workspace kind).
    workspaces_root: str = "./_workspaces"

    # Optional path to a JSON file describing tenants. If unset, a small
    # in-memory demo registry is used so the service runs out of the box.
    tenants_file: Optional[str] = None

    # Postgres connection string for conversation sessions. Required to use
    # the /v1/sessions endpoints; one-shot /v1/tasks endpoints work without it.
    # Example: postgresql://user:pass@localhost:5432/agent
    database_url: Optional[str] = None
    db_pool_max_size: int = 10


@lru_cache
def get_settings() -> Settings:
    return Settings()
