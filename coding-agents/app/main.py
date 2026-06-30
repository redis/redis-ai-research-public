"""Multi-tenant coding-agent service.

Stateless endpoints:
    GET  /healthz                          - liveness check (no auth)
    GET  /v1/me                            - echo the authenticated tenant
    POST /v1/tasks                         - run one task to completion
    POST /v1/tasks/stream                  - run one task, stream events (SSE)

Session endpoints (Postgres-backed; require DATABASE_URL):
    POST   /v1/sessions                              - create a session
    GET    /v1/sessions                              - list this tenant's sessions
    DELETE /v1/sessions/{session_id}                 - delete a session + history
    GET    /v1/sessions/{session_id}/messages        - read conversation history
    POST   /v1/sessions/{session_id}/messages        - send a message; agent runs
                                                       with full prior history
    POST   /v1/sessions/{session_id}/messages/stream - same, SSE-streamed
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .agent import run_coding_task, run_coding_task_stream
from .auth import get_current_tenant
from .config import Settings, get_settings
from .sessions import (
    PostgresSession,
    assert_session_owned_by,
    create_pool,
    create_session_row,
    delete_session_for_tenant,
    get_pool,
    list_sessions_for_tenant,
)
from .tenancy import Tenant

logger = logging.getLogger("coding_agent")


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    """Build the Postgres pool on startup if DATABASE_URL is set."""
    settings = get_settings()
    app.state.pool = None
    if settings.database_url:
        app.state.pool = await create_pool(settings)
    else:
        logger.warning(
            "DATABASE_URL not set; /v1/sessions endpoints will return 503."
        )
    try:
        yield
    finally:
        if app.state.pool is not None:
            await app.state.pool.close()


app = FastAPI(title="Multi-tenant Coding Agent", version="1.1.0", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class TaskRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=20_000)
    model: str | None = Field(
        default=None, description="Optional per-request model override."
    )


class TaskResponse(BaseModel):
    tenant_id: str
    model: str
    status: str
    output: str


class MeResponse(BaseModel):
    tenant_id: str
    name: str
    workspace_kind: str


class SessionCreatedResponse(BaseModel):
    session_id: str


class SessionListEntry(BaseModel):
    session_id: str
    created_at: str


class SessionListResponse(BaseModel):
    sessions: list[SessionListEntry]


class MessageItem(BaseModel):
    item: dict


class MessagesResponse(BaseModel):
    session_id: str
    items: list[dict]


# ---------------------------------------------------------------------------
# Stateless endpoints
# ---------------------------------------------------------------------------


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.get("/v1/me", response_model=MeResponse)
def me(tenant: Tenant = Depends(get_current_tenant)) -> MeResponse:
    return MeResponse(
        tenant_id=tenant.id, name=tenant.name, workspace_kind=tenant.workspace_kind
    )


def _sse_response(generator) -> StreamingResponse:
    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _stream_task(
    tenant: Tenant,
    prompt: str,
    settings: Settings,
    model_override: str | None,
    session: PostgresSession | None,
):
    try:
        async for event_type, payload in run_coding_task_stream(
            tenant=tenant,
            prompt=prompt,
            settings=settings,
            model_override=model_override,
            session=session,
        ):
            line = f"event: {event_type}\ndata: {json.dumps(payload)}\n\n"
            yield line.encode("utf-8")
    except Exception as exc:
        logger.exception("Stream failed for tenant %s", tenant.id)
        err = {"error": type(exc).__name__, "detail": str(exc)}
        yield f"event: error\ndata: {json.dumps(err)}\n\n".encode("utf-8")


@app.post("/v1/tasks", response_model=TaskResponse)
async def create_task(
    body: TaskRequest,
    tenant: Tenant = Depends(get_current_tenant),
    settings: Settings = Depends(get_settings),
) -> TaskResponse:
    """Run a coding task synchronously in the tenant's isolated sandbox."""
    try:
        result = await run_coding_task(
            tenant=tenant,
            prompt=body.prompt,
            settings=settings,
            model_override=body.model,
        )
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=f"Task exceeded {settings.task_timeout_seconds}s.",
        )
    except Exception as exc:
        logger.exception("Task failed for tenant %s", tenant.id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Agent run failed: {type(exc).__name__}",
        )

    return TaskResponse(
        tenant_id=result.tenant_id,
        model=result.model,
        status="completed",
        output=str(result.output),
    )


@app.post("/v1/tasks/stream")
async def create_task_stream(
    body: TaskRequest,
    tenant: Tenant = Depends(get_current_tenant),
    settings: Settings = Depends(get_settings),
) -> StreamingResponse:
    """Run a one-shot coding task and stream events as SSE."""
    return _sse_response(
        _stream_task(tenant, body.prompt, settings, body.model, session=None)
    )


# ---------------------------------------------------------------------------
# Session endpoints (Postgres-backed)
# ---------------------------------------------------------------------------


@app.post(
    "/v1/sessions",
    response_model=SessionCreatedResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_session(
    tenant: Tenant = Depends(get_current_tenant),
    pool=Depends(get_pool),
) -> SessionCreatedResponse:
    session_id = uuid.uuid4().hex
    await create_session_row(pool, tenant, session_id)
    return SessionCreatedResponse(session_id=session_id)


@app.get("/v1/sessions", response_model=SessionListResponse)
async def list_sessions(
    tenant: Tenant = Depends(get_current_tenant),
    pool=Depends(get_pool),
) -> SessionListResponse:
    rows = await list_sessions_for_tenant(pool, tenant)
    return SessionListResponse(
        sessions=[SessionListEntry(**r) for r in rows]
    )


@app.delete("/v1/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: str,
    tenant: Tenant = Depends(get_current_tenant),
    pool=Depends(get_pool),
) -> None:
    await delete_session_for_tenant(pool, tenant, session_id)
    return None


@app.get(
    "/v1/sessions/{session_id}/messages",
    response_model=MessagesResponse,
)
async def list_session_messages(
    session_id: str,
    tenant: Tenant = Depends(get_current_tenant),
    pool=Depends(get_pool),
) -> MessagesResponse:
    await assert_session_owned_by(pool, tenant, session_id)
    items = await PostgresSession(session_id, pool).get_items()
    return MessagesResponse(session_id=session_id, items=items)


@app.post(
    "/v1/sessions/{session_id}/messages",
    response_model=TaskResponse,
)
async def send_session_message(
    session_id: str,
    body: TaskRequest,
    tenant: Tenant = Depends(get_current_tenant),
    settings: Settings = Depends(get_settings),
    pool=Depends(get_pool),
) -> TaskResponse:
    """Run one more turn against the named session. Prior history is replayed."""
    await assert_session_owned_by(pool, tenant, session_id)
    session = PostgresSession(session_id, pool)
    try:
        result = await run_coding_task(
            tenant=tenant,
            prompt=body.prompt,
            settings=settings,
            model_override=body.model,
            session=session,
        )
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=f"Task exceeded {settings.task_timeout_seconds}s.",
        )
    except Exception as exc:
        logger.exception(
            "Session turn failed (tenant=%s session=%s)", tenant.id, session_id
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Agent run failed: {type(exc).__name__}",
        )

    return TaskResponse(
        tenant_id=result.tenant_id,
        model=result.model,
        status="completed",
        output=str(result.output),
    )


@app.post("/v1/sessions/{session_id}/messages/stream")
async def stream_session_message(
    session_id: str,
    body: TaskRequest,
    tenant: Tenant = Depends(get_current_tenant),
    settings: Settings = Depends(get_settings),
    pool=Depends(get_pool),
) -> StreamingResponse:
    """SSE-streamed variant of POST /v1/sessions/{id}/messages."""
    await assert_session_owned_by(pool, tenant, session_id)
    session = PostgresSession(session_id, pool)
    return _sse_response(
        _stream_task(tenant, body.prompt, settings, body.model, session=session)
    )
