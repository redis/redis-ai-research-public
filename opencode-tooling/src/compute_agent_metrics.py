import argparse
import json
import os
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional

DEFAULT_BUILTIN_TOOL_TYPES = {
    "bash",
    "edit",
    "glob",
    "grep",
    "list",
    "patch",
    "read",
    "search",
    "task",
    "write",
}
DEFAULT_SOURCE_FILE_PATTERN = r"([^/\s]+\.(?:txt|json))\b"
SOURCE_FILE_EDGE_CHARS = " \t\n\r\"'`.,:;()[]{}<>"
PATH_MENTION_RE = re.compile(
    r"(?:(?:/|\.{1,2}/)[^\s\"'`]+|[A-Za-z0-9_.-]+\.(?:txt|json|md|py|yaml|yml|csv))"
)
TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{3,}")
FAILURE_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r"\bi can(?:not|'t)\b",
        r"\bunable to\b",
        r"\bcan't analyze yet\b",
        r"\bcan't answer\b",
        r"\bplease (?:provide|paste|upload|share|tell me)\b",
        r"\bdoes(?: not|n't) exist\b",
        r"\bisn't present\b",
        r"\bnot present\b",
        r"\bmissing\b",
    ]
]
TOOL_ERROR_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r"\berror\b",
        r"\bexception\b",
        r"\btraceback\b",
        r"command not found",
        r"no such file or directory",
        r"permission denied",
        r"\bfailed\b",
    ]
]
INVALID_COMMAND_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r"command not found",
        r"\bsyntax error\b",
        r"\binvalid option\b",
        r"\busage:",
        r"\bunknown option\b",
        r"\bno module named\b",
        r"\btraceback\b",
    ]
]
DEAD_END_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r"no files found",
        r"no matches found",
        r"\b0 matches\b",
        r"\bnot found\b",
        r"\bempty\b",
    ]
]
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOCAL_LOGS_ROOT = PROJECT_ROOT / "local_logs"


def _build_parser() -> argparse.ArgumentParser:
    """Create the CLI parser for the metrics script."""
    parser = argparse.ArgumentParser(
        description="Compute aggregated agent metrics from Opik traces or exported session turns."
    )
    parser.add_argument(
        "--opik-key",
        default=os.environ.get("OPIK_API_KEY"),
        help="Opik API key. Defaults to the OPIK_API_KEY environment variable.",
    )
    parser.add_argument(
        "--opik-workspace",
        help="Opik workspace name.",
    )
    parser.add_argument(
        "--opik-project",
        help="Opik project name.",
    )
    parser.add_argument(
        "--metrics-dir",
        type=Path,
        default=None,
        help="Directory where metrics artifacts will be written. Defaults to metrics/<project>.",
    )
    parser.add_argument(
        "--builtin-tool-type",
        action="append",
        default=[],
        help=(
            "Repeatable builtin tool type. Tool types not in the builtin set are counted "
            "as custom tools."
        ),
    )
    parser.add_argument(
        "--source-file-regex",
        default=DEFAULT_SOURCE_FILE_PATTERN,
        help="Regex used to extract the source filename from a turn prompt or trace input text.",
    )
    parser.add_argument(
        "--read-local-logs",
        action="store_true",
        help="Read turn data from local session exports instead of Opik.",
    )
    parser.add_argument(
        "--local-logs-root",
        type=Path,
        default=DEFAULT_LOCAL_LOGS_ROOT,
        help="Root directory containing local session exports.",
    )
    parser.add_argument(
        "--trajectory-file",
        type=Path,
        help="Read turn data from one exported trajectory JSON file instead of Opik or local project logs.",
    )
    return parser


def _validate_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    """Validate required CLI arguments."""
    if args.trajectory_file and args.read_local_logs:
        parser.error("--trajectory-file cannot be combined with --read-local-logs.")

    if args.trajectory_file is None and not args.opik_project:
        parser.error("Provide --opik-project unless --trajectory-file is used.")

    if args.read_local_logs and not args.trajectory_file:
        if not args.opik_workspace or not args.opik_project:
            parser.error(
                "Provide --opik-workspace and --opik-project when using --read-local-logs."
            )

    if not args.read_local_logs and args.trajectory_file is None:
        if not args.opik_workspace or not args.opik_project:
            parser.error("Provide --opik-workspace and --opik-project when reading from Opik.")
    if not args.read_local_logs and args.trajectory_file is None and not args.opik_key:
        parser.error(
            "Provide an Opik API key with --opik-key or set the OPIK_API_KEY environment variable."
        )

    try:
        re.compile(args.source_file_regex)
    except re.error as exc:
        parser.error(f"Invalid --source-file-regex value: {exc}")


def _sanitize_path_component(value: str) -> str:
    """Return a filesystem-safe directory name."""
    sanitized = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip())
    return sanitized.strip("-") or "default"


def _local_project_dir(root: Path, workspace: str, project: str) -> Path:
    """Return the local directory used for a workspace/project export backend."""
    return root / _sanitize_path_component(workspace) / _sanitize_path_component(project)


def _get_trace_value(trace: Any, key: str, default: Any = None) -> Any:
    """Return a field from either an Opik object or a local dict trace."""
    if isinstance(trace, Mapping):
        return trace.get(key, default)
    return getattr(trace, key, default)


def _get_builtin_tool_types(extra_builtin_tool_types: Iterable[str]) -> set[str]:
    """Return the builtin tool type set used to classify custom tools."""
    builtin_tool_types = set(DEFAULT_BUILTIN_TOOL_TYPES)
    builtin_tool_types.update(
        tool_type.strip() for tool_type in extra_builtin_tool_types if tool_type.strip()
    )
    return builtin_tool_types


def _extract_source_file(input_text: str, source_file_pattern: re.Pattern[str]) -> str:
    """Extract the source filename from turn input text."""
    source_file_match = source_file_pattern.search(input_text)
    if source_file_match:
        source_file = source_file_match.group(1).strip(SOURCE_FILE_EDGE_CHARS)
        if source_file:
            return source_file
    return "unknown"


def _extract_trace_usage(trace: Any) -> Dict[str, float]:
    """Extract stable usage fields from an Opik trace."""
    trace_usage = _get_trace_value(trace, "usage", {}) or {}
    usage_statistics = {"duration": _get_trace_value(trace, "duration", 0) or 0}

    usage_key_map = {
        "original_usage.total_tokens": "total_tokens",
        "original_usage.cache_read_tokens": "cache_read_tokens",
    }
    for old_key, new_key in usage_key_map.items():
        value = trace_usage.get(old_key)
        if value is not None:
            usage_statistics[new_key] = value

    return usage_statistics


def _normalize_span(span: Any) -> Dict[str, Any]:
    """Extract the subset of span fields used by this script."""
    if isinstance(span, Mapping):
        span_data = dict(span)
    else:
        span_data = span.dict()
    normalized_span = {
        "id": span_data.get("id"),
        "name": span_data.get("name"),
        "parent_span_id": span_data.get("parent_span_id"),
        "type": span_data.get("type"),
        "start_time": span_data.get("start_time"),
        "end_time": span_data.get("end_time"),
        "input": span_data.get("input") or {},
        "output": span_data.get("output") or {},
        "tags": span_data.get("tags"),
        "metrics": ((span_data.get("metadata") or {}).get("metrics") or {}),
    }
    return normalized_span


def _get_tool_type(span_dict: Mapping[str, Any]) -> str:
    """Safely read a tool type from a normalized span dictionary."""
    span_input = span_dict.get("input")
    if not isinstance(span_input, Mapping):
        return ""
    tool_type = span_input.get("tool_type")
    if isinstance(tool_type, str):
        return tool_type
    return ""


def _get_bash_type(span_dict: Mapping[str, Any]) -> str:
    """Extract the shell command name from a bash tool span."""
    span_input = span_dict.get("input")
    if not isinstance(span_input, Mapping):
        return "<empty>"
    command = span_input.get("command")
    if not isinstance(command, str) or not command.strip():
        return "<empty>"
    return command.split()[0]


def _get_output_path(span_dict: Mapping[str, Any]) -> Optional[str]:
    """Return a tool output path when the span output is dictionary-like."""
    span_output = span_dict.get("output")
    if not isinstance(span_output, Mapping):
        return None

    output_path = span_output.get("outputPath")
    if isinstance(output_path, str):
        return output_path
    return None


def _get_text_output(value: Any) -> str:
    """Return a best-effort text representation for Opik output payloads."""
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        text_value = value.get("text")
        if isinstance(text_value, str):
            return text_value
        if "error" in value and isinstance(value["error"], str):
            return value["error"]
        try:
            return json.dumps(value, sort_keys=True)
        except TypeError:
            return str(value)
    if value is None:
        return ""
    return str(value)


def _normalize_text(text: str) -> str:
    """Normalize free-form text for token comparisons."""
    return re.sub(r"\s+", " ", text).strip().lower()


def _extract_tokens(text: str) -> set[str]:
    """Extract a set of normalized lexical tokens from text."""
    return {match.group(0).lower() for match in TOKEN_RE.finditer(text)}


def _normalize_path_mention(path_text: str) -> str:
    """Normalize a path/file mention for comparison."""
    return path_text.strip(SOURCE_FILE_EDGE_CHARS)


def _path_variants(path_text: str) -> set[str]:
    """Return useful comparison variants for a path or filename."""
    normalized_path = _normalize_path_mention(path_text)
    if not normalized_path:
        return set()

    variants = {normalized_path.lower()}
    basename = normalized_path.split("/")[-1]
    if basename:
        variants.add(basename.lower())
    return variants


def _extract_path_mentions(text: str) -> List[str]:
    """Extract path-like mentions from text."""
    mentions = []
    for match in PATH_MENTION_RE.finditer(text):
        mention = _normalize_path_mention(match.group(0))
        if mention:
            mentions.append(mention)
    return mentions


def _get_trace_output_text(trace: Any) -> str:
    """Extract final answer text from an Opik trace."""
    return _get_text_output(_get_trace_value(trace, "output", {}) or {})


def _is_reasoning_span(span_dict: Mapping[str, Any]) -> bool:
    """Check whether a span is a reasoning span."""
    if span_dict.get("type") != "llm":
        return False
    tags = span_dict.get("tags") or []
    return isinstance(tags, list) and "reasoning" in tags


def _is_tool_error(span_dict: Mapping[str, Any]) -> bool:
    """Heuristically classify a tool span as an error."""
    span_output = span_dict.get("output")
    if isinstance(span_output, Mapping):
        if span_output.get("error"):
            return True
        status_value = span_output.get("status")
        if isinstance(status_value, str) and status_value.lower() in {"error", "failed"}:
            return True

    output_text = _get_text_output(span_output)
    for pattern in TOOL_ERROR_PATTERNS:
        if pattern.search(output_text):
            return True
    return False


def _is_dead_end_tool(span_dict: Mapping[str, Any]) -> bool:
    """Heuristically classify a tool span as a dead end."""
    if _is_tool_error(span_dict):
        return True

    tool_type = _get_tool_type(span_dict)
    span_output = span_dict.get("output")
    output_text = _get_text_output(span_output)
    normalized_output_text = _normalize_text(output_text)

    if tool_type in {"glob", "grep", "search"}:
        if isinstance(span_output, Mapping):
            count_value = span_output.get("count")
            matches_value = span_output.get("matches")
            if count_value == 0 or matches_value == 0:
                return True
        return any(pattern.search(normalized_output_text) for pattern in DEAD_END_PATTERNS)

    if tool_type == "read":
        return normalized_output_text == ""

    if tool_type == "task":
        return normalized_output_text == ""

    return normalized_output_text == ""


def _is_invalid_command(span_dict: Mapping[str, Any]) -> bool:
    """Heuristically classify a bash span as an invalid command."""
    span_input = span_dict.get("input")
    if not isinstance(span_input, Mapping):
        return True

    command = span_input.get("command")
    if not isinstance(command, str) or not command.strip():
        return True

    output_text = _get_text_output(span_dict.get("output"))
    return any(pattern.search(output_text) for pattern in INVALID_COMMAND_PATTERNS)


def _get_action_target(span_dict: Mapping[str, Any]) -> str:
    """Return a normalized action target string for trajectory metrics."""
    tool_type = _get_tool_type(span_dict)
    span_input = span_dict.get("input")
    if not isinstance(span_input, Mapping):
        return tool_type or "<unknown>"

    if tool_type == "read":
        file_path = span_input.get("filePath", "<unknown>")
        return f"read:{_normalize_path_mention(str(file_path))}"
    if tool_type == "glob":
        return f"glob:{span_input.get('pattern', '<unknown>')}"
    if tool_type == "grep":
        return f"grep:{span_input.get('pattern', '<unknown>')}"
    if tool_type == "bash":
        command = span_input.get("command", "<empty>")
        return f"bash:{str(command).strip()}"
    if tool_type == "task":
        return f"task:{span_input.get('subagent_type', '<unknown>')}"
    return f"tool:{tool_type}"


def _collect_supported_paths(
    source_file: str, input_text: str, normalized_spans: List[Dict[str, Any]]
) -> set[str]:
    """Collect supported path mentions for hallucination checks."""
    supported_paths = set()
    supported_paths.update(_path_variants(source_file))
    for mention in _extract_path_mentions(input_text):
        supported_paths.update(_path_variants(mention))

    for span_dict in normalized_spans:
        if span_dict.get("type") != "tool":
            continue

        span_input = span_dict.get("input")
        if isinstance(span_input, Mapping):
            for key in ["filePath", "path"]:
                value = span_input.get(key)
                if isinstance(value, str):
                    supported_paths.update(_path_variants(value))

        output_path = _get_output_path(span_dict)
        if output_path:
            supported_paths.update(_path_variants(output_path))

        output_text = _get_text_output(span_dict.get("output"))
        for mention in _extract_path_mentions(output_text):
            supported_paths.update(_path_variants(mention))

    return supported_paths


def _compute_final_answer_specificity(
    final_answer_text: str, source_file: str, evidence_tokens: set[str]
) -> float:
    """Compute a rough specificity score for the final answer on a 0-1 scale."""
    if not final_answer_text.strip():
        return 0.0

    score = 0.0
    normalized_text = _normalize_text(final_answer_text)
    answer_tokens = _extract_tokens(normalized_text)
    overlap_tokens = answer_tokens.intersection(evidence_tokens)

    if len(final_answer_text) >= 80:
        score += 0.15
    if len(final_answer_text) >= 200:
        score += 0.1
    if source_file.lower() in normalized_text or _extract_path_mentions(final_answer_text):
        score += 0.2
    if len(re.findall(r"\d+", final_answer_text)) >= 1:
        score += 0.15
    if len(re.findall(r"\d+", final_answer_text)) >= 3:
        score += 0.1
    if "\n-" in final_answer_text or "\n1." in final_answer_text or final_answer_text.count(":") >= 2:
        score += 0.15
    if len(overlap_tokens) >= 3:
        score += 0.15
    if len(overlap_tokens) >= 6:
        score += 0.1

    return round(min(score, 1.0), 4)


def _is_first_pass_success(final_answer_text: str, grounded: bool, specificity: float) -> bool:
    """Heuristically determine whether the turn succeeded on the first pass."""
    normalized_text = _normalize_text(final_answer_text)
    if not normalized_text:
        return False
    if any(pattern.search(normalized_text) for pattern in FAILURE_PATTERNS):
        return False
    return grounded or specificity >= 0.45


def _compute_trace_metrics(
    trace: Any,
    source_file: str,
    input_text: str,
    normalized_spans: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Compute trajectory-style metrics for a single turn."""
    final_answer_text = _get_trace_output_text(trace)
    evidence_text_parts = [source_file, input_text]
    action_targets: List[str] = []
    read_targets: List[str] = []
    reasoning_count = 0
    tool_count = 0
    tool_error_count = 0
    dead_end_count = 0
    bash_count = 0
    python_bash_count = 0
    subagent_count = 0
    invalid_command_count = 0

    for span_dict in normalized_spans:
        if _is_reasoning_span(span_dict):
            reasoning_count += 1

        if span_dict.get("type") != "tool":
            continue

        tool_count += 1
        tool_type = _get_tool_type(span_dict)
        action_targets.append(_get_action_target(span_dict))
        evidence_text_parts.append(tool_type)
        evidence_text_parts.append(_get_text_output(span_dict.get("output")))

        span_input = span_dict.get("input")
        if isinstance(span_input, Mapping):
            file_path = span_input.get("filePath")
            if isinstance(file_path, str):
                read_targets.append(_normalize_path_mention(file_path))
                evidence_text_parts.append(file_path)

        if _is_tool_error(span_dict):
            tool_error_count += 1
        if _is_dead_end_tool(span_dict):
            dead_end_count += 1
        if tool_type == "bash":
            bash_count += 1
            if _get_bash_type(span_dict) in {"python", "python3"}:
                python_bash_count += 1
            if _is_invalid_command(span_dict):
                invalid_command_count += 1
        if tool_type == "task":
            subagent_count += 1

    evidence_tokens = _extract_tokens(" ".join(evidence_text_parts))
    final_answer_tokens = _extract_tokens(final_answer_text)
    final_answer_supported_paths = _collect_supported_paths(
        source_file, input_text, normalized_spans
    )
    final_answer_path_mentions = _extract_path_mentions(final_answer_text)
    hallucinated_mentions = [
        mention
        for mention in final_answer_path_mentions
        if not _path_variants(mention).intersection(final_answer_supported_paths)
    ]

    grounded = False
    overlap_count = len(final_answer_tokens.intersection(evidence_tokens))
    if overlap_count >= 3:
        grounded = True
    elif any(
        _path_variants(mention).intersection(final_answer_supported_paths)
        for mention in final_answer_path_mentions
    ):
        grounded = True

    specificity = _compute_final_answer_specificity(
        final_answer_text, source_file, evidence_tokens
    )
    first_pass_success = _is_first_pass_success(
        final_answer_text, grounded, specificity
    )

    unique_targets = set(action_targets)
    backtracking_events = 0
    context_switch_count = 0
    seen_targets = set()
    previous_target: Optional[str] = None
    for target in action_targets:
        if previous_target is not None and target != previous_target:
            context_switch_count += 1
        if target in seen_targets and previous_target is not None and target != previous_target:
            backtracking_events += 1
        seen_targets.add(target)
        previous_target = target

    read_target_counts = Counter(read_targets)
    redundant_reads = sum(count - 1 for count in read_target_counts.values() if count > 1)

    metrics = {
        "trace_id": _get_trace_value(trace, "id"),
        "source_file": source_file,
        "first_pass_success": first_pass_success,
        "final_answer_specificity": specificity,
        "final_answer_grounded": grounded,
        "exploration_breadth": len(unique_targets),
        "exploration_depth": len(action_targets),
        "backtracking_rate": round(
            backtracking_events / len(action_targets), 4
        )
        if action_targets
        else 0.0,
        "redundant_read_rate": round(redundant_reads / len(read_targets), 4)
        if read_targets
        else 0.0,
        "dead_end_rate": round(dead_end_count / tool_count, 4) if tool_count else 0.0,
        "context_switch_count": context_switch_count,
        "planning_to_execution_ratio": round(reasoning_count / tool_count, 4)
        if tool_count
        else 0.0,
        "tool_error_rate": round(tool_error_count / tool_count, 4) if tool_count else 0.0,
        "hallucinated_path_rate": round(
            len(hallucinated_mentions) / len(final_answer_path_mentions), 4
        )
        if final_answer_path_mentions
        else 0.0,
        "invalid_command_rate": round(invalid_command_count / bash_count, 4)
        if bash_count
        else 0.0,
        "tool_call_count": tool_count,
        "reasoning_span_count": reasoning_count,
        "read_count": len(read_targets),
        "read_targets": read_targets,
        "bash_count": bash_count,
        "python_bash_count": python_bash_count,
        "subagent_count": subagent_count,
        "dead_end_count": dead_end_count,
        "tool_error_count": tool_error_count,
        "invalid_command_count": invalid_command_count,
        "backtracking_events": backtracking_events,
        "redundant_reads": redundant_reads,
        "final_answer_path_mentions": final_answer_path_mentions,
        "hallucinated_path_mentions": hallucinated_mentions,
        "final_answer_text": final_answer_text,
    }
    return metrics


def _summarize_turn_metrics(turn_metrics: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate turn-level metrics into summary rates and averages."""
    turn_count = len(turn_metrics)
    if turn_count == 0:
        return {}

    all_read_targets = [
        read_target
        for metric in turn_metrics
        for read_target in metric["read_targets"]
    ]
    read_target_counts = Counter(all_read_targets)
    repeated_document_read_count = sum(
        count - 1 for count in read_target_counts.values() if count > 1
    )

    return {
        "turn_count": turn_count,
        "first_pass_success_rate": round(
            sum(1 for metric in turn_metrics if metric["first_pass_success"]) / turn_count,
            4,
        ),
        "final_answer_specificity": round(
            sum(metric["final_answer_specificity"] for metric in turn_metrics) / turn_count,
            4,
        ),
        "final_answer_grounding_rate": round(
            sum(1 for metric in turn_metrics if metric["final_answer_grounded"]) / turn_count,
            4,
        ),
        "exploration_breadth": round(
            sum(metric["exploration_breadth"] for metric in turn_metrics) / turn_count,
            4,
        ),
        "exploration_depth": round(
            sum(metric["exploration_depth"] for metric in turn_metrics) / turn_count,
            4,
        ),
        "backtracking_rate": round(
            sum(metric["backtracking_rate"] for metric in turn_metrics) / turn_count,
            4,
        ),
        "redundant_read_rate": round(
            sum(metric["redundant_read_rate"] for metric in turn_metrics) / turn_count,
            4,
        ),
        "dead_end_rate": round(
            sum(metric["dead_end_count"] for metric in turn_metrics)
            / max(sum(metric["tool_call_count"] for metric in turn_metrics), 1),
            4,
        ),
        "context_switch_count": round(
            sum(metric["context_switch_count"] for metric in turn_metrics) / turn_count,
            4,
        ),
        "planning_to_execution_ratio": round(
            sum(metric["planning_to_execution_ratio"] for metric in turn_metrics)
            / turn_count,
            4,
        ),
        "tool_error_rate": round(
            sum(metric["tool_error_count"] for metric in turn_metrics)
            / max(sum(metric["tool_call_count"] for metric in turn_metrics), 1),
            4,
        ),
        "hallucinated_path_rate": round(
            sum(len(metric["hallucinated_path_mentions"]) for metric in turn_metrics)
            / max(sum(len(metric["final_answer_path_mentions"]) for metric in turn_metrics), 1),
            4,
        ),
        "invalid_command_rate": round(
            sum(metric["invalid_command_count"] for metric in turn_metrics)
            / max(sum(metric["bash_count"] for metric in turn_metrics), 1),
            4,
        ),
        "bash_call_count": sum(metric["bash_count"] for metric in turn_metrics),
        "python_bash_call_count": sum(
            metric["python_bash_count"] for metric in turn_metrics
        ),
        "subagent_call_count": sum(metric["subagent_count"] for metric in turn_metrics),
        "document_read_count": sum(metric["read_count"] for metric in turn_metrics),
        "unique_document_read_count": len(read_target_counts),
        "repeated_document_read_count": repeated_document_read_count,
        "repeated_document_read_rate": round(
            repeated_document_read_count / len(all_read_targets), 4
        )
        if all_read_targets
        else 0.0,
    }


def _save_plot(fig: Any, output_path: Path) -> None:
    """Save a matplotlib figure and close it."""
    import matplotlib.pyplot as plt

    fig.tight_layout()
    fig.savefig(output_path)
    plt.close(fig)


def _dump_json(path: Path, obj: Any) -> None:
    """Write JSON to disk with a Counter-friendly fallback."""
    with path.open("w") as file_handle:
        try:
            json.dump(obj, file_handle, indent=4)
        except TypeError:
            json.dump(dict(obj), file_handle, indent=4)


def _load_local_trace_records(
    local_logs_root: Path, workspace: str, project: str
) -> List[Dict[str, Any]]:
    """Load locally exported trace records for one workspace/project."""
    sessions_dir = _local_project_dir(local_logs_root, workspace, project) / "sessions"
    if not sessions_dir.is_dir():
        raise FileNotFoundError(
            f"No local session export directory found at: {sessions_dir}"
        )

    trace_records = []
    for session_file in sorted(sessions_dir.glob("*.json")):
        session_payload = json.loads(session_file.read_text())
        session_traces = session_payload.get("traces")
        if not isinstance(session_traces, list):
            continue
        trace_records.extend(session_traces)

    return trace_records


def _load_trajectory_file(trajectory_file: Path) -> List[Dict[str, Any]]:
    """Load trace records from one exported trajectory JSON file."""
    if not trajectory_file.is_file():
        raise FileNotFoundError(f"Trajectory file not found: {trajectory_file}")

    session_payload = json.loads(trajectory_file.read_text())
    session_traces = session_payload.get("traces")
    if not isinstance(session_traces, list):
        raise ValueError(
            f"Trajectory file does not contain a top-level 'traces' list: {trajectory_file}"
        )
    return session_traces


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    _validate_args(parser, args)
    import matplotlib.pyplot as plt
    import pandas as pd
    import seaborn as sns

    source_file_pattern = re.compile(args.source_file_regex)
    builtin_tool_types = _get_builtin_tool_types(args.builtin_tool_type)
    if args.trajectory_file is not None:
        default_metrics_key = args.trajectory_file.stem
    else:
        default_metrics_key = args.opik_project
    metrics_dir = args.metrics_dir or PROJECT_ROOT / "metrics" / default_metrics_key
    metrics_dir.mkdir(parents=True, exist_ok=True)

    if args.trajectory_file is not None:
        trace_list = _load_trajectory_file(args.trajectory_file)
        client = None
    elif args.read_local_logs:
        trace_list = _load_local_trace_records(
            local_logs_root=args.local_logs_root,
            workspace=args.opik_workspace,
            project=args.opik_project,
        )
        client = None
    else:
        from opik import Opik, configure

        configure(api_key=args.opik_key, workspace=args.opik_workspace, force=False)
        client = Opik(project_name=args.opik_project)
        trace_list = client.search_traces(project_name=args.opik_project)

    agent_python_code: List[Dict[str, Any]] = []
    subagent_calls: Counter[str] = Counter()
    subagent_outputs: MutableMapping[str, MutableMapping[str, List[Dict[str, Any]]]] = (
        defaultdict(lambda: defaultdict(list))
    )
    document_reads: Counter[str] = Counter()
    bash_calls: Counter[str] = Counter()
    custom_tool_calls: Counter[str] = Counter()
    builtin_tool_calls: Counter[str] = Counter()
    tool_output_to_tool_type: Dict[str, str] = {}
    source_file_to_usage_statistics: MutableMapping[str, List[Dict[str, float]]] = (
        defaultdict(list)
    )
    per_turn_trajectory_metrics: List[Dict[str, Any]] = []
    unmatched_turn_inputs: List[str] = []

    for trace in trace_list:
        trace_input = _get_trace_value(trace, "input", {}) or {}
        input_text = trace_input.get("text", "") if isinstance(trace_input, Mapping) else ""
        source_file = _extract_source_file(input_text, source_file_pattern)
        if source_file == "unknown" and input_text:
            unmatched_turn_inputs.append(input_text)

        source_file_to_usage_statistics[source_file].append(_extract_trace_usage(trace))

        if args.read_local_logs or args.trajectory_file is not None:
            span_list = _get_trace_value(trace, "spans", []) or []
        else:
            span_list = client.search_spans(trace_id=_get_trace_value(trace, "id"))

        normalized_spans = [_normalize_span(span) for span in span_list]
        normalized_spans.sort(
            key=lambda span_dict: (
                str(span_dict.get("start_time") or ""),
                str(span_dict.get("id") or ""),
            )
        )
        per_turn_trajectory_metrics.append(
            _compute_trace_metrics(trace, source_file, input_text, normalized_spans)
        )

        for span_dict in normalized_spans:
            if span_dict["type"] != "tool":
                continue

            tool_type = _get_tool_type(span_dict)
            if not tool_type:
                builtin_tool_calls["<missing_tool_type>"] += 1
                continue

            if tool_type == "read":
                builtin_tool_calls[tool_type] += 1
                read_file_path = span_dict["input"].get("filePath", "<unknown>")
                if isinstance(read_file_path, str) and read_file_path.endswith(source_file):
                    document_reads["source_file"] += 1
                elif read_file_path in tool_output_to_tool_type:
                    document_reads[f"tool_output:{tool_output_to_tool_type[read_file_path]}"] += 1
                else:
                    document_reads[str(read_file_path)] += 1
                continue

            if tool_type == "task":
                builtin_tool_calls[tool_type] += 1
                subagent_type = span_dict["input"].get("subagent_type", "<unknown>")
                subagent_calls[str(subagent_type)] += 1
                subagent_outputs[source_file][str(subagent_type)].append(
                    span_dict["output"]
                )
                continue

            if tool_type == "bash":
                builtin_tool_calls[tool_type] += 1
                bash_type = _get_bash_type(span_dict)
                if bash_type in {"python", "python3"}:
                    agent_python_code.append(
                        {key: span_dict[key] for key in ["input", "output"]}
                    )
                bash_calls[bash_type] += 1
                continue

            if tool_type in builtin_tool_types:
                builtin_tool_calls[tool_type] += 1
                continue

            custom_tool_calls[tool_type] += 1
            output_path = _get_output_path(span_dict)
            if output_path is not None:
                tool_output_to_tool_type[output_path] = tool_type

    print("\n====================")
    print("subagent_calls\n====================")
    print(json.dumps(dict(subagent_calls), indent=4))

    print("\n====================")
    print("document_reads\n====================")
    print(json.dumps(dict(document_reads), indent=4))

    print("\n====================")
    print("bash_calls\n====================")
    print(json.dumps(dict(bash_calls), indent=4))

    print("\n====================")
    print("custom_tool_calls\n====================")
    print(json.dumps(dict(custom_tool_calls), indent=4))

    trajectory_metrics_summary = _summarize_turn_metrics(per_turn_trajectory_metrics)
    trajectory_metrics_summary["total_duration"] = round(
        sum(
            entry.get("duration", 0) or 0
            for usage_statistics in source_file_to_usage_statistics.values()
            for entry in usage_statistics
        ),
        4,
    )
    print("\n====================")
    print("trajectory_metrics_summary\n====================")
    print(json.dumps(trajectory_metrics_summary, indent=4))

    agent_behavior_statistics = {
        "subagent_calls": dict(subagent_calls),
        "subagent_outputs": {
            source_file: dict(subagent_map)
            for source_file, subagent_map in subagent_outputs.items()
        },
        "document_reads": dict(document_reads),
        "bash_calls": dict(bash_calls),
        "builtin_tool_calls": dict(builtin_tool_calls),
        "custom_tool_calls": dict(custom_tool_calls),
        "trajectory_metrics_summary": trajectory_metrics_summary,
    }

    duration_chart_items = []
    total_tokens_chart_items = []
    token_totals: Counter[str] = Counter()

    for source_file, usage_statistics in source_file_to_usage_statistics.items():
        durations = [entry["duration"] for entry in usage_statistics if "duration" in entry]
        duration_chart_items.extend(
            {"source_file": source_file, "duration": duration} for duration in durations
        )

        total_tokens_values = [
            entry["total_tokens"]
            for entry in usage_statistics
            if "total_tokens" in entry
        ]
        total_tokens_chart_items.extend(
            {"source_file": source_file, "total_tokens": total_tokens}
            for total_tokens in total_tokens_values
        )

        for entry in usage_statistics:
            for category, value in entry.items():
                if category != "duration":
                    token_totals[category] += value

    sns.set_theme(style="whitegrid")

    if duration_chart_items:
        duration_df = pd.DataFrame(duration_chart_items)
        figure = plt.figure(
            figsize=(max(10, len(source_file_to_usage_statistics) * 1.5), 6)
        )
        axis = sns.barplot(
            data=duration_df,
            x="source_file",
            y="duration",
            errorbar="sd",
        )
        axis.set_title("Duration per Source File")
        axis.set_xlabel("Source File")
        axis.set_ylabel("Average Duration")
        axis.tick_params(axis="x", rotation=45)
        _save_plot(figure, metrics_dir / "duration_per_source_file.png")

    if token_totals:
        token_df = pd.DataFrame(
            [{"category": category, "tokens": tokens} for category, tokens in token_totals.items()]
        )
        figure = plt.figure(figsize=(max(8, len(token_totals) * 1.5), 6))
        axis = sns.barplot(data=token_df, x="category", y="tokens")
        axis.set_title("Token Consumption per Category")
        axis.set_xlabel("Category")
        axis.set_ylabel("Tokens")
        axis.tick_params(axis="x", rotation=45)
        _save_plot(figure, metrics_dir / "token_consumption_per_category.png")

    if total_tokens_chart_items:
        total_tokens_df = pd.DataFrame(total_tokens_chart_items)
        figure = plt.figure(
            figsize=(max(10, len(source_file_to_usage_statistics) * 1.5), 6)
        )
        axis = sns.barplot(
            data=total_tokens_df,
            x="source_file",
            y="total_tokens",
            errorbar="sd",
        )
        axis.set_title("Average Total Tokens per Source File")
        axis.set_xlabel("Source File")
        axis.set_ylabel("Average Total Tokens")
        axis.tick_params(axis="x", rotation=45)
        _save_plot(figure, metrics_dir / "total_tokens_per_source_file.png")

    json_outputs = [
        ("agent_python_code", agent_python_code),
        ("agent_behavior_statistics", agent_behavior_statistics),
        ("agent_usage_statistics", dict(source_file_to_usage_statistics)),
        ("token_consumption_per_category", dict(token_totals)),
        ("duration_per_source_file", duration_chart_items),
        ("trajectory_metrics_summary", trajectory_metrics_summary),
        ("trajectory_metrics_per_turn", per_turn_trajectory_metrics),
        (
            "subagent_outputs",
            {
                source_file: dict(subagent_map)
                for source_file, subagent_map in subagent_outputs.items()
            },
        ),
        ("unmatched_turn_inputs", unmatched_turn_inputs),
    ]
    for file_name, file_obj in json_outputs:
        _dump_json(metrics_dir / f"{file_name}.json", file_obj)


if __name__ == "__main__":
    main()
