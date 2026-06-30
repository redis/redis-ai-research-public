"""Postgres-backed conversation sessions.

Implements the SDK's `SessionABC` interface so any agent run can persist its
turns to Postgres and the next request can replay them. Shared by all FastAPI
workers/replicas, so sessions work in a distributed setup.

Tenant scoping: sessions carry a `tenant_id` column. Every accessor checks it,
so tenant A cannot read or write tenant B's session even if they guess the id.

Schema (created on app startup):

    agent_sessions(id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL,
                   created_at TIMESTAMPTZ NOT NULL DEFAULT now())
    agent_session_items(id BIGSERIAL PRIMARY KEY,
                        session_id TEXT REFERENCES agent_sessions(id) ON DELETE CASCADE,
                        item JSONB NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT now())
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

import asyncpg
from agents import SessionABC
from fastapi import Depends, HTTPException, Request, status

from .config import Settings, get_settings
from .tenancy import Tenant

logger = logging.getLogger("coding_agent.sessions")

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS agent_sessions (
    id         TEXT PRIMARY KEY,
    tenant_id  TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_agent_sessions_tenant
    ON agent_sessions(tenant_id);

CREATE TABLE IF NOT EXISTS agent_session_items (
    id          BIGSERIAL PRIMARY KEY,
    session_id  TEXT NOT NULL
                REFERENCES agent_sessions(id) ON DELETE CASCADE,
    item        JSONB NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_agent_session_items_session
    ON agent_session_items(session_id, id);
"""


async def _set_json_codecs(conn: asyncpg.Connection) -> None:
    """Auto-encode/decode JSONB <-> dict so we never juggle strings."""
    await conn.set_type_codec(
        "jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
    )
    await conn.set_type_codec(
        "json", encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
    )


async def create_pool(settings: Settings) -> asyncpg.Pool:
    """Build the pool used by every request."""
    if not settings.database_url:
        raise RuntimeError(
            "DATABASE_URL is not set. Sessions require Postgres — "
            "set DATABASE_URL in .env (e.g. postgresql://user:pass@localhost/agent)."
        )
    pool = await asyncpg.create_pool(
        dsn=settings.database_url,
        min_size=1,
        max_size=settings.db_pool_max_size,
        init=_set_json_codecs,
    )
    async with pool.acquire() as conn:
        await conn.execute(SCHEMA_SQL)
    logger.info("Postgres pool ready; session schema ensured.")
    return pool


def get_pool(request: Request) -> asyncpg.Pool:
    """FastAPI dependency. The pool lives on app.state, created in the lifespan."""
    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database pool is not initialized.",
        )
    return pool


# ---------------------------------------------------------------------------
# Session class
# ---------------------------------------------------------------------------


class PostgresSession(SessionABC):
    """SDK Session backed by Postgres. Construct with a session id + pool."""

    def __init__(self, session_id: str, pool: asyncpg.Pool) -> None:
        self.session_id = session_id
        self._pool = pool

    async def get_items(self, limit: Optional[int] = None) -> list[dict[str, Any]]:
        async with self._pool.acquire() as conn:
            if limit is not None:
                # Take the last `limit` items in chronological order.
                rows = await conn.fetch(
                    """
                    SELECT item FROM (
                        SELECT id, item FROM agent_session_items
                         WHERE session_id = $1
                         ORDER BY id DESC LIMIT $2
                    ) t
                    ORDER BY id ASC
                    """,
                    self.session_id,
                    limit,
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT item FROM agent_session_items
                     WHERE session_id = $1
                     ORDER BY id ASC
                    """,
                    self.session_id,
                )
        return [r["item"] for r in rows]

    async def add_items(self, items: list[dict[str, Any]]) -> None:
        if not items:
            return
        async with self._pool.acquire() as conn:
            await conn.executemany(
                "INSERT INTO agent_session_items(session_id, item) VALUES($1, $2)",
                [(self.session_id, item) for item in items],
            )

    async def pop_item(self) -> Optional[dict[str, Any]]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                DELETE FROM agent_session_items
                 WHERE id = (
                    SELECT id FROM agent_session_items
                     WHERE session_id = $1
                     ORDER BY id DESC LIMIT 1
                 )
                RETURNING item
                """,
                self.session_id,
            )
        return row["item"] if row else None

    async def clear_session(self) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM agent_session_items WHERE session_id = $1",
                self.session_id,
            )


# ---------------------------------------------------------------------------
# Tenant-scoped access helpers (used by the endpoints)
# ---------------------------------------------------------------------------


async def create_session_row(
    pool: asyncpg.Pool, tenant: Tenant, session_id: str
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO agent_sessions(id, tenant_id) VALUES($1, $2)",
            session_id,
            tenant.id,
        )


async def assert_session_owned_by(
    pool: asyncpg.Pool, tenant: Tenant, session_id: str
) -> None:
    """Raise 404 if the session does not exist OR belongs to a different tenant.

    Same status code in both cases so attackers can't enumerate session ids.
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT tenant_id FROM agent_sessions WHERE id = $1",
            session_id,
        )
    if row is None or row["tenant_id"] != tenant.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Session not found."
        )


async def list_sessions_for_tenant(
    pool: asyncpg.Pool, tenant: Tenant
) -> list[dict[str, Any]]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, created_at
              FROM agent_sessions
             WHERE tenant_id = $1
             ORDER BY created_at DESC
            """,
            tenant.id,
        )
    return [{"session_id": r["id"], "created_at": r["created_at"].isoformat()} for r in rows]


async def delete_session_for_tenant(
    pool: asyncpg.Pool, tenant: Tenant, session_id: str
) -> None:
    """Delete after verifying ownership; cascades to items."""
    await assert_session_owned_by(pool, tenant, session_id)
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM agent_sessions WHERE id = $1", session_id)
