from __future__ import annotations

import argparse
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import urlopen

import yaml
from opencode_ai import APIConnectionError, APITimeoutError, Opencode
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table


DEFAULT_RUN_COUNT = 3
MAX_OPTIMIZATION_TRIES = 3
console = Console()
STOPWORDS = {
    "a",
    "all",
    "an",
    "and",
    "are",
    "as",
    "be",
    "by",
    "for",
    "from",
    "how",
    "in",
    "is",
    "it",
    "must",
    "of",
    "on",
    "or",
    "question",
    "response",
    "satisfy",
    "should",
    "that",
    "the",
    "their",
    "this",
    "to",
    "user",
    "with",
}
QUESTIONS_TEMPLATE = """questions:
  - What percentage of cars have automatic transmissions?
  - Are young drivers more likely to drive manual transmissions?
"""
OBJECTIVE_TEMPLATE = """objectives:
  - id: use_redis_for_question
    description: The agent must know to access the redis database for the user question
    threshold: 100%
  - id: read_diagnostics_first
    description: The diagnostics/diagnose_issues.json must be read first
    threshold: 100%
"""
CONFIG_TEMPLATE = """run_count: 3
"""


@dataclass
class CriterionAssessment:
    criterion: str
    satisfied: bool
    evidence: str


@dataclass
class ObjectiveDefinition:
    id: str
    description: str
    threshold: float


@dataclass
class RunArtifact:
    run_id: int
    session_id: str
    response_text: str
    tool_activity: list[dict[str, str]]
    message: dict[str, Any]


@dataclass
class ServerHandle:
    started_by_script: bool
    base_url: str
    process: subprocess.Popen[str] | None = None
    log_path: Path | None = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run OpenCode on questions from QUESTIONS.yaml, save each response, and "
            "evaluate them against OBJECTIVE.yaml."
        )
    )
    parser.add_argument(
        "--output-dir",
        help="Directory where run artifacts are saved. Defaults to artifacts/<timestamp>.",
    )
    parser.add_argument(
        "--model",
        help="Override the OpenCode model id for all runs.",
    )
    parser.add_argument(
        "--max-tries",
        type=int,
        default=MAX_OPTIMIZATION_TRIES,
        help="Maximum number of AGENTS.md optimization attempts.",
    )
    parser.add_argument(
        "--server-start-timeout",
        type=float,
        default=30.0,
        help="Seconds to wait for a managed OpenCode server to become ready.",
    )
    parser.add_argument(
        "--no-manage-server",
        action="store_true",
        help="Do not auto-start or auto-stop the OpenCode server.",
    )
    parser.add_argument(
        "--setup",
        action="store_true",
        help="Create example QUESTIONS.yaml, OBJECTIVE.yaml, and config.yaml files, then exit.",
    )
    return parser.parse_args()


def load_questions() -> list[str]:
    questions_path = repo_root() / "QUESTIONS.yaml"
    if not questions_path.exists():
        raise FileNotFoundError("QUESTIONS.yaml was not found.")

    loaded = yaml.safe_load(questions_path.read_text(encoding="utf-8"))
    if loaded is None:
        return []

    if isinstance(loaded, list):
        raw_questions = loaded
    elif isinstance(loaded, dict):
        raw_questions = loaded.get("questions", [])
    else:
        raise ValueError("QUESTIONS.yaml must contain a list of questions or a top-level 'questions' list.")

    if not isinstance(raw_questions, list):
        raise ValueError("QUESTIONS.yaml field 'questions' must be a list.")

    questions: list[str] = []
    for item in raw_questions:
        if isinstance(item, str):
            text = item.strip()
        elif isinstance(item, dict):
            text = str(item.get("text", item.get("question", ""))).strip()
        else:
            raise ValueError("Each question in QUESTIONS.yaml must be a string or mapping.")

        if text:
            questions.append(text)

    if not questions:
        raise ValueError("QUESTIONS.yaml must contain at least one question.")
    return questions


def repo_root() -> Path:
    configured_root = os.environ.get("APP_ROOT", "").strip()
    if configured_root:
        return Path(configured_root).expanduser().resolve()

    cwd = Path.cwd().resolve()
    markers = ("QUESTIONS.yaml", "OBJECTIVE.yaml", "AGENT.md", "AGENTS.md", "opencode.jsonc")
    if any((cwd / marker).exists() for marker in markers):
        return cwd

    return Path(__file__).resolve().parent


def resolve_markdown_file(*names: str) -> Path:
    root = repo_root()
    for name in names:
        candidate = root / name
        if candidate.exists():
            return candidate
    looked_for = ", ".join(names)
    raise FileNotFoundError(f"Could not find any of: {looked_for}")


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def load_repo_config() -> dict[str, Any]:
    config_path = repo_root() / "config.yaml"
    if not config_path.exists():
        return {}

    loaded = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise ValueError("config.yaml must contain a top-level mapping.")
    return loaded


def resolve_run_count(config: dict[str, Any]) -> int:
    raw_run_count = config.get("run_count", DEFAULT_RUN_COUNT)
    if not isinstance(raw_run_count, int) or raw_run_count < 1:
        raise ValueError("config.yaml field 'run_count' must be an integer greater than 0.")
    return raw_run_count


def ensure_template_file(path: Path, template: str) -> bool:
    if path.exists():
        return False
    path.write_text(template, encoding="utf-8")
    return True


def serialize_questions(questions: list[str]) -> list[str]:
    return questions


def run_setup() -> int:
    root = repo_root()
    questions_path = root / "QUESTIONS.yaml"
    objective_path = root / "OBJECTIVE.yaml"
    config_path = root / "config.yaml"

    questions_created = ensure_template_file(questions_path, QUESTIONS_TEMPLATE)
    objective_created = ensure_template_file(objective_path, OBJECTIVE_TEMPLATE)
    config_created = ensure_template_file(config_path, CONFIG_TEMPLATE)

    console.rule("Setup")
    console.print(
        f"QUESTIONS file: [bold]{questions_path}[/bold] "
        f"({'created' if questions_created else 'already exists'})"
    )
    console.print(
        Panel(
            Markdown(f"```yaml\n{QUESTIONS_TEMPLATE.rstrip()}\n```"),
            title="QUESTIONS.yaml template",
            border_style="green",
        )
    )
    console.print(
        f"OBJECTIVE file: [bold]{objective_path}[/bold] "
        f"({'created' if objective_created else 'already exists'})"
    )
    console.print(
        Panel(
            Markdown(f"```yaml\n{OBJECTIVE_TEMPLATE.rstrip()}\n```"),
            title="OBJECTIVE.yaml template",
            border_style="blue",
        )
    )
    console.print(
        f"Config file: [bold]{config_path}[/bold] "
        f"({'created' if config_created else 'already exists'})"
    )
    console.print(
        Panel(
            Markdown(f"```yaml\n{CONFIG_TEMPLATE.rstrip()}\n```"),
            title="config.yaml template",
            border_style="magenta",
        )
    )
    console.print(
        "Created [bold]config.yaml[/bold] with a default run count when needed. "
        "Fill in [bold]QUESTIONS.yaml[/bold] and [bold]OBJECTIVE.yaml[/bold], "
        "then rerun [bold]./.venv/bin/python main.py[/bold] without [bold]--setup[/bold]."
    )
    return 0


def base_url() -> str:
    return os.environ.get("OPENCODE_BASE_URL", "http://localhost:4096")


def server_is_ready(target_base_url: str) -> bool:
    try:
        with urlopen(f"{target_base_url}/config", timeout=2) as response:
            return 200 <= response.status < 300
    except (OSError, URLError):
        return False


def parse_host_port(target_base_url: str) -> tuple[str, int]:
    parsed = urlparse(target_base_url)
    if not parsed.scheme or not parsed.hostname or not parsed.port:
        raise RuntimeError(
            f"OPENCODE_BASE_URL must include scheme, host, and port. Got: {target_base_url!r}"
        )
    return parsed.hostname, parsed.port


def wait_for_server(target_base_url: str, timeout_seconds: float, process: subprocess.Popen[str] | None) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if server_is_ready(target_base_url):
            return
        if process is not None and process.poll() is not None:
            raise RuntimeError("Managed OpenCode server exited before becoming ready.")
        time.sleep(0.5)
    raise RuntimeError(
        f"Server startup timed out: OpenCode at {target_base_url} did not become ready within "
        f"{timeout_seconds:.0f}s."
    )


def start_server_if_needed(output_dir: Path, timeout_seconds: float, manage_server: bool) -> ServerHandle:
    target_base_url = base_url()
    if server_is_ready(target_base_url):
        return ServerHandle(started_by_script=False, base_url=target_base_url)

    if not manage_server:
        raise RuntimeError(
            f"Could not connect to OpenCode at {target_base_url}. Start the server or omit --no-manage-server."
        )

    hostname, port = parse_host_port(target_base_url)
    log_path = output_dir / "opencode-server.log"
    log_file = log_path.open("w", encoding="utf-8")
    process = subprocess.Popen(
        [
            "uv",
            "run",
            "opencode",
            "serve",
            "--hostname",
            hostname,
            "--port",
            str(port),
        ],
        cwd=repo_root(),
        stdout=log_file,
        stderr=subprocess.STDOUT,
        text=True,
    )

    try:
        wait_for_server(target_base_url, timeout_seconds, process)
    except BaseException:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        log_file.close()
        details = ""
        if log_path.exists():
            log_text = log_path.read_text(encoding="utf-8")
            if log_text.strip():
                details = f" See {log_path} for server logs."
        raise RuntimeError(f"Failed during OpenCode server startup at {target_base_url}.{details}")

    return ServerHandle(
        started_by_script=True,
        base_url=target_base_url,
        process=process,
        log_path=log_path,
    )


def stop_server(handle: ServerHandle) -> None:
    if not handle.started_by_script or handle.process is None:
        return

    if handle.process.poll() is None:
        handle.process.terminate()
        try:
            handle.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            handle.process.kill()
            handle.process.wait(timeout=5)

    if handle.process.stdout is not None:
        handle.process.stdout.close()


def is_mcp_enabled(config: Any) -> bool:
    enabled = getattr(config, "enabled", None)
    return enabled is not False


def discover_mcp_server_name(config: Any) -> str:
    configured_name = os.environ.get("OPENCODE_MCP_SERVER")
    mcp_config = getattr(config, "mcp", None) or {}

    if configured_name:
        if configured_name in mcp_config:
            return configured_name
        available = ", ".join(sorted(mcp_config)) or "none"
        raise RuntimeError(
            f"OPENCODE_MCP_SERVER={configured_name!r} was not found in the active OpenCode config. "
            f"Available MCP servers: {available}."
        )

    enabled_servers = [name for name, entry in mcp_config.items() if is_mcp_enabled(entry)]
    redis_candidates = [name for name in enabled_servers if "redis" in name.lower()]
    if len(redis_candidates) == 1:
        return redis_candidates[0]
    if len(enabled_servers) == 1:
        return enabled_servers[0]
    if redis_candidates:
        return sorted(redis_candidates)[0]

    available = ", ".join(sorted(enabled_servers or list(mcp_config))) or "none"
    raise RuntimeError(
        "Could not automatically determine the Redis MCP server name from the active OpenCode config. "
        f"Available MCP servers: {available}. Set OPENCODE_MCP_SERVER explicitly if needed."
    )


def resolve_runtime_settings(model_override: str | None = None) -> tuple[str, str, str, str]:
    target_base_url = base_url()
    client = Opencode(base_url=target_base_url)
    try:
        providers = client.app.providers()
        config = client.config.get()
    except APIConnectionError as exc:
        raise RuntimeError(
            "Could not connect to OpenCode at "
            f"{target_base_url}. Start the OpenCode server or set OPENCODE_BASE_URL to the correct URL."
        ) from exc
    provider_id = os.environ.get("OPENCODE_PROVIDER_ID", "opencode")
    model_id = model_override or os.environ.get("OPENCODE_MODEL_ID", providers.default.get(provider_id))
    if not model_id:
        raise RuntimeError(f"No default model found for provider_id={provider_id!r}")

    mcp_server_name = discover_mcp_server_name(config)
    return target_base_url, provider_id, model_id, mcp_server_name


def build_client(model_override: str | None = None) -> tuple[Opencode, str, str, str]:
    target_base_url, provider_id, model_id, mcp_server_name = resolve_runtime_settings(model_override)
    client = Opencode(base_url=target_base_url)
    return client, provider_id, model_id, mcp_server_name


def _session_id_from_event(evt: object) -> str | None:
    props = getattr(evt, "properties", None)
    if props is None:
        return None
    return getattr(props, "session_id", None) or getattr(props, "sessionID", None)


def _describe_tool_event(evt: object, session_id: str) -> dict[str, str] | None:
    etype = getattr(evt, "type", None)
    if etype != "message.part.updated":
        return None

    props = getattr(evt, "properties", None)
    part = getattr(props, "part", None)
    if part is None:
        return None

    part_session_id = (
        getattr(part, "session_id", None)
        or getattr(part, "sessionID", None)
        or _session_id_from_event(evt)
    )
    if part_session_id != session_id:
        return None

    part_type = getattr(part, "type", None)
    if not part_type or "tool" not in part_type:
        return None

    tool_name = getattr(part, "tool", "?")
    call_id = getattr(part, "call_id", None) or getattr(part, "callID", None) or ""
    state = getattr(part, "state", None)
    status = getattr(state, "status", None) or "unknown"
    input_obj = getattr(state, "input", None) or {}

    input_summary = ""
    if isinstance(input_obj, dict):
        if "filePath" in input_obj:
            input_summary = f"filePath={input_obj['filePath']}"
        elif "command" in input_obj:
            input_summary = f"command={str(input_obj['command'])[:160]}"

    tool_label = f"{tool_name} {call_id}".strip()
    return {
        "tool": tool_label,
        "status": str(status),
        "input_summary": input_summary,
    }


def extract_response_text(message: Any) -> str:
    parts = getattr(message, "parts", []) or []
    chunks: list[str] = []
    for part in parts:
        if isinstance(part, dict) and part.get("type") == "text" and part.get("text"):
            chunks.append(part["text"])
    return "\n".join(chunks).strip()


def invoke_once(run_id: int, question: str, system_prompt: str, model_override: str | None = None) -> RunArtifact:
    client, provider_id, model_id, mcp_server_name = build_client(model_override)
    session = client.session.create(extra_body={})

    events_q: queue.Queue[object] = queue.Queue()
    stop_events = threading.Event()
    done = threading.Event()
    idle = threading.Event()
    result_holder: dict[str, Any] = {}
    tool_activity: list[dict[str, str]] = []

    def pump_events() -> None:
        try:
            for evt in client.event.list(extra_body={}):
                if stop_events.is_set():
                    break
                events_q.put(evt)
        except BaseException as exc:
            events_q.put(exc)

    def run_chat() -> None:
        try:
            result_holder["message"] = client.session.chat(
                session.id,
                provider_id=provider_id,
                model_id=model_id,
                system=system_prompt,
                parts=[{"type": "text", "text": question}],
                tools={f"{mcp_server_name}_*": True},
                extra_body={},
            )
        except BaseException as exc:
            result_holder["error"] = exc
        finally:
            done.set()

    threading.Thread(target=pump_events, daemon=True).start()
    threading.Thread(target=run_chat, daemon=True).start()

    while True:
        try:
            evt = events_q.get(timeout=1)
        except queue.Empty:
            if done.is_set() and (idle.is_set() or "error" in result_holder):
                break
            continue

        if isinstance(evt, BaseException):
            stop_events.set()
            raise evt

        tool_evt = _describe_tool_event(evt, session.id)
        if tool_evt:
            tool_activity.append(tool_evt)

        if getattr(evt, "type", None) == "session.idle" and _session_id_from_event(evt) == session.id:
            idle.set()

        if done.is_set() and idle.is_set():
            break

    stop_events.set()

    if "error" in result_holder:
        error = result_holder["error"]
        if isinstance(error, APITimeoutError):
            raise RuntimeError(
                f"Experiment run {run_id} timed out after server startup while waiting for OpenCode response."
            ) from error
        raise error

    message = result_holder["message"]
    return RunArtifact(
        run_id=run_id,
        session_id=session.id,
        response_text=extract_response_text(message),
        tool_activity=tool_activity,
        message=message.model_dump(mode="json"),
    )


def parse_threshold_value(raw_value: Any, field_name: str) -> float:
    if isinstance(raw_value, bool):
        raise ValueError(f"{field_name} must be a number or percentage string.")

    if isinstance(raw_value, (int, float)):
        value = float(raw_value)
        if 0.0 <= value <= 1.0:
            return value
        if 1.0 < value <= 100.0:
            return value / 100.0
        raise ValueError(f"{field_name} must be between 0 and 1, or 0 and 100.")

    if isinstance(raw_value, str):
        text = raw_value.strip()
        percent_match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*%", text)
        if percent_match:
            return float(percent_match.group(1)) / 100.0
        numeric_match = re.fullmatch(r"\d+(?:\.\d+)?", text)
        if numeric_match:
            return parse_threshold_value(float(text), field_name)

    raise ValueError(f"{field_name} must be a number or percentage string.")


def load_objective_config(path: Path) -> list[ObjectiveDefinition]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError("OBJECTIVE.yaml must contain a top-level mapping.")

    raw_objectives = loaded.get("objectives")
    if not isinstance(raw_objectives, list) or not raw_objectives:
        raise ValueError("OBJECTIVE.yaml must define a non-empty 'objectives' list.")

    objectives: list[ObjectiveDefinition] = []
    for index, raw_objective in enumerate(raw_objectives, start=1):
        if not isinstance(raw_objective, dict):
            raise ValueError("Each entry in OBJECTIVE.yaml 'objectives' must be a mapping.")

        description = str(raw_objective.get("description", "")).strip()
        if not description:
            raise ValueError(f"OBJECTIVE.yaml objective #{index} is missing a description.")

        objective_id = str(raw_objective.get("id", f"objective_{index}"))
        threshold = parse_threshold_value(raw_objective.get("threshold", 1.0), f"objectives[{index}].threshold")
        objectives.append(ObjectiveDefinition(id=objective_id, description=description, threshold=threshold))

    return objectives


def assess_criterion(
    objective: ObjectiveDefinition,
    response_text: str,
    tool_activity: list[dict[str, str]],
    mcp_server_name: str,
) -> CriterionAssessment:
    criterion = objective.description
    criterion_lower = criterion.lower()
    response_lower = response_text.lower()
    tool_names = [item["tool"].lower() for item in tool_activity]
    tool_base_names = [item["tool"].split()[0].lower() for item in tool_activity]

    if "redis" in criterion_lower and "access" in criterion_lower:
        mcp_prefix = f"{mcp_server_name.lower()}_"
        canonical_redis_mcp_tools = {
            "ft_info",
            "redis_execute",
            "slowlog_get",
        }
        matching_tools = [
            tool_name
            for tool_name in tool_base_names
            if tool_name.startswith(mcp_prefix) or tool_name in canonical_redis_mcp_tools
        ]
        satisfied = bool(matching_tools)
        if matching_tools:
            evidence = f"Matched Redis MCP tool activity: {', '.join(sorted(set(matching_tools)))}"
        else:
            evidence = (
                "No Redis MCP tool usage found. Redis access through bash/read/other non-MCP tools "
                "does not satisfy this criterion."
            )
        return CriterionAssessment(criterion=criterion, satisfied=satisfied, evidence=evidence)

    tokens = {
        token.lower()
        for token in re.findall(r"[A-Za-z][A-Za-z0-9_]+", criterion)
        if token.lower() not in STOPWORDS
    }
    matched_tokens = sorted(token for token in tokens if token in response_lower)
    token_ratio = len(matched_tokens) / max(len(tokens), 1)
    satisfied = token_ratio >= 0.6
    if matched_tokens:
        evidence = f"Matched criterion keywords: {', '.join(matched_tokens)}"
    else:
        evidence = "No meaningful keyword overlap found in response text."
    return CriterionAssessment(criterion=criterion, satisfied=satisfied, evidence=evidence)


def save_run_artifact(output_dir: Path, artifact: RunArtifact, question: str) -> None:
    payload = {
        "run_id": artifact.run_id,
        "question": question,
        "session_id": artifact.session_id,
        "response_text": artifact.response_text,
        "tool_activity": artifact.tool_activity,
        "message": artifact.message,
    }
    json_path = output_dir / f"run-{artifact.run_id}.json"
    md_path = output_dir / f"run-{artifact.run_id}.md"
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    md_lines = [artifact.response_text.rstrip(), "", "## Tool Activity", ""]
    if artifact.tool_activity:
        tool_names: list[str] = []
        seen: set[str] = set()
        for item in artifact.tool_activity:
            tool_name = item["tool"].split()[0]
            if tool_name not in seen:
                seen.add(tool_name)
                tool_names.append(tool_name)

        for tool_name in tool_names:
            md_lines.append(f"- `{tool_name}`")
    else:
        md_lines.append("- No tool activity captured.")

    md_path.write_text("\n".join(md_lines).rstrip() + "\n", encoding="utf-8")


def print_saved_run_outputs(output_dir: Path) -> None:
    for run_file in sorted(output_dir.glob("run-*.md")):
        content = run_file.read_text(encoding="utf-8").rstrip()
        console.print()
        console.print(
            Panel(
                Markdown(content or "_(empty response)_"),
                title=run_file.name,
                border_style="green",
            )
        )


def print_agents_file(attempt_dir: Path, instructions_name: str) -> None:
    instructions_path = attempt_dir / instructions_name
    if not instructions_path.exists():
        return

    content = instructions_path.read_text(encoding="utf-8").rstrip()
    console.print()
    console.print(
        Panel(
            Markdown(content or "_(empty instructions)_"),
            title=f"{instructions_name} - {attempt_dir.name}",
            border_style="yellow",
        )
    )


def print_evaluation_summary(
    evaluation: dict[str, Any],
    output_dir: Path,
    server_handle: ServerHandle,
    provider_id: str,
    model_id: str,
    mcp_server_name: str,
    label: str,
) -> None:
    table = Table(title=f"Experiment Summary - {label}", header_style="bold cyan")
    table.add_column("run")
    table.add_column("session")
    table.add_column("passed")

    for result in evaluation["run_results"]:
        table.add_row(
            str(result["run_id"]),
            result["session_id"],
            "yes" if result["passed_all_criteria"] else "no",
        )

    console.print(table)
    console.print(f"Saved artifacts to: [bold]{output_dir}[/bold]")
    console.print(f"Server started by script: [bold]{server_handle.started_by_script}[/bold]")
    console.print(f"Provider: [bold]{provider_id}[/bold]")
    console.print(f"Model: [bold]{model_id}[/bold]")
    console.print(f"Redis MCP server: [bold]{mcp_server_name}[/bold]")
    console.print(f"Passing runs: [bold]{evaluation['passing_runs']}/{evaluation['total_runs']}[/bold]")
    console.print(f"Success rate: [bold]{evaluation['success_rate']:.0%}[/bold]")
    console.print(f"Threshold satisfied: [bold]{evaluation['threshold_satisfied']}[/bold]")
    for objective in evaluation.get("objective_results", []):
        console.print(
            "Objective "
            f"[bold]{objective['id']}[/bold]: "
            f"{objective['success_rate']:.0%} "
            f"({objective['passing_runs']}/{objective['total_runs']} runs), "
            f"threshold {objective['threshold']:.0%}, passed={objective['threshold_satisfied']}"
        )


def run_parallel_experiments(
    question: str,
    instructions_text: str,
    model_override: str | None,
    run_count: int,
) -> list[RunArtifact]:
    artifacts: list[RunArtifact] = []
    with ThreadPoolExecutor(max_workers=run_count) as executor:
        future_to_run_id = {
            executor.submit(invoke_once, run_id, question, instructions_text, model_override): run_id
            for run_id in range(1, run_count + 1)
        }
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("{task.completed}/{task.total}"),
            TimeElapsedColumn(),
            console=console,
        ) as progress:
            task_id = progress.add_task("Running OpenCode experiments", total=run_count)
            for future in as_completed(future_to_run_id):
                run_id = future_to_run_id[future]
                try:
                    artifact = future.result()
                except Exception as exc:
                    message = str(exc).lower()
                    if "timeout" in message:
                        raise RuntimeError(
                            f"Experiment run {run_id} timed out after server startup."
                        ) from exc
                    raise RuntimeError(f"Experiment run {run_id} failed: {exc}") from exc
                artifacts.append(artifact)
                progress.update(task_id, advance=1)

    return artifacts


def sanitize_agents_text(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    return stripped + "\n"


def build_optimizer_artifact_bundle(attempt_dir: Path, instructions_name: str) -> str:
    bundle_paths = [
        attempt_dir / instructions_name,
        attempt_dir / "OBJECTIVE.yaml",
        attempt_dir / "request.json",
        attempt_dir / "evaluation.json",
    ]
    bundle_paths.extend(sorted(attempt_dir.rglob("run-*.md")))
    bundle_paths.extend(sorted(attempt_dir.rglob("run-*.json")))

    sections: list[str] = []
    for path in bundle_paths:
        if not path.exists():
            continue
        sections.append(f"=== {path.relative_to(attempt_dir)} ===\n{path.read_text(encoding='utf-8').strip()}")
    return "\n\n".join(sections)


def propose_updated_instructions(
    attempt_dir: Path,
    instructions_name: str,
    model_override: str | None,
) -> str:
    artifact_bundle = build_optimizer_artifact_bundle(attempt_dir, instructions_name)
    optimizer_prompt = (
        "You are revising an AGENTS.md file for a reusable experiment harness. "
        "Infer what the runtime agent must do by reading the previous attempt artifacts. "
        "The goal is to make the next attempt satisfy OBJECTIVE.yaml.\n\n"
        "Requirements:\n"
        "- Base the revision on evidence from the artifact files.\n"
        "- Keep the file reusable; do not add unnecessary hardcoded assumptions beyond what the artifacts prove is required.\n"
        "- Preserve useful existing instructions unless they conflict with the objective.\n"
        "- Strengthen the instructions so the next run is more likely to satisfy the failed criteria.\n"
        "- Return only the full revised AGENTS.md content. No code fences. No explanation.\n\n"
        f"Previous attempt artifacts:\n\n{artifact_bundle}"
    )

    client, provider_id, model_id, _ = build_client(model_override)
    session = client.session.create(extra_body={})
    message = client.session.chat(
        session.id,
        provider_id=provider_id,
        model_id=model_id,
        system=(
            "You rewrite AGENTS.md files based on experiment evidence. "
            "Output only the final AGENTS.md content."
        ),
        parts=[{"type": "text", "text": optimizer_prompt}],
        tools={},
        extra_body={},
    )
    revised = sanitize_agents_text(extract_response_text(message))
    if not revised.strip():
        raise RuntimeError("AGENTS optimization returned empty content.")
    return revised


def print_attempt_header(attempt_number: int, max_tries: int) -> None:
    console.rule(f"Attempt {attempt_number}/{max_tries}")


def format_improvement(delta: float, *, scale: float = 100.0, suffix: str = "%") -> str:
    amount = delta * scale
    return f"{amount:+.0f}{suffix}"


def build_final_summary_markdown(
    final_evaluation: dict[str, Any],
    attempt_summaries: list[dict[str, Any]],
    instructions_name: str,
    final_instructions_text: str,
) -> str:
    passing_questions = final_evaluation["passing_questions"]
    total_questions = final_evaluation["total_questions"]
    success_rate = final_evaluation["success_rate"]

    lines = [
        "## Final Summary",
        "",
        f"- Questions passing all thresholds: **{passing_questions}/{total_questions}** ({success_rate:.0%})",
        "- The instructions block below is display-only; this summary does not write to the live AGENTS file.",
        "",
        "### Improvement By Iteration",
        "",
    ]

    previous_passing_questions: int | None = None
    previous_success_rate: float | None = None
    for summary in attempt_summaries:
        attempt_number = summary["attempt_number"]
        current_passing_questions = summary["passing_questions"]
        current_total_questions = summary["total_questions"]
        current_success_rate = summary["success_rate"]

        if previous_passing_questions is None or previous_success_rate is None:
            improvement_text = "baseline"
        else:
            question_delta = current_passing_questions - previous_passing_questions
            rate_delta = current_success_rate - previous_success_rate
            improvement_text = (
                f"{question_delta:+d} question(s), {format_improvement(rate_delta, suffix=' pts')}"
            )

        lines.append(
            "- "
            f"Attempt {attempt_number}: {current_passing_questions}/{current_total_questions} questions "
            f"passed ({current_success_rate:.0%}); change vs previous: {improvement_text}"
        )
        previous_passing_questions = current_passing_questions
        previous_success_rate = current_success_rate

    lines.extend(
        [
            "",
            f"### Displayed {instructions_name} Snapshot",
            "",
            "```md",
            final_instructions_text.rstrip(),
            "```",
        ]
    )
    return "\n".join(lines)


def print_final_summary(
    final_evaluation: dict[str, Any],
    attempt_summaries: list[dict[str, Any]],
    instructions_name: str,
    final_instructions_text: str,
) -> None:
    summary_markdown = build_final_summary_markdown(
        final_evaluation,
        attempt_summaries,
        instructions_name,
        final_instructions_text,
    )
    console.print()
    console.print(
        Panel(
            Markdown(summary_markdown),
            title="Final Summary",
            border_style="cyan",
        )
    )


def evaluate_saved_runs(
    output_dir: Path,
    criteria: list[ObjectiveDefinition],
    mcp_server_name: str,
) -> dict[str, Any]:
    run_results: list[dict[str, Any]] = []
    objective_pass_counts = {criterion.id: 0 for criterion in criteria}
    for run_file in sorted(output_dir.glob("run-*.json")):
        payload = json.loads(run_file.read_text(encoding="utf-8"))
        assessments = [
            asdict(
                assess_criterion(
                    criterion,
                    payload["response_text"],
                    payload.get("tool_activity", []),
                    mcp_server_name,
                )
            )
            for criterion in criteria
        ]
        for criterion, assessment in zip(criteria, assessments):
            if assessment["satisfied"]:
                objective_pass_counts[criterion.id] += 1
        run_passed = all(item["satisfied"] for item in assessments)
        run_results.append(
            {
                "run_id": payload["run_id"],
                "session_id": payload["session_id"],
                "passed_all_criteria": run_passed,
                "criteria": assessments,
            }
        )

    passing_runs = sum(1 for result in run_results if result["passed_all_criteria"])
    total_runs = len(run_results)
    success_rate = passing_runs / total_runs if total_runs else 0.0

    objective_results = []
    for criterion in criteria:
        objective_passing_runs = objective_pass_counts[criterion.id]
        objective_success_rate = objective_passing_runs / total_runs if total_runs else 0.0
        objective_results.append(
            {
                "id": criterion.id,
                "description": criterion.description,
                "threshold": criterion.threshold,
                "passing_runs": objective_passing_runs,
                "total_runs": total_runs,
                "success_rate": objective_success_rate,
                "threshold_satisfied": objective_success_rate >= criterion.threshold,
            }
        )

    objectives_satisfied = all(item["threshold_satisfied"] for item in objective_results)
    return {
        "total_runs": total_runs,
        "passing_runs": passing_runs,
        "success_rate": success_rate,
        "objective_results": objective_results,
        "objectives_satisfied": objectives_satisfied,
        "threshold_satisfied": objectives_satisfied,
        "run_results": run_results,
    }


def default_output_dir() -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return repo_root() / "artifacts" / timestamp


def question_output_dir(root_output_dir: Path, question_index: int) -> Path:
    return root_output_dir / f"question-{question_index:02d}"


def summarize_question_results(question_results: list[dict[str, Any]]) -> dict[str, Any]:
    passing_questions = sum(1 for result in question_results if result["threshold_satisfied"])
    total_questions = len(question_results)
    success_rate = passing_questions / total_questions if total_questions else 0.0
    return {
        "total_questions": total_questions,
        "passing_questions": passing_questions,
        "success_rate": success_rate,
        "threshold_satisfied": passing_questions == total_questions,
        "question_results": question_results,
    }


def run_question_experiment(
    question: str,
    question_index: int,
    attempt_dir: Path,
    instructions_text: str,
    instructions_path: Path,
    objective_path: Path,
    objective_text: str,
    criteria: list[ObjectiveDefinition],
    provider_id: str,
    model_id: str,
    mcp_server_name: str,
    server_handle: ServerHandle,
    model_override: str | None,
    run_count: int,
) -> dict[str, Any]:
    question_dir = question_output_dir(attempt_dir, question_index)
    question_dir.mkdir(parents=True, exist_ok=True)

    (question_dir / "request.json").write_text(
        json.dumps(
            {
                "question": question,
                "question_index": question_index,
                "attempt_dir": attempt_dir.name,
                "run_count": run_count,
                "model_override": model_override,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    (question_dir / instructions_path.name).write_text(instructions_text, encoding="utf-8")
    (question_dir / objective_path.name).write_text(objective_text, encoding="utf-8")

    artifacts = run_parallel_experiments(question, instructions_text, model_override, run_count)

    for artifact in sorted(artifacts, key=lambda item: item.run_id):
        save_run_artifact(question_dir, artifact, question)

    evaluation = evaluate_saved_runs(question_dir, criteria, mcp_server_name)
    (question_dir / "evaluation.json").write_text(
        json.dumps(evaluation, indent=2),
        encoding="utf-8",
    )

    print_saved_run_outputs(question_dir)
    print_evaluation_summary(
        evaluation,
        question_dir,
        server_handle,
        provider_id,
        model_id,
        mcp_server_name,
        f"Attempt {attempt_dir.name} / Question {question_index}",
    )

    return {
        "question_index": question_index,
        "question": question,
        "question_dir": str(question_dir),
        "threshold_satisfied": evaluation["threshold_satisfied"],
        "evaluation": evaluation,
    }


def main() -> int:
    args = parse_args()
    if args.setup:
        return run_setup()

    if args.max_tries < 1:
        raise SystemExit("--max-tries must be at least 1.")

    questions = load_questions()
    config = load_repo_config()
    run_count = resolve_run_count(config)

    instructions_path = resolve_markdown_file("AGENT.md", "AGENTS.md")
    objective_path = repo_root() / "OBJECTIVE.yaml"
    if not objective_path.exists():
        raise FileNotFoundError("OBJECTIVE.yaml was not found.")

    base_instructions = read_text(instructions_path)
    objective_text = read_text(objective_path)
    criteria = load_objective_config(objective_path)

    output_dir = Path(args.output_dir) if args.output_dir else default_output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / f"original-{instructions_path.name}").write_text(base_instructions, encoding="utf-8")
    (output_dir / objective_path.name).write_text(objective_text, encoding="utf-8")
    (output_dir / "request.json").write_text(
        json.dumps(
            {
                "questions": serialize_questions(questions),
                "run_count": run_count,
                "model_override": args.model,
                "max_tries": args.max_tries,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    server_handle = start_server_if_needed(
        output_dir=output_dir,
        timeout_seconds=args.server_start_timeout,
        manage_server=not args.no_manage_server,
    )
    runtime_base_url, provider_id, model_id, mcp_server_name = resolve_runtime_settings(args.model)
    (output_dir / "server.json").write_text(
        json.dumps(
            {
                "base_url": runtime_base_url,
                "started_by_script": server_handle.started_by_script,
                "log_path": str(server_handle.log_path) if server_handle.log_path else None,
                "provider_id": provider_id,
                "model_id": model_id,
                "mcp_server_name": mcp_server_name,
                "max_tries": args.max_tries,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    attempt_summaries: list[dict[str, Any]] = []
    final_attempt_dir: Path | None = None
    final_evaluation: dict[str, Any] | None = None
    final_instructions_text = base_instructions if base_instructions.endswith("\n") else base_instructions + "\n"

    try:
        for attempt_number in range(1, args.max_tries + 1):
            attempt_dir = output_dir / f"attempt-{attempt_number}"
            attempt_dir.mkdir(parents=True, exist_ok=True)

            if attempt_number == 1:
                instructions_text = final_instructions_text
            else:
                previous_attempt_dir = output_dir / f"attempt-{attempt_number - 1}"
                console.print(
                    f"Optimizing [bold]{instructions_path.name}[/bold] from [bold]{previous_attempt_dir}[/bold]"
                )
                instructions_text = propose_updated_instructions(
                    previous_attempt_dir,
                    instructions_path.name,
                    args.model,
                )

            final_instructions_text = instructions_text

            instructions_path.write_text(instructions_text, encoding="utf-8")
            (attempt_dir / instructions_path.name).write_text(instructions_text, encoding="utf-8")
            (attempt_dir / objective_path.name).write_text(objective_text, encoding="utf-8")
            (attempt_dir / "request.json").write_text(
                json.dumps(
                    {
                        "questions": serialize_questions(questions),
                        "run_count": run_count,
                        "attempt_number": attempt_number,
                        "max_tries": args.max_tries,
                        "model_override": args.model,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            print_attempt_header(attempt_number, args.max_tries)
            print_agents_file(attempt_dir, instructions_path.name)
            question_results: list[dict[str, Any]] = []
            for question_index, question in enumerate(questions, start=1):
                console.rule(f"Question {question_index}/{len(questions)}")
                question_result = run_question_experiment(
                    question=question,
                    question_index=question_index,
                    attempt_dir=attempt_dir,
                    instructions_text=instructions_text,
                    instructions_path=instructions_path,
                    objective_path=objective_path,
                    objective_text=objective_text,
                    criteria=criteria,
                    provider_id=provider_id,
                    model_id=model_id,
                    mcp_server_name=mcp_server_name,
                    server_handle=server_handle,
                    model_override=args.model,
                    run_count=run_count,
                )
                question_results.append(question_result)

            evaluation = summarize_question_results(question_results)
            (attempt_dir / "evaluation.json").write_text(
                json.dumps(evaluation, indent=2),
                encoding="utf-8",
            )

            attempt_summaries.append(
                {
                    "attempt_number": attempt_number,
                    "instructions_file": str(attempt_dir / instructions_path.name),
                    "success_rate": evaluation["success_rate"],
                    "threshold_satisfied": evaluation["threshold_satisfied"],
                    "passing_questions": evaluation["passing_questions"],
                    "total_questions": evaluation["total_questions"],
                }
            )
            (output_dir / "attempts.json").write_text(
                json.dumps(attempt_summaries, indent=2),
                encoding="utf-8",
            )

            console.print(f"Attempt success rate: [bold]{evaluation['success_rate']:.0%}[/bold]")
            console.print(f"Passing questions: [bold]{evaluation['passing_questions']}/{evaluation['total_questions']}[/bold]")
            console.print(f"Threshold satisfied: [bold]{evaluation['threshold_satisfied']}[/bold]")

            final_attempt_dir = attempt_dir
            final_evaluation = evaluation
            if evaluation["threshold_satisfied"]:
                break

        if final_attempt_dir is None or final_evaluation is None:
            raise RuntimeError("No experiment attempts were executed.")

        (output_dir / "final-evaluation.json").write_text(
            json.dumps(final_evaluation, indent=2),
            encoding="utf-8",
        )
        (output_dir / f"final-{instructions_path.name}").write_text(
            final_instructions_text,
            encoding="utf-8",
        )
    finally:
        stop_server(server_handle)

    console.print(f"Final attempt directory: [bold]{final_attempt_dir}[/bold]")
    console.print(f"Finished {len(questions)} question(s).")
    print_final_summary(
        final_evaluation,
        attempt_summaries,
        instructions_path.name,
        final_instructions_text,
    )
    return 0 if final_evaluation["threshold_satisfied"] else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc
