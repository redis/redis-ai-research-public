"""Builds and runs a per-tenant coding agent that executes directly on the host.

Architecture: orchestrator + parallel subagents.

The orchestrator is the agent the API talks to. It keeps the long-running
conversation and has an `invoke_subagents` tool that fans work out to
short-lived worker agents, each with a fresh context window. Passing N
subtasks runs them concurrently via asyncio.gather.

Execution model: NO sandbox. Tools run in this process against the tenant's
real workspace folder — shell commands run with cwd=<workspace>, file tools
are confined to paths under <workspace>. Edits write through to the real
files. Isolation, if needed, is handled outside this service.
"""
from __future__ import annotations

import asyncio
import json
import logging
from contextlib import AsyncExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Optional

import httpx
from agents import Agent, Runner, SessionABC, function_tool
from agents.mcp import MCPServerStdio
from agents.run import RunConfig
from pydantic import BaseModel, Field

from .config import Settings
from .tenancy import Tenant, resolve_local_workspace

logger = logging.getLogger("coding_agent.agent")

WEBFETCH_MAX_BYTES = 200_000
WEBFETCH_TIMEOUT_S = 20.0
SHELL_TIMEOUT_S = 120
TOOL_OUTPUT_MAX_CHARS = 20_000

ORCHESTRATOR_INSTRUCTIONS_TEMPLATE = (
    "You are a capable general assistant and careful software engineer. "
    "You work directly in the user's real workspace folder:\n"
    "    {root}\n"
    "All your tools operate on the actual files — edits are real and "
    "persistent, so be deliberate. Use paths relative to the workspace root "
    "(absolute paths under it also work). Never touch anything outside it.\n\n"
    "For code tasks: read the relevant files before changing anything, make "
    "the smallest change that satisfies the request, prefer the project's own "
    "test/build commands, and state the exact command you ran. Finish with a "
    "short summary of what you changed and how you verified it.\n\n"
    "You are not limited to coding tasks. For general questions needing the "
    "web, PREFER the Exa tools when available: `web_search_exa` to FIND pages "
    "and `web_fetch_exa` to READ them (it extracts clean text even from "
    "JavaScript-heavy sites). Fall back to the built-in `webfetch` when Exa "
    "tools are unavailable or a specific call fails, and use `webfetch` "
    "directly for open JSON APIs (e.g. Wikipedia's REST API, open-data "
    "endpoints). Never fetch search-engine result pages (Google/Bing) — they "
    "block plain fetches. Try before declaring something impossible: if one "
    "source fails, try another phrasing or site, and cite which URL your "
    "answer came from.\n\n"
    "If the request is ambiguous, underspecified, or could be interpreted in "
    "meaningfully different ways (which file? destructive or not? what output "
    "format?), ASK a concise clarifying question and end your turn instead of "
    "guessing — this is a conversation, and the user's next message will "
    "answer you. Ask at most 1-2 pointed questions, and offer the options you "
    "see. Only proceed without asking when one interpretation is clearly "
    "reasonable; in that case state your assumption up front.\n\n"
    "When calling tools, apply only the constraints the user actually stated, "
    "and aim for COMPLETE results. Never add restrictive parameters (fare or "
    "category exclusions, low result limits) the user did not ask for — and "
    "check parameter descriptions for filters that are restrictive BY DEFAULT "
    "and explicitly disable them (e.g. a default-true 'exclude_X' flag should "
    "be passed as false) unless the user asked for that narrowing. If a "
    "result set looks suspiciously small or says has_more, broaden or "
    "paginate before answering. Report the full result set you got.\n\n"
    "You have `todo_write` and `todo_read` tools for tracking multi-step plans. "
    "On non-trivial tasks, call `todo_write` early to lay out the steps, then "
    "update statuses as you go. The list resets each task.\n\n"
    "You have an `invoke_subagents` tool that spawns focused worker subagents. "
    "Use it when work decomposes into independent pieces — e.g. exploring "
    "several modules at once or running multiple independent analyses. Pass "
    "MULTIPLE subtasks in one call to run them concurrently; pass a single "
    "subtask when later steps depend on the result. Subagents work in the SAME "
    "real folder, so do NOT run parallel subagents that write to the same "
    "files — keep parallel work read-only or partition by file. Each subagent "
    "returns a short report; integrate those reports yourself."
)

WORKER_INSTRUCTIONS_TEMPLATE = (
    "You are a focused worker subagent operating directly in the user's real "
    "workspace folder: {root}. Edits are real. Do exactly the task the "
    "orchestrator gave you and nothing else. Return a concise factual report "
    "— what you found, what you changed, what command you ran and its "
    "outcome. Your output goes back to the orchestrator, not the end user, so "
    "skip pleasantries and lead with the findings."
)


@dataclass
class TaskResult:
    tenant_id: str
    model: str
    output: Any


AGENTS_MD_MAX_CHARS = 30_000


def _load_agents_md(root: Path) -> Optional[str]:
    """Read AGENTS.md from the workspace root, if present.

    Loaded fresh on every run so edits apply immediately. Truncated to keep a
    runaway file from eating the context window.
    """
    p = root / "AGENTS.md"
    if not p.is_file():
        return None
    try:
        text = p.read_text("utf-8", errors="replace").strip()
    except OSError:
        return None
    if not text:
        return None
    if len(text) > AGENTS_MD_MAX_CHARS:
        text = text[:AGENTS_MD_MAX_CHARS] + "\n\n[AGENTS.md truncated]"
    return text


def _with_agents_md(instructions: str, agents_md: Optional[str]) -> str:
    if agents_md is None:
        return instructions
    return (
        f"{instructions}\n\n"
        f"## Workspace instructions (from AGENTS.md in the workspace root)\n"
        f"Follow these for all work in this workspace:\n\n{agents_md}"
    )


async def _prepare_agents_md(
    root: Path, prompt: str, session: Optional[SessionABC]
) -> tuple[str, Optional[str], dict]:
    """Decide how AGENTS.md reaches the model.

    Returns (prompt, instructions_md, info) where info describes what happened
    (surfaced as a `workspace_instructions` SSE event on streaming endpoints).

    * One-shot task (no session): AGENTS.md goes into the instructions —
      re-read every run, never persisted.
    * First message of a session: AGENTS.md is prepended to the user message,
      so it's stored ONCE in history and replayed on every later turn.
    * Later session turns: nothing added — the copy in history covers it.
    """
    agents_md = _load_agents_md(root)
    if agents_md is None:
        return prompt, None, {"loaded": False, "reason": "no AGENTS.md in workspace root"}

    if session is None:
        return prompt, agents_md, {
            "loaded": True, "mode": "instructions", "chars": len(agents_md),
        }
    existing = await session.get_items(limit=1)
    if existing:
        return prompt, None, {
            "loaded": True, "mode": "replayed_from_session_history",
        }
    return (
        f"## Workspace instructions (from AGENTS.md in the workspace root)\n"
        f"Follow these for all work in this conversation:\n\n{agents_md}\n\n"
        f"---\n\n{prompt}",
        None,
        {"loaded": True, "mode": "first_message", "chars": len(agents_md)},
    )


# ---------------------------------------------------------------------------
# MCP: discovery + connection
# ---------------------------------------------------------------------------


def _parse_mcp_json(path: Path) -> list[dict]:
    """Parse an mcp.json into normalized server definitions.

    Accepts {"servers": {...}}, Claude-style {"mcpServers": {...}}, and
    OpenCode-style {"mcp": {...}} keys. Entry shapes:
      * local/stdio:  {"command": "npx", "args": [...]} — OpenCode's
        command-as-array (["npx", "-y", "pkg"]) is normalized too.
      * remote:       {"url": "https://...", "transport": "http"|"sse",
                       "headers": {...}} — transport defaults to http
        (streamable), or sse when the URL ends with /sse.
    """
    text = path.read_text("utf-8")
    # Tolerate .jsonc-style line comments (OpenCode configs use them).
    text = "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("//")
    )
    data = json.loads(text)
    servers = data.get("servers") or data.get("mcpServers") or data.get("mcp") or {}
    defs = []
    for name, cfg in servers.items():
        d = {"name": name, **cfg}
        d.pop("type", None)  # OpenCode's "local"/"remote" marker; url implies remote
        cmd = d.get("command")
        if isinstance(cmd, list):  # OpenCode style: command is the full argv
            d["command"] = cmd[0]
            d["args"] = cmd[1:] + list(d.get("args", []))
        defs.append(d)
    return defs


def _nearest_pyproject_dir(start: Path) -> Optional[Path]:
    """Walk up from `start` looking for a pyproject.toml (max 4 levels)."""
    current = start if start.is_dir() else start.parent
    for _ in range(4):
        if (current / "pyproject.toml").exists():
            return current
        if current.parent == current:
            break
        current = current.parent
    return None


def _py_server_definition(f: Path) -> Optional[dict]:
    """Build a launch definition for a FastMCP server script, or None if the
    file doesn't look like one (no mcp.run() entrypoint)."""
    try:
        text = f.read_text("utf-8", errors="ignore")
    except OSError:
        return None
    if ".run(" not in text or "__main__" not in text:
        return None
    project = _nearest_pyproject_dir(f)
    if project is not None:
        args = ["run", "--project", str(project), "python", str(f)]
    else:
        args = ["run", "--with", "fastmcp", "python", str(f)]
    return {"name": f.stem, "command": "uv", "args": args}


def discover_mcp_definitions(path_str: str) -> list[dict]:
    """Turn a user-supplied path into MCP server definitions.

    Accepts:
      * an http(s) URL              -> one remote server (SSE or streamable-http)
      * an mcp.json file            -> its listed servers
      * a single .py FastMCP script -> one server
      * a directory                 -> its mcp.json if present, else every
                                       *.py inside that has an mcp.run()
                                       __main__ entrypoint
    Raises ValueError if nothing usable is found.
    """
    if path_str.startswith(("http://", "https://")):
        from urllib.parse import urlparse
        host = urlparse(path_str).hostname or "remote"
        return [{"name": host.replace(".", "-"), "url": path_str}]

    path = Path(path_str).expanduser().resolve()
    if not path.exists():
        raise ValueError(f"MCP path does not exist: {path}")

    if path.is_file():
        if path.suffix == ".json":
            defs = _parse_mcp_json(path)
        elif path.suffix == ".py":
            d = _py_server_definition(path)
            defs = [d] if d else []
        else:
            raise ValueError(f"Unsupported MCP path (want .json, .py, or dir): {path}")
    else:
        cfg = path / "mcp.json"
        if cfg.exists():
            defs = _parse_mcp_json(cfg)
        else:
            defs = [
                d
                for f in sorted(path.glob("*.py"))
                if (d := _py_server_definition(f)) is not None
            ]
    if not defs:
        raise ValueError(f"No MCP servers found at {path}")
    return defs


def _load_mcp_definitions(settings: Settings) -> list[dict]:
    """Definitions from the static MCP_CONFIG fallback (may be empty)."""
    if not settings.mcp_config:
        return []
    path = Path(settings.mcp_config)
    if not path.exists():
        logger.warning("MCP_CONFIG=%s does not exist; skipping MCP.", path)
        return []
    return _parse_mcp_json(path)


def _translate_workspace_paths(value: Any, root: Path) -> Any:
    """Recursively normalize path-like tool args to absolute host paths.

    * legacy 'repo/<x>' (from old sandbox-era sessions) -> <root>/<x>
    * relative paths that exist under the workspace root -> absolute
    Everything else passes through untouched.
    """
    if isinstance(value, str):
        if value == "repo":
            return str(root)
        if value.startswith("repo/"):
            return str(root / value[len("repo/"):])
        if value and not value.startswith("/") and (root / value).exists():
            return str(root / value)
        return value
    if isinstance(value, dict):
        return {k: _translate_workspace_paths(v, root) for k, v in value.items()}
    if isinstance(value, list):
        return [_translate_workspace_paths(v, root) for v in value]
    return value


class PathTranslatingMCPServer(MCPServerStdio):
    """MCP server whose tool-call args get workspace-relative paths absolutized.

    MCP servers run with their own cwd, so relative paths the model passes
    (or legacy 'repo/...' paths from old sessions) would miss. Rewriting at
    the call boundary makes this reliable instead of prompt-dependent.
    """

    path_root: Optional[Path] = None  # set after construction when known

    async def call_tool(self, tool_name, arguments, meta=None):
        if self.path_root is not None and arguments:
            arguments = _translate_workspace_paths(arguments, self.path_root)
        return await super().call_tool(tool_name, arguments, meta)


class MCPPool:
    """Keeps MCP servers alive across runs, per tenant.

    Stateful MCP servers (e.g. BrowserMCP, whose Chrome extension connects to
    one specific server process) break if relaunched per turn. The pool
    launches each tenant's servers once and reuses them until the source list
    changes, a server stops responding, or the service shuts down.
    """

    def __init__(self) -> None:
        self._entries: dict[str, tuple[str, AsyncExitStack, list]] = {}
        self._lock = asyncio.Lock()

    async def get(
        self,
        tenant_id: str,
        definitions: list[dict],
        path_root: Optional[Path] = None,
    ) -> list:
        fingerprint = json.dumps(definitions, sort_keys=True)
        async with self._lock:
            entry = self._entries.get(tenant_id)
            if entry is not None:
                old_fp, _, servers = entry
                if old_fp == fingerprint and await self._alive(servers):
                    for srv in servers:
                        if isinstance(srv, PathTranslatingMCPServer):
                            srv.path_root = path_root
                    return servers
                await self._close_locked(tenant_id)
            stack = AsyncExitStack()
            servers = await connect_mcp_servers(definitions, stack, path_root)
            self._entries[tenant_id] = (fingerprint, stack, servers)
            return servers

    @staticmethod
    async def _alive(servers: list) -> bool:
        for srv in servers:
            try:
                session = getattr(srv, "session", None)
                if session is not None:
                    await asyncio.wait_for(session.send_ping(), timeout=5)
            except Exception:
                logger.warning("MCP server %r stopped responding; rebuilding.", srv.name)
                return False
        return True

    async def invalidate(self, tenant_id: str) -> None:
        """Close a tenant's servers (call after changing their sources)."""
        async with self._lock:
            await self._close_locked(tenant_id)

    async def _close_locked(self, tenant_id: str) -> None:
        entry = self._entries.pop(tenant_id, None)
        if entry is None:
            return
        try:
            await entry[1].aclose()
        except Exception:
            # MCP stdio cleanup can complain when closed from a different
            # task than the one that opened it; the child procs still die
            # with us at shutdown, so log and move on.
            logger.debug("MCP stack close for %s raised; ignoring.", tenant_id)

    async def close_all(self) -> None:
        async with self._lock:
            for tenant_id in list(self._entries):
                await self._close_locked(tenant_id)


mcp_pool = MCPPool()


async def connect_mcp_servers(
    definitions: list[dict],
    stack: AsyncExitStack,
    path_root: Optional[Path] = None,
) -> list[MCPServerStdio]:
    """Launch and connect MCP servers over stdio.

    Servers are tied to the passed AsyncExitStack — closing the stack shuts
    them down. A server that fails to start is skipped with a log line rather
    than failing the whole task.

    Duplicate tool names across servers are a hard error in the SDK, so we
    dedupe: the first server to expose a tool name keeps it; later servers
    have that name filtered out (logged).
    """
    from agents.mcp import MCPServerSse, MCPServerStreamableHttp, create_static_tool_filter

    servers: list = []
    seen_tools: set[str] = set()
    for d in definitions:
        try:
            if d.get("url"):
                transport = d.get("transport") or (
                    "sse" if str(d["url"]).rstrip("/").endswith("/sse") else "http"
                )
                cls = MCPServerSse if transport == "sse" else MCPServerStreamableHttp
                server = cls(
                    params={"url": d["url"], "headers": d.get("headers") or None},
                    cache_tools_list=True,
                    name=d["name"],
                )
            else:
                server = PathTranslatingMCPServer(
                    params={
                        "command": d["command"],
                        "args": d.get("args", []),
                        "env": d.get("env") or None,
                        "cwd": d.get("cwd"),
                    },
                    cache_tools_list=True,
                    name=d["name"],
                )
                server.path_root = path_root
            await stack.enter_async_context(server)
            tools = await server.list_tools()
            names = [t.name for t in tools]
            allowed = [n for n in names if n not in seen_tools]
            dropped = sorted(set(names) - set(allowed))
            if dropped:
                logger.warning(
                    "MCP server %r: dropping duplicate tools %s (already "
                    "provided by an earlier server).", d["name"], dropped,
                )
                server.tool_filter = create_static_tool_filter(
                    allowed_tool_names=allowed
                )
            seen_tools.update(allowed)
            servers.append(server)
        except Exception:
            logger.exception("MCP server %r failed to start; skipping.", d.get("name"))
    return servers


# ---------------------------------------------------------------------------
# Model resolution
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Host-execution tools (the sandbox replacement)
# ---------------------------------------------------------------------------


def _resolve_in_root(root: Path, path_str: str) -> Path:
    """Resolve a user/model-supplied path and require it to be under `root`.

    Accepts paths relative to the workspace root, absolute paths under it,
    and legacy 'repo/...' paths from old sandbox-era sessions.
    """
    cleaned = path_str.strip()
    if cleaned == "repo":
        cleaned = "."
    elif cleaned.startswith("repo/"):
        cleaned = cleaned[len("repo/"):]
    p = Path(cleaned)
    candidate = p if p.is_absolute() else root / p
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError:
        raise ValueError(
            f"Path {path_str!r} is outside the workspace root {root}. "
            "Use a path relative to the workspace root."
        )
    return resolved


def _truncate(text: str, limit: int = TOOL_OUTPUT_MAX_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n\n[truncated at {limit} chars]"


def _make_workspace_tools(root: Path) -> list:
    """Build the host-execution tool set, all confined to `root`."""

    @function_tool
    async def run_shell(cmd: str, timeout_seconds: int = SHELL_TIMEOUT_S) -> str:
        """Run a shell command with the workspace root as working directory.

        Use for listing/searching files (ls, rg, find), running tests/builds,
        git, and anything else command-line. Output (stdout+stderr) is
        truncated to ~20k chars — pipe through head/tail/rg for large output.
        The command runs on the real host: destructive commands have real
        effects, so be careful and stay inside the workspace.
        """
        try:
            proc = await asyncio.create_subprocess_shell(
                cmd,
                cwd=str(root),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            try:
                out, _ = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout_seconds
                )
            except asyncio.TimeoutError:
                proc.kill()
                return f"ERROR: command timed out after {timeout_seconds}s: {cmd}"
            text = out.decode("utf-8", errors="replace")
            return _truncate(
                f"exit code: {proc.returncode}\n{text}"
                if text
                else f"exit code: {proc.returncode} (no output)"
            )
        except Exception as exc:
            return f"ERROR running command: {type(exc).__name__}: {exc}"

    @function_tool
    async def read_file(path: str, offset: int = 0, limit: int = 1000) -> str:
        """Read a text file from the workspace.

        `offset` is the 0-based first line, `limit` the max number of lines.
        Paths are relative to the workspace root.
        """
        try:
            target = _resolve_in_root(root, path)
            lines = target.read_text("utf-8", errors="replace").splitlines()
            window = lines[offset : offset + limit]
            numbered = "\n".join(
                f"{i + offset + 1}\t{line}" for i, line in enumerate(window)
            )
            header = f"{target} ({len(lines)} lines total, showing {offset + 1}-{offset + len(window)})"
            return _truncate(f"{header}\n{numbered}")
        except Exception as exc:
            return f"ERROR: {type(exc).__name__}: {exc}"

    @function_tool
    async def write_file(path: str, content: str) -> str:
        """Create or overwrite a file in the workspace with `content`.

        Parent directories are created as needed. This writes to the REAL
        file — prefer edit_file for small changes to existing files.
        """
        try:
            target = _resolve_in_root(root, path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, "utf-8")
            return f"Wrote {len(content)} chars to {target}"
        except Exception as exc:
            return f"ERROR: {type(exc).__name__}: {exc}"

    @function_tool
    async def edit_file(path: str, old_string: str, new_string: str) -> str:
        """Replace an exact string in a file (must match exactly once).

        Read the file first so old_string matches the current content
        precisely, including whitespace. For multiple identical occurrences,
        include more surrounding context to make the match unique.
        """
        try:
            target = _resolve_in_root(root, path)
            text = target.read_text("utf-8")
            count = text.count(old_string)
            if count == 0:
                return "ERROR: old_string not found in file. Read the file and retry with the exact current text."
            if count > 1:
                return f"ERROR: old_string matches {count} times; add surrounding context to make it unique."
            target.write_text(text.replace(old_string, new_string, 1), "utf-8")
            return f"Edited {target}"
        except Exception as exc:
            return f"ERROR: {type(exc).__name__}: {exc}"

    return [run_shell, read_file, write_file, edit_file]


_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


def _make_webfetch_tool():
    """Fetch a URL. Runs in this process, like everything else now."""
    @function_tool
    async def webfetch(url: str) -> str:
        """Fetch the body of a URL (HTTP/HTTPS only) and return it as text.

        FALLBACK reader: prefer web_fetch_exa when available. Use this when
        Exa tools are missing or fail, and for direct API endpoints
        (Wikipedia REST, open-data JSON APIs). JavaScript-heavy sites return
        little useful text here. Response is truncated to ~200KB.
        """
        if not url.startswith(("http://", "https://")):
            return f"ERROR: webfetch only supports http(s) URLs (got: {url!r})"
        try:
            async with httpx.AsyncClient(
                timeout=WEBFETCH_TIMEOUT_S,
                follow_redirects=True,
                headers={"User-Agent": _BROWSER_UA},
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


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------


def resolve_workspace_root(
    tenant: Tenant, settings: Settings, workspace_override: Optional[Path]
) -> Path:
    """The real host directory the agent works in."""
    if workspace_override is not None:
        return workspace_override.resolve()
    if tenant.workspace_kind != "local_dir":
        raise ValueError(
            f"workspace_kind {tenant.workspace_kind!r} is not supported in "
            "direct-host mode; use local_dir or a workspace override."
        )
    return resolve_local_workspace(tenant, settings).resolve()


def _build_worker_agent(
    tenant: Tenant,
    settings: Settings,
    model_override: Optional[str],
    root: Path,
) -> Agent:
    return Agent(
        name=f"worker[{tenant.id}]",
        model=_resolve_model(tenant, settings, model_override),
        instructions=_with_agents_md(
            WORKER_INSTRUCTIONS_TEMPLATE.format(root=root), _load_agents_md(root)
        ),
        tools=_make_workspace_tools(root),
    )


def _make_invoke_subagents_tool(
    tenant: Tenant,
    settings: Settings,
    model_override: Optional[str],
    root: Path,
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
            worker = _build_worker_agent(tenant, settings, model_override, root)
            run_config = RunConfig(workflow_name=f"tenant:{tenant.id}:worker:{idx}")
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


def build_agent(
    tenant: Tenant,
    settings: Settings,
    model_override: Optional[str] = None,
    root: Optional[Path] = None,
    mcp_servers: Optional[list] = None,
    agents_md: Optional[str] = None,
) -> Agent:
    assert root is not None
    todo_write, todo_read = _make_todo_tools()
    return Agent(
        name=f"coding-agent[{tenant.id}]",
        model=_resolve_model(tenant, settings, model_override),
        instructions=_with_agents_md(
            ORCHESTRATOR_INSTRUCTIONS_TEMPLATE.format(root=root), agents_md
        ),
        mcp_servers=mcp_servers or [],
        tools=[
            *_make_workspace_tools(root),
            _make_invoke_subagents_tool(tenant, settings, model_override, root),
            _make_webfetch_tool(),
            todo_write,
            todo_read,
        ],
    )


# ---------------------------------------------------------------------------
# Streaming event summarizer
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Run entry points (signatures unchanged from the sandbox era)
# ---------------------------------------------------------------------------


async def run_coding_task_stream(
    tenant: Tenant,
    prompt: str,
    settings: Settings,
    model_override: Optional[str] = None,
    session: Optional[SessionABC] = None,
    workspace_override: Optional[Path] = None,
    mcp_definitions: Optional[list[dict]] = None,
):
    """Async generator: yields (event_type, payload_dict) for one task.

    The last yielded event is always ("done", {"output": ..., "model": ...}).
    """
    root = resolve_workspace_root(tenant, settings, workspace_override)
    prompt, instructions_md, agents_md_info = await _prepare_agents_md(
        root, prompt, session
    )
    yield "workspace_instructions", {
        "type": "workspace_instructions",
        "source": "AGENTS.md",
        **agents_md_info,
    }
    defs = mcp_definitions if mcp_definitions is not None else _load_mcp_definitions(settings)
    mcp_servers = await mcp_pool.get(tenant.id, defs, path_root=root)
    agent = build_agent(
        tenant, settings, model_override, root=root, mcp_servers=mcp_servers,
        agents_md=instructions_md,
    )
    run_config = RunConfig(workflow_name=f"tenant:{tenant.id}")
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

    timeout = settings.task_timeout_seconds
    agen = _iter()
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
    workspace_override: Optional[Path] = None,
    mcp_definitions: Optional[list[dict]] = None,
) -> TaskResult:
    """Run one coding task for a tenant directly in the host workspace."""
    root = resolve_workspace_root(tenant, settings, workspace_override)
    prompt, instructions_md, _ = await _prepare_agents_md(root, prompt, session)
    defs = (
        mcp_definitions
        if mcp_definitions is not None
        else _load_mcp_definitions(settings)
    )
    mcp_servers = await mcp_pool.get(tenant.id, defs, path_root=root)
    agent = build_agent(
        tenant, settings, model_override, root=root, mcp_servers=mcp_servers,
        agents_md=instructions_md,
    )
    run_config = RunConfig(workflow_name=f"tenant:{tenant.id}")
    max_turns = tenant.max_turns or settings.max_turns

    result = await asyncio.wait_for(
        Runner.run(
            agent,
            prompt,
            run_config=run_config,
            max_turns=max_turns,
            session=session,
        ),
        timeout=settings.task_timeout_seconds,
    )

    model_name = model_override or tenant.model or settings.default_model
    return TaskResult(tenant_id=tenant.id, model=model_name, output=result.final_output)
