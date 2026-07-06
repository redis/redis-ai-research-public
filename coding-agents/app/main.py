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
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from contextlib import AsyncExitStack

from .agent import (
    connect_mcp_servers,
    discover_mcp_definitions,
    run_coding_task,
    run_coding_task_stream,
)
from .auth import get_current_tenant
from .config import Settings, get_settings
from .sessions import (
    PostgresSession,
    assert_session_owned_by,
    close_session_for_tenant,
    create_pool,
    create_session_row,
    delete_session_for_tenant,
    find_latest_open_session,
    get_pool,
    get_session_record,
    list_sessions_for_tenant,
    touch_session,
)
from .tenancy import Tenant, WorkspacePathError, resolve_workspace_override
from .workspace_state import (
    FileWorkspaceStateStore,
    PostgresWorkspaceStateStore,
    WorkspaceStateStore,
)

logger = logging.getLogger("coding_agent")


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    """Build the Postgres pool on startup if DATABASE_URL is set."""
    settings = get_settings()
    app.state.pool = None
    if settings.database_url:
        app.state.pool = await create_pool(settings)
        app.state.ws_store = await PostgresWorkspaceStateStore.create(app.state.pool)
    else:
        logger.warning(
            "DATABASE_URL not set; /v1/sessions endpoints will return 503."
        )
        app.state.ws_store = FileWorkspaceStateStore("./_workspace_state.json")
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
    workspace_path: str | None = Field(
        default=None,
        description=(
            "Optional host directory to mount as the agent's workspace for "
            "this request, replacing the tenant's default. Must resolve under "
            "one of the tenant's allowed_roots (or ALLOW_ANY_WORKSPACE_PATH "
            "must be on)."
        ),
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


class McpRequest(BaseModel):
    path: str = Field(
        ...,
        min_length=1,
        description=(
            "An mcp.json file, a single FastMCP server .py, or a directory "
            "of server scripts (auto-discovered)."
        ),
    )


class McpServerInfo(BaseModel):
    name: str
    tools: list[str]


class McpResponse(BaseModel):
    tenant_id: str
    path: str | None
    servers: list[McpServerInfo] = []


class WorkspaceRequest(BaseModel):
    path: str = Field(..., min_length=1, description="Host directory the agent should work in.")


class WorkspaceResponse(BaseModel):
    tenant_id: str
    path: str | None


class SessionCreateRequest(BaseModel):
    workspace_path: str | None = Field(
        default=None,
        description=(
            "Folder this conversation is about. Defaults to the tenant's "
            "current active workspace (PUT /v1/workspace), or the tenant "
            "default if none is set."
        ),
    )


class SessionCreatedResponse(BaseModel):
    session_id: str
    workspace_path: str | None
    status: str = "open"


class SessionListEntry(BaseModel):
    session_id: str
    workspace_path: str | None
    status: str
    created_at: str
    last_active_at: str


class SessionListResponse(BaseModel):
    sessions: list[SessionListEntry]


class ChatRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=20_000)
    model: str | None = None
    session_id: str | None = Field(
        default=None,
        description="Continue this exact conversation. Omit to auto-resume "
        "the latest open session for the current folder (or start one).",
    )
    workspace_path: str | None = Field(
        default=None,
        description="Folder to chat about. Omit to use the active workspace.",
    )


class ChatResponse(BaseModel):
    tenant_id: str
    model: str
    status: str
    output: str
    session_id: str
    workspace_path: str | None
    resumed: bool


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


def get_ws_store(request: "Request") -> WorkspaceStateStore:
    return request.app.state.ws_store


def _resolve_workspace_or_400(
    tenant: Tenant, requested: str | None, settings: Settings
):
    """Validate a per-request workspace_path, or raise 400 with a clean detail."""
    if requested is None:
        return None
    try:
        return resolve_workspace_override(tenant, requested, settings)
    except WorkspacePathError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        )


def _mcp_key(tenant: Tenant) -> str:
    """Store key for a tenant's MCP path (shares the workspace-state store)."""
    return f"{tenant.id}#mcp"


async def _effective_mcp_definitions(
    tenant: Tenant, store: WorkspaceStateStore
) -> list[dict] | None:
    """Per-tenant MCP servers set via PUT /v1/mcp, else None (static fallback).

    A stored path that no longer resolves is skipped with a warning rather
    than failing the task.
    """
    stored = await store.get(_mcp_key(tenant))
    if stored is None:
        return None
    try:
        return discover_mcp_definitions(stored)
    except ValueError:
        logger.warning(
            "Stored MCP path %r for tenant %s is no longer valid; ignoring.",
            stored,
            tenant.id,
        )
        return None


async def _effective_workspace(
    tenant: Tenant,
    body_path: str | None,
    settings: Settings,
    store: WorkspaceStateStore,
):
    """Pick the workspace for this run: request param > stored state > default.

    A stored path is re-validated on every use (it may have been deleted or
    the tenant's allowed_roots may have changed since it was set).
    """
    if body_path is not None:
        return _resolve_workspace_or_400(tenant, body_path, settings)
    stored = await store.get(tenant.id)
    if stored is not None:
        return _resolve_workspace_or_400(tenant, stored, settings)
    return None


async def _resolve_chat_session(
    tenant: Tenant,
    body: "ChatRequest",
    settings: Settings,
    store: WorkspaceStateStore,
    pool,
) -> tuple[str, str | None, bool]:
    """Pick the conversation for this chat turn.

    Returns (session_id, workspace_path, resumed):
      * explicit session_id -> that session (409 if closed), its bound folder;
      * else resolve the folder (request > active workspace > default), then
        resume the latest open session for it, or create one.
    """
    if body.session_id is not None:
        record = await get_session_record(pool, tenant, body.session_id)
        if record["status"] != "open":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Session is closed. Start a new one (omit session_id).",
            )
        return record["id"], record["workspace_path"], True

    ws = await _effective_workspace(tenant, body.workspace_path, settings, store)
    ws_key = str(ws) if ws is not None else None

    existing = await find_latest_open_session(pool, tenant, ws_key)
    if existing is not None:
        return existing, ws_key, True

    session_id = uuid.uuid4().hex
    await create_session_row(pool, tenant, session_id, workspace_path=ws_key)
    return session_id, ws_key, False


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
    workspace_override=None,
    mcp_definitions: list[dict] | None = None,
):
    try:
        async for event_type, payload in run_coding_task_stream(
            tenant=tenant,
            prompt=prompt,
            settings=settings,
            model_override=model_override,
            session=session,
            workspace_override=workspace_override,
            mcp_definitions=mcp_definitions,
        ):
            line = f"event: {event_type}\ndata: {json.dumps(payload)}\n\n"
            yield line.encode("utf-8")
    except Exception as exc:
        logger.exception("Stream failed for tenant %s", tenant.id)
        err = {"error": type(exc).__name__, "detail": str(exc)}
        yield f"event: error\ndata: {json.dumps(err)}\n\n".encode("utf-8")


# ---------------------------------------------------------------------------
# Workspace state endpoints
# ---------------------------------------------------------------------------


@app.put("/v1/workspace", response_model=WorkspaceResponse)
async def set_workspace(
    body: WorkspaceRequest,
    tenant: Tenant = Depends(get_current_tenant),
    settings: Settings = Depends(get_settings),
    store: WorkspaceStateStore = Depends(get_ws_store),
) -> WorkspaceResponse:
    """Set the folder the agent runs in for all subsequent tasks by this tenant."""
    resolved = _resolve_workspace_or_400(tenant, body.path, settings)
    await store.set(tenant.id, str(resolved))
    return WorkspaceResponse(tenant_id=tenant.id, path=str(resolved))


@app.get("/v1/workspace", response_model=WorkspaceResponse)
async def get_workspace(
    tenant: Tenant = Depends(get_current_tenant),
    store: WorkspaceStateStore = Depends(get_ws_store),
) -> WorkspaceResponse:
    """Show the currently active workspace (null = tenant default)."""
    return WorkspaceResponse(tenant_id=tenant.id, path=await store.get(tenant.id))


@app.delete("/v1/workspace", response_model=WorkspaceResponse)
async def clear_workspace(
    tenant: Tenant = Depends(get_current_tenant),
    store: WorkspaceStateStore = Depends(get_ws_store),
) -> WorkspaceResponse:
    """Reset to the tenant's default workspace."""
    await store.clear(tenant.id)
    return WorkspaceResponse(tenant_id=tenant.id, path=None)


# ---------------------------------------------------------------------------
# MCP endpoints
# ---------------------------------------------------------------------------


@app.put("/v1/mcp", response_model=McpResponse)
async def set_mcp(
    body: McpRequest,
    tenant: Tenant = Depends(get_current_tenant),
    settings: Settings = Depends(get_settings),
    store: WorkspaceStateStore = Depends(get_ws_store),
) -> McpResponse:
    """Enable MCP servers from a path for all subsequent tasks by this tenant.

    The path is authorized like a workspace path (allowed_roots /
    ALLOW_ANY_WORKSPACE_PATH), then each discovered server is launched once to
    verify it starts and to report its tools.
    """
    # Authorize: the path (or its parent, for a file) must be an allowed dir.
    probe = Path(body.path).expanduser()
    probe_dir = probe if probe.is_dir() else probe.parent
    _resolve_workspace_or_400(tenant, str(probe_dir), settings)

    try:
        definitions = discover_mcp_definitions(body.path)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc))

    # Validation pass: actually launch each server and list its tools.
    servers_info: list[McpServerInfo] = []
    async with AsyncExitStack() as stack:
        servers = await connect_mcp_servers(definitions, stack)
        for srv in servers:
            tools = await srv.list_tools()
            servers_info.append(
                McpServerInfo(name=srv.name, tools=[t.name for t in tools])
            )
    if not servers_info:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="No MCP server at that path could be started (see server logs).",
        )

    await store.set(_mcp_key(tenant), body.path)
    return McpResponse(tenant_id=tenant.id, path=body.path, servers=servers_info)


@app.get("/v1/mcp", response_model=McpResponse)
async def get_mcp(
    tenant: Tenant = Depends(get_current_tenant),
    store: WorkspaceStateStore = Depends(get_ws_store),
) -> McpResponse:
    """Show the tenant's MCP servers with their live tool lists.

    Servers are launched fresh (read-only, nothing is stored), so this always
    reflects the current state of the server files — exactly what the agent
    will see on its next task.
    """
    stored = await store.get(_mcp_key(tenant))
    servers: list[McpServerInfo] = []
    if stored is not None:
        try:
            definitions = discover_mcp_definitions(stored)
        except ValueError:
            definitions = []
        if definitions:
            async with AsyncExitStack() as stack:
                for srv in await connect_mcp_servers(definitions, stack):
                    tools = await srv.list_tools()
                    servers.append(
                        McpServerInfo(name=srv.name, tools=[t.name for t in tools])
                    )
    return McpResponse(tenant_id=tenant.id, path=stored, servers=servers)


@app.delete("/v1/mcp", response_model=McpResponse)
async def clear_mcp(
    tenant: Tenant = Depends(get_current_tenant),
    store: WorkspaceStateStore = Depends(get_ws_store),
) -> McpResponse:
    """Disable per-tenant MCP servers (static MCP_CONFIG fallback still applies)."""
    await store.clear(_mcp_key(tenant))
    return McpResponse(tenant_id=tenant.id, path=None, servers=[])


@app.post("/v1/tasks", response_model=TaskResponse)
async def create_task(
    body: TaskRequest,
    tenant: Tenant = Depends(get_current_tenant),
    settings: Settings = Depends(get_settings),
    store: WorkspaceStateStore = Depends(get_ws_store),
) -> TaskResponse:
    """Run a coding task synchronously in the tenant's isolated sandbox."""
    workspace = await _effective_workspace(tenant, body.workspace_path, settings, store)
    mcp_defs = await _effective_mcp_definitions(tenant, store)
    try:
        result = await run_coding_task(
            tenant=tenant,
            prompt=body.prompt,
            settings=settings,
            model_override=body.model,
            workspace_override=workspace,
            mcp_definitions=mcp_defs,
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
    store: WorkspaceStateStore = Depends(get_ws_store),
) -> StreamingResponse:
    """Run a one-shot coding task and stream events as SSE."""
    workspace = await _effective_workspace(tenant, body.workspace_path, settings, store)
    mcp_defs = await _effective_mcp_definitions(tenant, store)
    return _sse_response(
        _stream_task(
            tenant,
            body.prompt,
            settings,
            body.model,
            session=None,
            workspace_override=workspace,
            mcp_definitions=mcp_defs,
        )
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
    body: SessionCreateRequest | None = None,
    tenant: Tenant = Depends(get_current_tenant),
    settings: Settings = Depends(get_settings),
    pool=Depends(get_pool),
    store: WorkspaceStateStore = Depends(get_ws_store),
) -> SessionCreatedResponse:
    """Start a conversation bound to a folder (default: the active workspace)."""
    body_path = body.workspace_path if body else None
    ws = await _effective_workspace(tenant, body_path, settings, store)
    ws_key = str(ws) if ws is not None else None
    session_id = uuid.uuid4().hex
    await create_session_row(pool, tenant, session_id, workspace_path=ws_key)
    return SessionCreatedResponse(session_id=session_id, workspace_path=ws_key)


@app.post("/v1/sessions/{session_id}/close", response_model=SessionCreatedResponse)
async def close_session(
    session_id: str,
    tenant: Tenant = Depends(get_current_tenant),
    pool=Depends(get_pool),
) -> SessionCreatedResponse:
    """Close a conversation. History is kept (DELETE purges it); /v1/chat will
    no longer auto-resume it."""
    await close_session_for_tenant(pool, tenant, session_id)
    record = await get_session_record(pool, tenant, session_id)
    return SessionCreatedResponse(
        session_id=session_id,
        workspace_path=record["workspace_path"],
        status="closed",
    )


# ---------------------------------------------------------------------------
# Chat: conversational entry point with folder-scoped auto-resume
# ---------------------------------------------------------------------------


@app.post("/v1/chat", response_model=ChatResponse)
async def chat(
    body: ChatRequest,
    tenant: Tenant = Depends(get_current_tenant),
    settings: Settings = Depends(get_settings),
    pool=Depends(get_pool),
    store: WorkspaceStateStore = Depends(get_ws_store),
) -> ChatResponse:
    """One conversational turn. Auto-resumes the latest open session for the
    current folder, or starts a new one; pass session_id to pin a conversation.
    """
    session_id, ws_key, resumed = await _resolve_chat_session(
        tenant, body, settings, store, pool
    )
    workspace = (
        _resolve_workspace_or_400(tenant, ws_key, settings) if ws_key else None
    )
    await touch_session(pool, session_id)
    mcp_defs = await _effective_mcp_definitions(tenant, store)
    try:
        result = await run_coding_task(
            tenant=tenant,
            prompt=body.prompt,
            settings=settings,
            model_override=body.model,
            session=PostgresSession(session_id, pool),
            workspace_override=workspace,
            mcp_definitions=mcp_defs,
        )
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=f"Task exceeded {settings.task_timeout_seconds}s.",
        )
    except Exception as exc:
        logger.exception(
            "Chat turn failed (tenant=%s session=%s)", tenant.id, session_id
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Agent run failed: {type(exc).__name__}",
        )

    return ChatResponse(
        tenant_id=result.tenant_id,
        model=result.model,
        status="completed",
        output=str(result.output),
        session_id=session_id,
        workspace_path=ws_key,
        resumed=resumed,
    )


@app.post("/v1/chat/stream")
async def chat_stream(
    body: ChatRequest,
    tenant: Tenant = Depends(get_current_tenant),
    settings: Settings = Depends(get_settings),
    pool=Depends(get_pool),
    store: WorkspaceStateStore = Depends(get_ws_store),
) -> StreamingResponse:
    """SSE variant of /v1/chat. First event is `session` with the session_id
    in use, so clients learn the conversation handle immediately."""
    session_id, ws_key, resumed = await _resolve_chat_session(
        tenant, body, settings, store, pool
    )
    workspace = (
        _resolve_workspace_or_400(tenant, ws_key, settings) if ws_key else None
    )
    await touch_session(pool, session_id)
    mcp_defs = await _effective_mcp_definitions(tenant, store)

    async def sse():
        head = {
            "session_id": session_id,
            "workspace_path": ws_key,
            "resumed": resumed,
        }
        yield f"event: session\ndata: {json.dumps(head)}\n\n".encode("utf-8")
        async for chunk in _stream_task(
            tenant,
            body.prompt,
            settings,
            body.model,
            session=PostgresSession(session_id, pool),
            workspace_override=workspace,
            mcp_definitions=mcp_defs,
        ):
            yield chunk

    return _sse_response(sse())


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
    store: WorkspaceStateStore = Depends(get_ws_store),
) -> TaskResponse:
    """Run one more turn against the named session. Prior history is replayed.

    The workspace is the folder the session was bound to at creation (a
    request-level workspace_path overrides for this turn only).
    """
    record = await get_session_record(pool, tenant, session_id)
    bound = body.workspace_path or record["workspace_path"]
    workspace = (
        _resolve_workspace_or_400(tenant, bound, settings) if bound else None
    )
    await touch_session(pool, session_id)
    mcp_defs = await _effective_mcp_definitions(tenant, store)
    session = PostgresSession(session_id, pool)
    try:
        result = await run_coding_task(
            tenant=tenant,
            prompt=body.prompt,
            settings=settings,
            model_override=body.model,
            session=session,
            workspace_override=workspace,
            mcp_definitions=mcp_defs,
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
    store: WorkspaceStateStore = Depends(get_ws_store),
) -> StreamingResponse:
    """SSE-streamed variant of POST /v1/sessions/{id}/messages."""
    record = await get_session_record(pool, tenant, session_id)
    bound = body.workspace_path or record["workspace_path"]
    workspace = (
        _resolve_workspace_or_400(tenant, bound, settings) if bound else None
    )
    await touch_session(pool, session_id)
    mcp_defs = await _effective_mcp_definitions(tenant, store)
    session = PostgresSession(session_id, pool)
    return _sse_response(
        _stream_task(
            tenant,
            body.prompt,
            settings,
            body.model,
            session=session,
            workspace_override=workspace,
            mcp_definitions=mcp_defs,
        )
    )
