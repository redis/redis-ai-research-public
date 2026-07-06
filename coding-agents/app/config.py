"""Application settings, loaded from environment / .env file.

Env var names match the field names (case-insensitive), e.g. OPENAI_API_KEY,
DEFAULT_MODEL, etc.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Optional

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

    # DEV ONLY. When true, `workspace_path` on a task request is accepted for
    # ANY existing directory on the host — no allowed_roots check. Do not
    # enable in multi-tenant prod; it lets any authenticated tenant point the
    # agent at any host path the server process can read.
    allow_any_workspace_path: bool = False

    # Optional path to an MCP config file (mcp.json). Each server listed there
    # is launched over stdio and its tools are attached to the orchestrator.
    # NOTE: stdio MCP servers run in the SERVER process, outside the sandbox.
    mcp_config: Optional[str] = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
