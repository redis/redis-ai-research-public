"""Builds and runs a per-tenant sandbox coding agent.

Architecture: orchestrator + parallel subagents.

The orchestrator is the agent /v1/tasks talks to. It keeps the long-running
conversation and has one extra tool, `invoke_subagents`, that fans work out to
short-lived worker agents. Each worker runs in its own sandbox session with a
fresh context window, does one focused chunk of work, and returns a short
report. Passing N subtasks runs them concurrently via asyncio.gather.

Why: long coding tasks blow the orchestrator's token budget if every file read
and command output sits in its context. Subagents absorb that detail and return
only conclusions.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Literal, Optional

import httpx
from agents import Runner, SessionABC, function_tool
from agents.run import RunConfig
from agents.sandbox import SandboxAgent, SandboxRunConfig
from agents.sandbox.capabilities import Capabilities
from pydantic import BaseModel, Field

from .config import Settings
from .tenancy import Tenant, build_manifest, make_sandbox_client

WEBFETCH_MAX_BYTES = 200_000
WEBFETCH_TIMEOUT_S = 20.0

ORCHESTRATOR_INSTRUCTIONS = (
    "You are a careful software engineer working inside an isolated sandbox. "
    "The tenant's project is mounted at `repo/`. Before changing anything, read "
    "the relevant files to understand the code. Make the smallest change that "
    "satisfies the request and preserve existing behavior. Edit files with "
    "apply_patch using paths relative to the sandbox workspace root. When you "
    "run commands, prefer the project's own test/build commands and state the "
    "exact command you ran. Finish with a short summary of what you changed and "
    "how you verified it. Never touch anything outside `repo/`.\n\n"
    "You have a `webfetch` tool for reading public web pages or API responses "
    "when the task needs info the workspace doesn't have (docs, release notes, "
    "etc.). Don't use it for anything that should come from the codebase.\n\n"
    "You have `todo_write` and `todo_read` tools for tracking multi-step plans. "
    "On non-trivial tasks, call `todo_write` early to lay out the steps, then "
    "update statuses as you go. The list resets each task.\n\n"
    "You have an `invoke_subagents` tool that spawns focused worker subagents. "
    "Use it when work decomposes into independent pieces — e.g. exploring "
    "several modules at once, running multiple independent analyses, or "
    "drafting alternative approaches in parallel. Pass MULTIPLE subtasks in one "
    "call to run them concurrently. Pass a single subtask when later steps "
    "depend on the result. Subagents share the same workspace, so do NOT run "
    "parallel subagents that write to the same files — keep parallel work "
    "read-only or partition the files by directory. Each subagent returns a "
    "short report; integrate those reports yourself."
)

WORKER_INSTRUCTIONS = (
    "You are a focused worker subagent. You share the tenant's sandbox "
    "workspace, mounted at `repo/`. Do exactly the task the orchestrator gave "
    "you and nothing else. Return a concise factual report — what you found, "
    "what you changed, what command you ran and its outcome. Your output goes "
    "back to the orchestrator, not the end user, so skip pleasantries and lead "
    "with the findings."
)


@dataclass
class TaskResult:
    tenant_id: str
    model: str
    output: Any


def _resolve_model(tenant: Tenant, settings: Settings, override: Optional[str]):
    """Return either a model-name string or a Model instance.

    If a tenant has its own OpenAI key (e.g. for billing isolation), we build a
    dedicated Responses model bound to that key. This is concurrency-safe
    because the client is attached to the agent instance, not set globally.
    Otherwise we return the model name and let the SDK use the default client
    configured from OPENAI_API_KEY.
    """
    model_name = override or tenant.model or settings.default_model
    if not tenant.openai_api_key:
        return model_name

    from openai import AsyncOpenAI

    try:
        from agents import OpenAIResponsesModel
    except ImportError:  # module layout differs across versions
        from agents.models.openai_responses import OpenAIResponsesModel

    client = AsyncOpenAI(api_key=tenant.openai_api_key)
    return OpenAIResponsesModel(model=model_name, openai_client=client)


def _build_worker_agent(
    tenant: Tenant, settings: Settings, model_override: Optional[str]
) -> SandboxAgent:
    return SandboxAgent(
        name=f"worker[{tenant.id}]",
        model=_resolve_model(tenant, settings, model_override),
        instructions=WORKER_INSTRUCTIONS,
        default_manifest=build_manifest(tenant, settings),
        capabilities=Capabilities.default(),
    )


def _make_invoke_subagents_tool(
    tenant: Tenant, settings: Settings, model_override: Optional[str]
):
    """Build the orchestrator's subagent-spawning tool, bound to this tenant."""
    # Workers get a smaller turn budget so a runaway worker can't burn the
    # orchestrator's whole allowance.
    worker_max_turns = max(5, (tenant.max_turns or settings.max_turns) // 2)

    @function_tool
    async def invoke_subagents(subtasks: list[str]) -> str:
        """Spawn one or more worker subagents and return their combined reports.

        Pass MULTIPLE subtasks in one call to run them in parallel. Only do this
        when the subtasks are independent — e.g. read-only exploration, or
        writes to non-overlapping files. For dependent work, call this tool
        repeatedly with one subtask at a time so the orchestrator can react to
        each report before the next step.

        Each subtask string is the full instruction the worker will see, so
        write it as a self-contained prompt: state the goal, the files in
        scope, and what to report back.
        """
        async def _run_one(idx: int, task_text: str) -> str:
            worker = _build_worker_agent(tenant, settings, model_override)
            client = make_sandbox_client(settings)
            run_config = RunConfig(
                sandbox=SandboxRunConfig(client=client),
                workflow_name=f"tenant:{tenant.id}:worker:{idx}",
            )
            result = await Runner.run(
                worker,
                task_text,
                run_config=run_config,
                max_turns=worker_max_turns,
            )
            return f"[subagent {idx}] {result.final_output}"

        outputs = await asyncio.gather(
            *(_run_one(i, t) for i, t in enumerate(subtasks)),
            return_exceptions=True,
        )
        rendered = []
        for i, out in enumerate(outputs):
            if isinstance(out, Exception):
                rendered.append(
                    f"[subagent {i}] FAILED: {type(out).__name__}: {out}"
                )
            else:
                rendered.append(out)
        return "\n\n".join(rendered)

    return invoke_subagents


def _make_webfetch_tool():
    """Fetch a URL from the host process (not the sandbox).

    Note: this runs in the FastAPI server's network namespace, not the tenant's
    sandbox. If you need per-tenant network isolation, replace this with a
    shell `curl` call inside the sandbox instead.
    """
    @function_tool
    async def webfetch(url: str) -> str:
        """Fetch the body of a URL (HTTP/HTTPS only) and return it as text.

        Use for public docs, release notes, or API responses the workspace
        doesn't contain. Response is truncated to ~200KB.
        """
        if not url.startswith(("http://", "https://")):
            return f"ERROR: webfetch only supports http(s) URLs (got: {url!r})"
        try:
            async with httpx.AsyncClient(
                timeout=WEBFETCH_TIMEOUT_S, follow_redirects=True
            ) as client:
                resp = await client.get(url)
            body = resp.text
            truncated = len(body.encode("utf-8")) > WEBFETCH_MAX_BYTES
            if truncated:
                body = body.encode("utf-8")[:WEBFETCH_MAX_BYTES].decode(
                    "utf-8", errors="ignore"
                )
            suffix = "\n\n[truncated]" if truncated else ""
            return f"HTTP {resp.status_code} {url}\n\n{body}{suffix}"
        except httpx.HTTPError as exc:
            return f"ERROR: {type(exc).__name__}: {exc}"

    return webfetch


class TodoItem(BaseModel):
    id: str = Field(..., description="Stable id, e.g. '1', '2a'.")
    content: str = Field(..., description="What this step is.")
    status: Literal["pending", "in_progress", "completed"] = "pending"


def _make_todo_tools():
    """Per-task todo list. State lives in a closure so it resets per run."""
    state: list[TodoItem] = []

    def _render() -> str:
        if not state:
            return "(no todos)"
        marks = {"pending": "[ ]", "in_progress": "[~]", "completed": "[x]"}
        return "\n".join(f"{marks[i.status]} {i.id}: {i.content}" for i in state)

    @function_tool
    async def todo_write(items: list[TodoItem]) -> str:
        """Replace the current todo list with `items`.

        Each item: id (stable string), content (the step), status
        (pending|in_progress|completed). At most one item should be in_progress
        at a time. Call this whenever the plan changes or a step moves between
        states. Returns the new list.
        """
        nonlocal state
        state = list(items)
        return _render()

    @function_tool
    async def todo_read() -> str:
        """Return the current todo list."""
        return _render()

    return todo_write, todo_read


def build_sandbox_agent(
    tenant: Tenant, settings: Settings, model_override: Optional[str] = None
) -> SandboxAgent:
    todo_write, todo_read = _make_todo_tools()
    return SandboxAgent(
        name=f"coding-agent[{tenant.id}]",
        model=_resolve_model(tenant, settings, model_override),
        instructions=ORCHESTRATOR_INSTRUCTIONS,
        default_manifest=build_manifest(tenant, settings),
        capabilities=Capabilities.default(),
        tools=[
            _make_invoke_subagents_tool(tenant, settings, model_override),
            _make_webfetch_tool(),
            todo_write,
            todo_read,
        ],
    )


def _summarize_event(event) -> Optional[dict]:
    """Project a stream event into a small JSON-friendly dict.

    Returns None for events we don't want to forward (e.g. token deltas, which
    are very chatty). Tweak the filters here to taste.
    """
    etype = getattr(event, "type", type(event).__name__)

    if etype == "agent_updated_stream_event":
        return {"type": "agent_updated", "agent": getattr(event.new_agent, "name", "?")}

    if etype == "run_item_stream_event":
        item = event.item
        item_type = getattr(item, "type", type(item).__name__)
        out: dict = {"type": "run_item", "item": item_type}
        # Try to pull the most useful bit out of each item kind.
        if item_type == "tool_call_item":
            raw = getattr(item, "raw_item", None)
            out["tool"] = getattr(raw, "name", None) or getattr(raw, "type", None)
            args = getattr(raw, "arguments", None)
            if args is not None:
                out["arguments"] = (
                    args if isinstance(args, str) else str(args)
                )[:500]
        elif item_type == "tool_call_output_item":
            output = getattr(item, "output", "")
            out["output"] = (str(output) if output is not None else "")[:500]
        elif item_type == "message_output_item":
            raw = getattr(item, "raw_item", None)
            content = getattr(raw, "content", None)
            # `content` is typically a list of ResponseOutputText (or dict) blocks.
            # Extract their `.text` so the SSE stream shows the actual string.
            if isinstance(content, list):
                parts = []
                for c in content:
                    t = getattr(c, "text", None)
                    if t is None and isinstance(c, dict):
                        t = c.get("text")
                    if t:
                        parts.append(t)
                out["text"] = "".join(parts)[:500]
            elif content is None:
                out["text"] = ""
            else:
                out["text"] = str(content)[:500]
        elif item_type == "reasoning_item":
            out["summary"] = "(reasoning)"
        return out

    # Skip raw token-level events — they explode the stream.
    if etype == "raw_response_event":
        return None

    return {"type": etype}


async def run_coding_task_stream(
    tenant: Tenant,
    prompt: str,
    settings: Settings,
    model_override: Optional[str] = None,
    session: Optional[SessionABC] = None,
):
    """Async generator: yields (event_type, payload_dict) for one task.

    The last yielded event is always ("done", {"output": ..., "model": ...}).
    """
    agent = build_sandbox_agent(tenant, settings, model_override)
    client = make_sandbox_client(settings)
    run_config = RunConfig(
        sandbox=SandboxRunConfig(client=client),
        workflow_name=f"tenant:{tenant.id}",
    )
    max_turns = tenant.max_turns or settings.max_turns

    streamed = Runner.run_streamed(
        agent, prompt, run_config=run_config, max_turns=max_turns, session=session
    )

    async def _iter():
        async for event in streamed.stream_events():
            summary = _summarize_event(event)
            if summary is not None:
                yield summary["type"], summary
        model_name = model_override or tenant.model or settings.default_model
        yield "done", {
            "model": model_name,
            "output": str(streamed.final_output),
        }

    # Honor the per-task timeout the same way run_coding_task does.
    async def _with_timeout():
        async for ev in _iter():
            yield ev

    timeout = settings.task_timeout_seconds
    agen = _with_timeout()
    try:
        while True:
            try:
                ev = await asyncio.wait_for(agen.__anext__(), timeout=timeout)
            except StopAsyncIteration:
                return
            yield ev
    except asyncio.TimeoutError:
        yield "error", {
            "error": "TimeoutError",
            "detail": f"Task exceeded {timeout}s.",
        }


async def run_coding_task(
    tenant: Tenant,
    prompt: str,
    settings: Settings,
    model_override: Optional[str] = None,
    session: Optional[SessionABC] = None,
) -> TaskResult:
    """Run one coding task for a tenant in a fresh, isolated sandbox session."""
    agent = build_sandbox_agent(tenant, settings, model_override)
    client = make_sandbox_client(settings)

    run_config = RunConfig(
        sandbox=SandboxRunConfig(client=client),
        workflow_name=f"tenant:{tenant.id}",
    )
    max_turns = tenant.max_turns or settings.max_turns

    result = await asyncio.wait_for(
        Runner.run(
            agent, prompt, run_config=run_config, max_turns=max_turns, session=session
        ),
        timeout=settings.task_timeout_seconds,
    )

    model_name = model_override or tenant.model or settings.default_model
    return TaskResult(tenant_id=tenant.id, model=model_name, output=result.final_output)
