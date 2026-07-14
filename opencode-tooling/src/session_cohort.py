import argparse
import json
import math
import re
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape
from typing import Any, Dict, List

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.tree import Tree

from src import construct_session_log

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SESSION_ID_PATTERN = re.compile(r"ses_[A-Za-z0-9]+")
DEFAULT_COHORT_NAME = "session-cohort"
DEFAULT_COHORT_OUTPUT_DIR = PROJECT_ROOT / "cohort-analysis"
DEFAULT_INPUT_PATH = DEFAULT_COHORT_OUTPUT_DIR / "session-cohort-input.json"
DEFAULT_OUTPUT_PATH = DEFAULT_COHORT_OUTPUT_DIR / "session-cohort-output.json"
CONSOLE = Console(width=140)
TOOL_TIMELINE_COLORS = {
    "bash": "#E8833A",
    "read": "#3B82C4",
    "grep": "#8B5CF6",
    "glob": "#0E9AA7",
    "subagent": "#0E9AA7",
    "answer": "#94A3B8",
    "user": "#2D3748",
    "tool": "#64748B",
}


def _build_parser() -> argparse.ArgumentParser:
    """Create the cohort CLI parser."""
    parser = argparse.ArgumentParser(
        description=(
            "Read a session cohort JSON file, export OpenCode session trajectories, "
            "compute per-session metrics, and aggregate metric statistics."
        )
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_INPUT_PATH,
        help="Path to cohort input JSON. Defaults to cohort-analysis/session-cohort-input.json.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_PATH,
        help="Path to aggregate cohort output JSON. Defaults to cohort-analysis/session-cohort-output.json.",
    )
    parser.add_argument(
        "--cohort-name",
        action="append",
        default=[],
        help="Run only the named cohort. Repeat to run multiple selected cohorts.",
    )
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="Stop immediately if any session export or metrics computation fails.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print verbose details while exporting each session.",
    )
    return parser


def _dedupe_preserving_order(values: List[str]) -> List[str]:
    """Remove duplicates while preserving the first occurrence of each value."""
    seen = set()
    ordered_values = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        ordered_values.append(value)
    return ordered_values


def _normalize_session_ids(cohort_name: str, session_ids: Any) -> List[str]:
    """Validate and normalize one cohort's session ID list."""
    if not isinstance(session_ids, list) or not all(
        isinstance(session_id, str) for session_id in session_ids
    ):
        raise ValueError(
            f"Cohort '{cohort_name}' must contain a 'session_ids' list of strings."
        )
    session_ids = [session_id.strip() for session_id in session_ids if session_id.strip()]
    session_ids = _dedupe_preserving_order(session_ids)
    if not session_ids:
        raise ValueError(
            f"Cohort '{cohort_name}' must contain at least one non-empty session ID."
        )

    invalid_session_ids = [
        session_id for session_id in session_ids if not SESSION_ID_PATTERN.fullmatch(session_id)
    ]
    if invalid_session_ids:
        raise ValueError(
            f"Invalid session_id value(s) in cohort '{cohort_name}': "
            f"{', '.join(invalid_session_ids)}. Expected ses_-prefixed tokens containing only letters and digits."
        )
    return session_ids


def _load_cohort_input(path: Path) -> Dict[str, Any]:
    """Load and validate the cohort input JSON file."""
    if not path.is_file():
        raise FileNotFoundError(f"Cohort input file not found: {path}")

    payload = json.loads(path.read_text())
    if not isinstance(payload, dict):
        raise ValueError("Cohort input must be a JSON object.")

    cohorts = payload.get("cohorts")
    if not isinstance(cohorts, list) or not cohorts:
        raise ValueError("Cohort input must contain a non-empty 'cohorts' list.")

    normalized_cohorts = []
    for index, cohort in enumerate(cohorts, start=1):
        if not isinstance(cohort, dict):
            raise ValueError(f"Cohort entry #{index} must be a JSON object.")

        cohort_name = cohort.get("cohort_name", f"{DEFAULT_COHORT_NAME}-{index}")
        if not isinstance(cohort_name, str) or not cohort_name.strip():
            raise ValueError(f"Cohort entry #{index} must have a non-empty 'cohort_name'.")
        cohort_name = cohort_name.strip()

        normalized_cohorts.append(
            {
                "cohort_name": cohort_name,
                "session_ids": _normalize_session_ids(cohort_name, cohort.get("session_ids")),
            }
        )

    return {"cohorts": normalized_cohorts}


def _validate_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> Dict[str, Any]:
    """Validate CLI inputs and return normalized cohort input."""
    try:
        cohort_input = _load_cohort_input(args.input)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))

    selected_cohort_names = [name.strip() for name in args.cohort_name if name.strip()]
    if selected_cohort_names:
        selected_cohort_name_set = set(selected_cohort_names)
        available_cohort_names = {
            cohort["cohort_name"] for cohort in cohort_input["cohorts"]
        }
        missing_cohort_names = sorted(selected_cohort_name_set - available_cohort_names)
        if missing_cohort_names:
            parser.error(
                "Unknown --cohort-name value(s): "
                f"{', '.join(missing_cohort_names)}. Available cohorts: "
                f"{', '.join(sorted(available_cohort_names))}"
            )
        cohort_input["cohorts"] = [
            cohort
            for cohort in cohort_input["cohorts"]
            if cohort["cohort_name"] in selected_cohort_name_set
        ]

    return cohort_input


def _export_session_trajectory(session_module: Any, session_id: str, verbose: bool) -> Path:
    """Export one session trajectory JSON using construct-session-log.py helpers."""
    opencode_db_path = session_module._get_opencode_db_path()
    with sqlite3.connect(str(opencode_db_path)) as conn:
        cursor = conn.cursor()
        message_logs = session_module._get_message_logs(
            cursor=cursor, session_id=session_id, verbose=verbose
        )
        message_logs, subagent_id_to_session = session_module._update_message_logs_with_parts(
            cursor=cursor,
            session_id=session_id,
            message_logs=message_logs,
            verbose=verbose,
        )
        turns = session_module._process_logs_for_opik(message_logs)
        trace_records, subagent_logs = session_module._build_export_trace_records(
            cursor=cursor,
            session_id=session_id,
            message_logs_opik=turns,
            subagent_id_to_session=subagent_id_to_session,
            verbose=bool(verbose),
        )
        export_bundle = {
            "session": session_module._get_session_logs(
                cursor=cursor, session_id=session_id, verbose=verbose
            ),
            "messages": turns,
            "turns": turns,
            "traces": trace_records,
            "subagent_sessions": subagent_logs,
        }

    output_path = session_module._default_json_output_path(session_id)
    return session_module._write_json_export_bundle(output_path, export_bundle)


def _compute_metrics_for_trajectory(
    session_id: str, trajectory_file: Path, cohort_name: str
) -> Dict[str, Any]:
    """Run compute-agent-metrics.py for one trajectory file and return its summary."""
    metrics_dir = DEFAULT_COHORT_OUTPUT_DIR / "metrics" / cohort_name / session_id
    command = [
        sys.executable,
        "-m",
        "src.compute_agent_metrics",
        "--trajectory-file",
        str(trajectory_file),
        "--metrics-dir",
        str(metrics_dir),
    ]
    completed_process = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed_process.returncode != 0:
        raise RuntimeError(
            "compute-agent-metrics.py failed:\n"
            f"STDOUT:\n{completed_process.stdout}\n"
            f"STDERR:\n{completed_process.stderr}"
        )

    summary_path = metrics_dir / "trajectory_metrics_summary.json"
    if not summary_path.is_file():
        raise FileNotFoundError(f"Metrics summary not found: {summary_path}")

    summary = json.loads(summary_path.read_text())
    token_summary_path = metrics_dir / "token_consumption_per_category.json"
    if token_summary_path.is_file():
        token_summary = json.loads(token_summary_path.read_text())
        for token_category, value in token_summary.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                summary[f"usage_{token_category}"] = value

    return {
        "metrics_dir": str(metrics_dir),
        "summary_path": str(summary_path),
        "token_summary_path": str(token_summary_path) if token_summary_path.is_file() else None,
        "summary": summary,
    }


def _load_ordered_document_reads(metrics_dir: Path) -> List[Dict[str, Any]]:
    """Return document reads in turn order from per-turn metrics."""
    per_turn_path = metrics_dir / "trajectory_metrics_per_turn.json"
    if not per_turn_path.is_file():
        return []

    per_turn_metrics = json.loads(per_turn_path.read_text())
    ordered_reads = []
    for turn_index, turn_metrics in enumerate(per_turn_metrics, start=1):
        for read_target in turn_metrics.get("read_targets", []):
            ordered_reads.append(
                {
                    "order": len(ordered_reads) + 1,
                    "turn": turn_index,
                    "document": read_target,
                }
            )
    return ordered_reads


def _load_ordered_tool_calls(trajectory_file: Path) -> List[Dict[str, Any]]:
    """Return tool calls in trace/span order from an exported trajectory."""
    trajectory = json.loads(trajectory_file.read_text())
    ordered_tool_calls = []
    for turn_index, trace in enumerate(trajectory.get("traces", []), start=1):
        spans = trace.get("spans", [])
        if not isinstance(spans, list):
            continue
        spans = sorted(
            spans,
            key=lambda span: (
                str(span.get("start_time") or ""),
                str(span.get("id") or ""),
            ),
        )
        for span in spans:
            if span.get("type") != "tool":
                continue
            parameters = span.get("input") if isinstance(span.get("input"), dict) else {}
            display_parameters = {
                key: value for key, value in parameters.items() if key != "tool_type"
            }
            ordered_tool_calls.append(
                {
                    "order": len(ordered_tool_calls) + 1,
                    "turn": turn_index,
                    "tool": span.get("name") or "<unknown>",
                    "parameters": display_parameters,
                    "start_time": span.get("start_time"),
                    "end_time": span.get("end_time"),
                }
            )
    return ordered_tool_calls


def _load_final_output_response(trajectory_file: Path) -> str:
    """Return the final non-empty text response from an exported trajectory."""
    trajectory = json.loads(trajectory_file.read_text())
    final_text = ""
    for trace in trajectory.get("traces", []):
        output = trace.get("output")
        if isinstance(output, dict) and isinstance(output.get("text"), str) and output["text"].strip():
            final_text = output["text"].strip()
    return final_text


def _print_activity_counts_table(session_results: List[Dict[str, Any]]) -> None:
    """Print compact per-session document read and tool call counts."""
    table = Table(
        title="Activity Counts",
        box=box.SIMPLE_HEAVY,
        header_style="bold cyan",
        title_style="bold",
    )
    table.add_column("Session", style="bold", no_wrap=True)
    table.add_column("Document Reads", justify="right")
    table.add_column("Unique Documents", justify="right")
    table.add_column("Tool Calls", justify="right")

    for result in session_results:
        if result.get("status") != "succeeded":
            continue
        document_reads = result.get("document_reads_ordered") or []
        tool_calls = result.get("tool_calls_ordered") or []
        unique_documents = {
            item.get("document")
            for item in document_reads
            if isinstance(item.get("document"), str)
        }
        table.add_row(
            result["session_id"],
            str(len(document_reads)),
            str(len(unique_documents)),
            str(len(tool_calls)),
        )

    CONSOLE.print(table)


def _get_tool_statistics(session_results: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Compute aggregate tool call coverage for successful sessions."""
    succeeded_session_ids = [
        result["session_id"]
        for result in session_results
        if result.get("status") == "succeeded"
    ]
    succeeded_session_count = len(succeeded_session_ids)
    tool_counts: Dict[str, int] = {}
    tool_sessions: Dict[str, set[str]] = {}
    for result in session_results:
        if result.get("status") != "succeeded":
            continue
        session_id = result["session_id"]
        for item in result.get("tool_calls_ordered") or []:
            tool_name = item.get("tool") or "<unknown>"
            tool_counts[tool_name] = tool_counts.get(tool_name, 0) + 1
            tool_sessions.setdefault(tool_name, set()).add(session_id)

    tool_statistics = {}
    for tool_name, count in sorted(tool_counts.items()):
        sessions_called = len(tool_sessions[tool_name])
        sessions_missing = succeeded_session_count - sessions_called
        missing_session_percent = (
            round((sessions_missing / succeeded_session_count) * 100, 2)
            if succeeded_session_count
            else 0.0
        )
        tool_statistics[tool_name] = {
            "calls": count,
            "sessions_called": sessions_called,
            "sessions_missing": sessions_missing,
            "missing_session_percent": missing_session_percent,
        }
    return tool_statistics


def _print_tool_counts_table(session_results: List[Dict[str, Any]]) -> None:
    """Print aggregate tool call counts and session coverage for a cohort."""
    tool_statistics = _get_tool_statistics(session_results)

    if not tool_statistics:
        CONSOLE.print()
        CONSOLE.print(Panel("No tool calls", title="Coverage metrics", box=box.SIMPLE))
        return

    table = Table(
        title="Coverage metrics",
        box=box.SIMPLE_HEAVY,
        header_style="bold cyan",
        title_style="bold",
        caption="% Missing = percent of successful sessions where the tool was not called.",
    )
    table.add_column("Tool", style="bold", no_wrap=True)
    table.add_column("Calls", justify="right")
    table.add_column("Sessions Called", justify="right")
    table.add_column("Sessions Missing", justify="right")
    table.add_column("% Missing", justify="right")
    for tool_name, statistics in tool_statistics.items():
        row_style = "red" if statistics["sessions_missing"] > 0 else None
        table.add_row(
            tool_name,
            str(statistics["calls"]),
            str(statistics["sessions_called"]),
            str(statistics["sessions_missing"]),
            f"{statistics['missing_session_percent']:.2f}%",
            style=row_style,
        )

    CONSOLE.print()
    CONSOLE.print(table)


def _print_session_activity_details(session_results: List[Dict[str, Any]]) -> None:
    """Print ordered document reads and tool calls grouped by session."""
    tree = Tree("[bold]Session Activity Details[/bold]")
    has_sessions = False
    for result in session_results:
        if result.get("status") != "succeeded":
            continue

        has_sessions = True
        session_node = tree.add(f"[bold bright_magenta]{result['session_id']}[/bold bright_magenta]")
        document_reads_node = session_node.add("[cyan]Document reads[/cyan]")
        document_reads = result.get("document_reads_ordered") or []
        if not document_reads:
            document_reads_node.add("[dim]none[/dim]")
        seen_documents = set()
        for item in document_reads:
            document = item["document"]
            if document in seen_documents:
                document_reads_node.add(
                    f"[red]{item['order']}. turn {item['turn']}: {document} [repeated][/red]"
                )
            else:
                document_reads_node.add(f"{item['order']}. turn {item['turn']}: {document}")
                seen_documents.add(document)

        tool_calls_node = session_node.add("[cyan]Tool calls[/cyan]")
        tool_calls = result.get("tool_calls_ordered") or []
        if not tool_calls:
            tool_calls_node.add("[dim]none[/dim]")
        for item in tool_calls:
            tool_call_node = tool_calls_node.add(f"{item['order']}. turn {item['turn']}: {item['tool']}")
            parameters = item.get("parameters")
            if parameters:
                parameters_json = json.dumps(parameters, separators=(",", ":"), default=str)
                tool_call_node.add(
                    Text(f"__TOOL_PARAMS__{parameters_json}__END_TOOL_PARAMS__", style="dim")
                )

    if has_sessions:
        CONSOLE.print(tree)
    else:
        CONSOLE.print(Panel("No successful sessions", title="Session Activity Details"))


def _print_metric_statistics_table(metric_statistics: Dict[str, Any]) -> None:
    """Print aggregate metric statistics."""
    if not metric_statistics:
        CONSOLE.print(Panel("No metrics", title="Metric Summary", box=box.SIMPLE))
        return

    table = Table(
        title="Metric Summary",
        box=box.SIMPLE_HEAVY,
        header_style="bold cyan",
        title_style="bold",
    )
    table.add_column("Metric", style="bold", no_wrap=True)
    table.add_column("Mean", justify="right")
    table.add_column("Std Dev", justify="right")
    table.add_column("Min", justify="right")
    table.add_column("Max", justify="right")
    for metric_name, statistics in sorted(metric_statistics.items()):
        display_name = metric_name
        display_statistics = statistics
        if metric_name == "total_duration":
            display_name = "total_duration_seconds"
            display_statistics = {
                "mean": statistics["mean"] / 1000,
                "stdev": statistics["stdev"] / 1000,
                "min": statistics["min"] / 1000,
                "max": statistics["max"] / 1000,
            }
        table.add_row(
            display_name,
            f"{display_statistics['mean']:,.4f}",
            f"{display_statistics['stdev']:,.4f}",
            f"{display_statistics['min']:,.4f}",
            f"{display_statistics['max']:,.4f}",
        )

    CONSOLE.print(table)


def _timeline_tool_kind(tool_name: str) -> str:
    """Return the display kind for one timeline tool label."""
    lower_name = tool_name.lower()
    if lower_name.startswith("read:"):
        return "read"
    if lower_name.startswith("bash:"):
        return "bash"
    if lower_name.startswith("grep:"):
        return "grep"
    if lower_name.startswith("glob:"):
        return "glob"
    if lower_name.startswith("subagent"):
        return "subagent"
    return "tool"


def _timeline_tool_label(kind: str, order: int) -> str:
    """Return the compact label shown inside a timeline node."""
    if kind == "subagent":
        return "S"
    if kind == "tool":
        return "t"
    return kind[0]


def _shorten_timeline_text(value: str, max_chars: int = 64) -> str:
    """Return a compact label that fits under a timeline block."""
    if len(value) <= max_chars:
        return value
    return value[: max_chars - 1] + "…"


def _timeline_tool_detail_label(tool_name: str) -> str:
    """Return the text label shown below a tool node."""
    if ":" not in tool_name:
        return _shorten_timeline_text(tool_name)

    prefix, raw_detail = tool_name.split(":", 1)
    detail = raw_detail.strip()
    lower_prefix = prefix.lower()
    if lower_prefix in {"read", "glob"}:
        return _shorten_timeline_text(Path(detail).name or detail.lstrip("/"))
    if lower_prefix in {"grep", "bash"}:
        return _shorten_timeline_text(f"{lower_prefix}: {detail}")
    if lower_prefix == "tool":
        return _shorten_timeline_text(detail)
    return _shorten_timeline_text(prefix)


def _parse_timeline_timestamp(value: Any) -> float | None:
    """Return a unix timestamp for SVG positioning."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return None


def _assign_overlap_lanes(turn_calls: List[Dict[str, Any]]) -> tuple[list[Dict[str, Any]], int]:
    """Assign vertical lanes to overlapping tool calls in one turn."""
    timed_calls = []
    for item in turn_calls:
        start = _parse_timeline_timestamp(item.get("start_time"))
        end = _parse_timeline_timestamp(item.get("end_time"))
        if start is None or end is None:
            return [], 0
        if end < start:
            end = start
        timed_item = dict(item)
        timed_item["start_ts"] = start
        timed_item["end_ts"] = end
        timed_calls.append(timed_item)

    lane_end_times: list[float] = []
    assigned_calls = []
    for item in sorted(timed_calls, key=lambda call: (call["start_ts"], call["end_ts"], call["order"])):
        lane_index = None
        for index, lane_end_time in enumerate(lane_end_times):
            if item["start_ts"] >= lane_end_time:
                lane_index = index
                break
        if lane_index is None:
            lane_index = len(lane_end_times)
            lane_end_times.append(item["end_ts"])
        else:
            lane_end_times[lane_index] = item["end_ts"]
        item["lane"] = lane_index
        assigned_calls.append(item)
    return assigned_calls, max(len(lane_end_times), 1)


def _timeline_time_groups(timed_calls: List[Dict[str, Any]], threshold_seconds: float = 0.25) -> list[float]:
    """Return representative start times for visually distinct tool-call groups."""
    group_starts: list[float] = []
    for item in sorted(timed_calls, key=lambda call: call["start_ts"]):
        start_time = item["start_ts"]
        if not group_starts or start_time - group_starts[-1] > threshold_seconds:
            group_starts.append(start_time)
    return group_starts


def _build_trajectory_timeline_svg(session_result: Dict[str, Any]) -> str:
    """Build a lightweight SVG timeline for one exported trajectory."""
    session_id = session_result["session_id"]
    tool_calls = session_result.get("tool_calls_ordered") or []
    turn_count = max([item.get("turn", 1) for item in tool_calls] + [1])
    width = 1080
    left = 72
    right = width - 72
    top = 132
    timed_turns = []
    calls_by_turn: Dict[int, List[Dict[str, Any]]] = {}
    for item in tool_calls:
        calls_by_turn.setdefault(int(item.get("turn", 1)), []).append(item)

    for turn_index in range(1, turn_count + 1):
        turn_calls = calls_by_turn.get(turn_index, [])
        assigned_calls, lane_count = _assign_overlap_lanes(turn_calls)
        timed_turns.append(
            {
                "turn": turn_index,
                "calls": assigned_calls,
                "raw_calls": turn_calls,
                "lane_count": lane_count,
                "height": max(132, 98 + lane_count * 54),
            }
        )

    height = top + sum(turn["height"] for turn in timed_turns) + 72

    svg_parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" font-family="arizonaSans,Inter,Segoe UI,Arial,sans-serif">',
        '<rect width="100%" height="100%" fill="#0B0B10"/>',
        f'<rect x="24" y="24" width="{width - 48}" height="{height - 48}" rx="18" fill="#111116" stroke="#28292C"/>',
        f'<text x="{left}" y="54" font-size="13" fill="#A2A2A5">{xml_escape(session_id)} · time flows left to right; tool calls are grouped by turn.</text>',
    ]

    legend_items = [
        ("U", "User", TOOL_TIMELINE_COLORS["user"]),
        ("r", "read", TOOL_TIMELINE_COLORS["read"]),
        ("g", "glob/grep", TOOL_TIMELINE_COLORS["glob"]),
        ("S", "subagent", TOOL_TIMELINE_COLORS["subagent"]),
        ("t", "tool", TOOL_TIMELINE_COLORS["tool"]),
        ("✓", "answer", TOOL_TIMELINE_COLORS["answer"]),
    ]
    legend_x = left
    for label, text, color in legend_items:
        svg_parts.append(f'<rect x="{legend_x}" y="88" width="16" height="16" rx="3" fill="{color}"/>')
        svg_parts.append(f'<text x="{legend_x + 8}" y="100.5" text-anchor="middle" font-size="10" font-weight="700" fill="#FFFFFF">{xml_escape(label)}</text>')
        svg_parts.append(f'<text x="{legend_x + 22}" y="101" font-size="12" fill="#D1D1D5">{xml_escape(text)}</text>')
        legend_x += 92

    current_y = top
    for turn_data in timed_turns:
        turn_index = turn_data["turn"]
        turn_calls = turn_data["calls"]
        raw_calls = turn_data["raw_calls"]
        lane_count = turn_data["lane_count"]
        y = current_y + 28
        turn_start = min([item["start_ts"] for item in turn_calls], default=0)
        turn_end = max([item["end_ts"] for item in turn_calls], default=turn_start + 1)
        if turn_end <= turn_start:
            turn_end = turn_start + 1
        axis_start_x = left + 64
        axis_end_x = right - 64

        def x_for_time(timestamp: float) -> float:
            return axis_start_x + ((timestamp - turn_start) / (turn_end - turn_start)) * (axis_end_x - axis_start_x)

        def x_for_order(order: int) -> float:
            step = (axis_end_x - axis_start_x) / max(len(raw_calls) + 1, 1)
            return axis_start_x + order * step

        svg_parts.append(f'<text x="{left - 58}" y="{y + 5}" font-size="12" font-weight="700" fill="#F6F6FA">turn {turn_index}</text>')
        svg_parts.append(f'<line x1="{left}" y1="{y}" x2="{right}" y2="{y}" stroke="#28292C" stroke-width="1.5"/>')
        svg_parts.append(f'<rect x="{left - 15}" y="{y - 19}" width="30" height="38" rx="5" fill="{TOOL_TIMELINE_COLORS["user"]}"><title>User turn {turn_index}</title></rect>')
        svg_parts.append(f'<text x="{left}" y="{y + 5}" text-anchor="middle" font-size="13" font-weight="700" fill="#FFFFFF">U{turn_index}</text>')
        svg_parts.append(f'<text x="{left}" y="{y + 35}" transform="rotate(45 {left} {y + 35})" text-anchor="start" font-size="10.5" font-weight="600" fill="#D1D1D5">user</text>')

        if turn_calls:
            if lane_count > 1:
                svg_parts.append(f'<text x="{axis_start_x}" y="{y - 26}" font-size="11" font-weight="700" fill="#9EA0FF">parallel / overlapping tool calls</text>')
            svg_parts.append(f'<line x1="{axis_start_x}" y1="{y}" x2="{axis_end_x}" y2="{y}" stroke="#424245" stroke-width="2" stroke-dasharray="5 4"/>')
            svg_parts.append(f'<text x="{axis_end_x}" y="{y - 12}" text-anchor="end" font-size="10" fill="#A2A2A5">elapsed seconds</text>')
            for group_start in _timeline_time_groups(turn_calls):
                group_x = x_for_time(group_start)
                elapsed_seconds = group_start - turn_start
                svg_parts.append(f'<line x1="{group_x}" y1="{y - 7}" x2="{group_x}" y2="{y + 7}" stroke="#7678ED" stroke-width="1.2" opacity="0.8"/>')
                svg_parts.append(f'<text x="{group_x}" y="{y + 20}" text-anchor="middle" font-size="9.5" fill="#A2A2A5">+{elapsed_seconds:.1f}s</text>')
            for item in turn_calls:
                order = int(item.get("order", 0))
                tool_name = str(item.get("tool", "<unknown>"))
                kind = _timeline_tool_kind(tool_name)
                color = TOOL_TIMELINE_COLORS[kind]
                label = _timeline_tool_label(kind, order)
                detail_label = _timeline_tool_detail_label(tool_name)
                lane_y = y + 42 + item["lane"] * 54
                start_x = x_for_time(item["start_ts"])
                end_x = x_for_time(item["end_ts"])
                bar_width = max(end_x - start_x, 30)
                svg_parts.append(f'<rect x="{start_x}" y="{lane_y - 15}" width="{bar_width}" height="30" rx="6" fill="{color}"><title>{xml_escape(tool_name)} · {item.get("start_time")} → {item.get("end_time")}</title></rect>')
                svg_parts.append(f'<text x="{start_x + min(bar_width / 2, 15)}" y="{lane_y + 5}" text-anchor="middle" font-size="13" font-weight="700" fill="#FFFFFF">{xml_escape(label)}</text>')
                svg_parts.append(f'<text x="{start_x}" y="{lane_y + 26}" transform="rotate(45 {start_x} {lane_y + 26})" text-anchor="start" font-size="10.5" font-weight="600" fill="#D1D1D5">{xml_escape(detail_label)}</text>')
        else:
            for item in raw_calls:
                order = int(item.get("order", 0))
                tool_name = str(item.get("tool", "<unknown>"))
                kind = _timeline_tool_kind(tool_name)
                color = TOOL_TIMELINE_COLORS[kind]
                label = _timeline_tool_label(kind, order)
                detail_label = _timeline_tool_detail_label(tool_name)
                x = x_for_order(order)
                svg_parts.append(f'<rect x="{x - 15}" y="{y - 15}" width="30" height="30" rx="5" fill="{color}"><title>{xml_escape(tool_name)}</title></rect>')
                svg_parts.append(f'<text x="{x}" y="{y + 5}" text-anchor="middle" font-size="13" font-weight="700" fill="#FFFFFF">{xml_escape(label)}</text>')
                svg_parts.append(f'<text x="{x}" y="{y + 32}" transform="rotate(45 {x} {y + 32})" text-anchor="start" font-size="10.5" font-weight="600" fill="#D1D1D5">{xml_escape(detail_label)}</text>')

        answer_x = right
        svg_parts.append(f'<circle cx="{answer_x}" cy="{y}" r="15" fill="{TOOL_TIMELINE_COLORS["answer"]}"><title>turn {turn_index} final answer</title></circle>')
        svg_parts.append(f'<text x="{answer_x}" y="{y + 5}" text-anchor="middle" font-size="13" font-weight="700" fill="#FFFFFF">✓</text>')
        svg_parts.append(f'<text x="{answer_x}" y="{y + 32}" transform="rotate(45 {answer_x} {y + 32})" text-anchor="start" font-size="10.5" font-weight="600" fill="#D1D1D5">answer</text>')
        current_y += turn_data["height"]

    svg_parts.append("</svg>")
    return "\n".join(svg_parts)


def _print_trajectory_timeline_svgs(session_results: List[Dict[str, Any]]) -> None:
    """Print SVG timeline blocks for successful session trajectories."""
    CONSOLE.print()
    CONSOLE.print("[bold]Trajectory Timeline SVGs[/bold]")
    for result in session_results:
        if result.get("status") != "succeeded":
            continue
        CONSOLE.print("<!-- cohort-trajectory-svg-start -->", markup=False)
        CONSOLE.print(_build_trajectory_timeline_svg(result), markup=False)
        CONSOLE.print("<!-- cohort-trajectory-svg-end -->", markup=False)


def _print_final_output_responses(session_results: List[Dict[str, Any]]) -> None:
    """Print final output responses for each successful session."""
    CONSOLE.print()
    CONSOLE.print("[bold]Final Output Responses[/bold]")
    for result in session_results:
        if result.get("status") != "succeeded":
            continue
        response = result.get("final_output_response") or ""
        CONSOLE.print(f"\n[bold bright_magenta]{result['session_id']}[/bold bright_magenta]")
        if response:
            CONSOLE.print(response, markup=False)
        else:
            CONSOLE.print("[dim]No final response captured.[/dim]")


def _numeric_summary_values(session_results: List[Dict[str, Any]]) -> Dict[str, List[float]]:
    """Collect numeric metric values from successful session summaries."""
    values_by_metric: Dict[str, List[float]] = {}
    for result in session_results:
        if result.get("status") != "succeeded":
            continue
        summary = result.get("metrics_summary") or {}
        for metric_name, value in summary.items():
            if isinstance(value, bool):
                continue
            if isinstance(value, (int, float)):
                values_by_metric.setdefault(metric_name, []).append(float(value))
    return values_by_metric


def _aggregate_metric_statistics(session_results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Compute aggregate statistics for numeric per-session metrics."""
    aggregate = {}
    for metric_name, values in sorted(_numeric_summary_values(session_results).items()):
        count = len(values)
        mean = sum(values) / count
        variance = sum((value - mean) ** 2 for value in values) / count
        aggregate[metric_name] = {
            "count": count,
            "mean": mean,
            "stdev": math.sqrt(variance),
            "min": min(values),
            "max": max(values),
        }
    return aggregate


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    """Write a JSON payload to disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as file_handle:
        json.dump(payload, file_handle, indent=4)


def _process_cohort(
    cohort_name: str,
    session_ids: List[str],
    session_module: Any,
    verbose: bool,
    fail_fast: bool,
) -> Dict[str, Any]:
    """Export and analyze all sessions in one cohort."""
    CONSOLE.rule(f"[bold]Cohort: {cohort_name}[/bold]")
    session_results = []
    for index, session_id in enumerate(session_ids, start=1):
        CONSOLE.print(
            f"[cyan]Processing[/cyan] [bold]{session_id}[/bold] "
            f"[dim]({index}/{len(session_ids)})[/dim]"
        )
        try:
            trajectory_file = _export_session_trajectory(
                session_module=session_module,
                session_id=session_id,
                verbose=verbose,
            )
            metrics_result = _compute_metrics_for_trajectory(
                session_id=session_id,
                trajectory_file=trajectory_file,
                cohort_name=cohort_name,
            )
            metrics_dir = Path(metrics_result["metrics_dir"])
            document_reads_ordered = _load_ordered_document_reads(metrics_dir)
            tool_calls_ordered = _load_ordered_tool_calls(trajectory_file)
            final_output_response = _load_final_output_response(trajectory_file)
        except Exception as exc:
            session_result = {
                "session_id": session_id,
                "status": "failed",
                "error": str(exc),
            }
            session_results.append(session_result)
            CONSOLE.print(f"[red]Failed[/red] [bold]{session_id}[/bold]: {exc}")
            if fail_fast:
                raise
            continue

        session_result = {
            "session_id": session_id,
            "status": "succeeded",
            "trajectory_file": str(trajectory_file),
            "metrics_dir": metrics_result["metrics_dir"],
            "metrics_summary_path": metrics_result["summary_path"],
            "token_summary_path": metrics_result["token_summary_path"],
            "metrics_summary": metrics_result["summary"],
            "document_reads_ordered": document_reads_ordered,
            "tool_calls_ordered": tool_calls_ordered,
            "final_output_response": final_output_response,
        }
        session_results.append(session_result)
        CONSOLE.print(
            f"[green]Completed[/green] [bold]{session_id}[/bold] "
            f"[dim]trajectory={trajectory_file} metrics={metrics_result['metrics_dir']}[/dim]"
        )

    succeeded_count = sum(1 for result in session_results if result["status"] == "succeeded")
    failed_count = len(session_results) - succeeded_count
    metric_statistics = _aggregate_metric_statistics(session_results)
    tool_statistics = _get_tool_statistics(session_results)
    CONSOLE.print(
        Panel.fit(
            f"sessions={len(session_results)}\n"
            f"succeeded={succeeded_count}\n"
            f"failed={failed_count}",
            title=f"{cohort_name} Summary",
            border_style="cyan",
        )
    )
    _print_activity_counts_table(session_results)
    _print_session_activity_details(session_results)
    _print_tool_counts_table(session_results)
    _print_metric_statistics_table(metric_statistics)
    _print_trajectory_timeline_svgs(session_results)
    _print_final_output_responses(session_results)
    return {
        "cohort_name": cohort_name,
        "session_count": len(session_results),
        "succeeded_count": succeeded_count,
        "failed_count": failed_count,
        "trajectory_files": [
            result["trajectory_file"]
            for result in session_results
            if result.get("status") == "succeeded"
        ],
        "metric_statistics": metric_statistics,
        "tool_statistics": tool_statistics,
        "sessions": session_results,
    }


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    cohort_input = _validate_args(parser, args)
    session_module = construct_session_log

    cohort_results = [
        _process_cohort(
            cohort_name=cohort["cohort_name"],
            session_ids=cohort["session_ids"],
            session_module=session_module,
            verbose=args.verbose,
            fail_fast=args.fail_fast,
        )
        for cohort in cohort_input["cohorts"]
    ]

    cohort_report = {
        "input_file": str(args.input),
        "cohort_count": len(cohort_results),
        "session_count": sum(cohort["session_count"] for cohort in cohort_results),
        "succeeded_count": sum(cohort["succeeded_count"] for cohort in cohort_results),
        "failed_count": sum(cohort["failed_count"] for cohort in cohort_results),
        "cohorts": cohort_results,
    }
    _write_json(args.output, cohort_report)
    CONSOLE.print(f"[green]Wrote cohort report[/green]: [bold]{args.output}[/bold]")


if __name__ == "__main__":
    main()
