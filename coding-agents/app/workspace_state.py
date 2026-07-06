"""Per-tenant "active workspace" state.

The user points the service at a folder once (PUT /v1/workspace) and every
subsequent task for that tenant runs against it — no per-request parameter
needed. A request-level `workspace_path` still wins if supplied.

Two backends, chosen at startup:
  * PostgresWorkspaceStateStore — when DATABASE_URL is set. Correct across
    multiple uvicorn workers / replicas.
  * FileWorkspaceStateStore — JSON file fallback for dev without Postgres.
    Survives restarts; single-process only.
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Optional, Protocol

logger = logging.getLogger("coding_agent.workspace_state")

WORKSPACE_STATE_SQL = """
CREATE TABLE IF NOT EXISTS tenant_workspace (
    tenant_id  TEXT PRIMARY KEY,
    path       TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


class WorkspaceStateStore(Protocol):
    async def get(self, tenant_id: str) -> Optional[str]: ...
    async def set(self, tenant_id: str, path: str) -> None: ...
    async def clear(self, tenant_id: str) -> None: ...


class FileWorkspaceStateStore:
    """JSON-file store: {tenant_id: path}. Dev fallback when Postgres is off."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._lock = asyncio.Lock()

    def _load(self) -> dict:
        if not self._path.exists():
            return {}
        try:
            return json.loads(self._path.read_text("utf-8"))
        except (json.JSONDecodeError, OSError):
            logger.warning("Workspace state file unreadable; starting empty.")
            return {}

    def _save(self, data: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(data, indent=2), "utf-8")

    async def get(self, tenant_id: str) -> Optional[str]:
        async with self._lock:
            return self._load().get(tenant_id)

    async def set(self, tenant_id: str, path: str) -> None:
        async with self._lock:
            data = self._load()
            data[tenant_id] = path
            self._save(data)

    async def clear(self, tenant_id: str) -> None:
        async with self._lock:
            data = self._load()
            if data.pop(tenant_id, None) is not None:
                self._save(data)


class PostgresWorkspaceStateStore:
    """Postgres store — safe across workers/replicas."""

    def __init__(self, pool) -> None:
        self._pool = pool

    @classmethod
    async def create(cls, pool) -> "PostgresWorkspaceStateStore":
        async with pool.acquire() as conn:
            await conn.execute(WORKSPACE_STATE_SQL)
        return cls(pool)

    async def get(self, tenant_id: str) -> Optional[str]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT path FROM tenant_workspace WHERE tenant_id = $1", tenant_id
            )
        return row["path"] if row else None

    async def set(self, tenant_id: str, path: str) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO tenant_workspace(tenant_id, path)
                VALUES($1, $2)
                ON CONFLICT (tenant_id)
                DO UPDATE SET path = EXCLUDED.path, updated_at = now()
                """,
                tenant_id,
                path,
            )

    async def clear(self, tenant_id: str) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM tenant_workspace WHERE tenant_id = $1", tenant_id
            )
