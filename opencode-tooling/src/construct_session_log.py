import argparse
import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union, Tuple

from opik.id_helpers import generate_id

USERS_PATH = "/Users/"
OPENCODE_PATH = ".local/share/opencode/opencode.db"
SESSION_TABLE = "session"
MESSAGE_TABLE = "message"
PART_TABLE = "part"
IGNORE_USERS = {"/Users/Shared", "/Users/ITAdmin"}
SKIP_PART_TYPES = {"step-start", "step-finish", "patch"}
SESSION_ID_PATTERN = re.compile(r"ses_[A-Za-z0-9]+")
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOCAL_LOGS_ROOT = PROJECT_ROOT / "local_logs"
DEFAULT_SESSION_TRAJECTORIES_DIR = PROJECT_ROOT / "session_trajectories"
DEFAULT_MAX_STEP_OUTPUT_CHARS = 10000
TRUNCATED_OUTPUT_SUFFIX = "\n...[truncated]"

# # Unused typing
# LogValue = Union[str, Dict[str, Any], List[Any], int, float, bool, None]
# LogRecord = Dict[str, LogValue]

# region opencode.db path utils
def _get_immediate_folders_pathlib(directory_path):
    """
    Returns a list of immediate subfolder paths within the given directory.
    Used for getting system users --> loading /Users/.../opencode.db
    """
    p = Path(directory_path)
    folders = [entry for entry in p.iterdir() if entry.is_dir()]
    return folders


def _get_opencode_db_path():
    """
    Returns path to opencode.db file, inferring correct Users/... folder
    """
    # go through system users and check if .../opencode.db path exists for that user
    # - if so, add it to the potential paths to return
    user_folders_list = _get_immediate_folders_pathlib(USERS_PATH)
    opencode_db_paths = []
    for user_folder in user_folders_list:
        if str(user_folder) in IGNORE_USERS:
            continue
        db_path = user_folder.joinpath(OPENCODE_PATH)
        if db_path.is_file():
            opencode_db_paths.append(db_path)

    # raise error if opencode.db file unable to be found
    if len(opencode_db_paths) == 0:
        raise FileNotFoundError(
            f"No opencode.db path was found in Users/.../{OPENCODE_PATH}"
        )
    # raise error if multiple opencode.db files found
    if len(opencode_db_paths) > 1:
        matched_paths = "\n".join(str(path) for path in opencode_db_paths)
        raise OSError(
            f"Multiple opencode.db paths were found in Users/.../{OPENCODE_PATH}:\n{matched_paths}"
        )

    return opencode_db_paths[0]


# endregion opencode.db path utils


# region opencode.db parsing utils
def _clean_db_value(value: str) -> Union[str, Dict]:
    """Checks if a given DB value is a string representation of a dictionary -- if so, convert to dictionary"""
    try:
        return json.loads(value)
    except json.decoder.JSONDecodeError:
        return value
    except TypeError:
        return ""


def _clean_db_row(
    column_names: List[str], values: List[str]
) -> Dict[str, Union[str, Dict]]:
    """Combines the DB column names with the values of a DB row, formatting as a dictionary (and parsing DB values using _clean_db_value)"""
    return dict(zip(column_names, [_clean_db_value(value) for value in values]))


def _fetch_rows(
    cursor: sqlite3.Cursor,
    table_name: str,
    where_clause: str = "",
    parameters: Sequence[str] = (),
) -> List[sqlite3.Row]:
    """Execute a SELECT query and return all matching rows."""
    query = f"SELECT * FROM {table_name}"
    if where_clause:
        query += f" WHERE {where_clause}"
    cursor.execute(query, parameters)
    return cursor.fetchall()


def _get_table_columns(cursor: sqlite3.Cursor) -> List:
    """Gets the list of column names on an executed result from a sqlite3 cursor"""
    try:
        return [col[0] for col in cursor.description]
    except TypeError:
        raise AttributeError(
            f"_get_table_columns failed: no query has been executed for the current sqlite3.Cursor object"
        )


def _unix_ms_to_datetime(unix_ms: int) -> datetime:
    """Convert a unix timestamp in milliseconds to a datetime."""
    return datetime.fromtimestamp(unix_ms / 1000.0)


def _build_end_time(
    start_unix_ms: int, latency_ms: Optional[int]
) -> Optional[datetime]:
    """Convert start time and optional latency into an end timestamp."""
    if latency_ms is None:
        return None
    return _unix_ms_to_datetime(start_unix_ms + latency_ms)


def _metadata_with_metrics(
    metadata: Dict[str, Any], metrics: Dict[str, Any]
) -> Dict[str, Any]:
    """Return a shallow metadata copy with metrics included for logging."""
    new_metadata = dict(metadata)
    new_metadata["metrics"] = dict(metrics)
    return new_metadata


def _json_default_serializer(value: Any) -> str:
    """Serialize unsupported JSON values for local debugging output."""
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _sanitize_path_component(value: str) -> str:
    """Return a filesystem-safe directory name."""
    sanitized = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip())
    return sanitized.strip("-") or "default"


def _local_project_dir(root: Union[str, Path], workspace: str, project: str) -> Path:
    """Return the local directory used for a workspace/project export backend."""
    return (
        Path(root)
        / _sanitize_path_component(workspace)
        / _sanitize_path_component(project)
    )


def _new_local_span_id() -> str:
    """Return an Opik-compatible UUIDv7 for exported spans."""
    return generate_id()


def _get_first_user_text(message_log: Dict[str, Union[str, Dict]]) -> Dict[str, Any]:
    """Return the first user text payload from a cleaned message log."""
    parts = message_log.get("parts")
    if not isinstance(parts, list):
        return {}

    for part in parts:
        if not isinstance(part, dict):
            continue
        part_input = part.get("input")
        if not isinstance(part_input, dict):
            continue
        if isinstance(part_input.get("text"), str):
            return part_input
    return {}


def _get_last_assistant_output(
    message_log: Dict[str, Union[str, Dict]],
) -> Dict[str, Any]:
    """Return the last non-empty assistant output payload from a cleaned message log."""
    parts = message_log.get("parts")
    if not isinstance(parts, list):
        return {}

    fallback_output = {}
    for part in reversed(parts):
        if not isinstance(part, dict):
            continue
        part_output = part.get("output")
        if not isinstance(part_output, dict):
            continue
        if part_output:
            fallback_output = part_output
        if isinstance(part_output.get("text"), str):
            return part_output
    return fallback_output


def _build_turn_metadata(
    session_id: str,
    turn_index: int,
    user_message: Dict[str, Union[str, Dict]],
    assistant_messages: List[Dict[str, Union[str, Dict]]],
) -> Dict[str, Any]:
    """Build trace-level metadata for one user turn in a session."""
    assistant_message_ids = [
        msg["metadata"]["message_id"]
        for msg in assistant_messages
        if isinstance(msg.get("metadata"), dict)
        and isinstance(msg["metadata"].get("message_id"), str)
    ]
    assistant_agents = []
    for msg in assistant_messages:
        metadata = msg.get("metadata")
        if not isinstance(metadata, dict):
            continue
        agent_name = metadata.get("agent")
        if isinstance(agent_name, str) and agent_name not in assistant_agents:
            assistant_agents.append(agent_name)

    turn_metadata = dict(user_message["metadata"])
    turn_metadata.update(
        {
            "session_id": session_id,
            "turn_index": turn_index,
            "turn_message_id": user_message["metadata"]["message_id"],
            "assistant_message_ids": assistant_message_ids,
            "assistant_agents": assistant_agents,
            "assistant_message_count": len(assistant_messages),
        }
    )
    return turn_metadata


# endregion opencode.db parsing utils


# region opencode.db retrieval functions
def _get_session_logs(
    cursor: sqlite3.Cursor, session_id: str, verbose: Optional[bool] = False
) -> Dict[str, str]:
    """Gets the metadata associated with an opencode session (via session_id parameter)"""

    # get entries from sessions where session_id is a match
    rows = _fetch_rows(cursor, SESSION_TABLE, "id = ?", (session_id,))

    # error handling: no associated session found
    if len(rows) == 0:
        raise KeyError(
            f"No session found for session_id: {session_id}. Check that session_id is correct."
        )
    # error handling: multiple sessions found (given id assignment of opencode sessions, this should never happen)
    if len(rows) > 1:
        raise AttributeError(
            f"Multiple sessions found in opencode.db for session_id: {session_id}. This error should never be raised."
        )

    session_logs = _clean_db_row(_get_table_columns(cursor), rows[0])
    if verbose:
        print(json.dumps(session_logs, indent=4))

    return session_logs


def _get_message_logs(
    cursor: sqlite3.Cursor, session_id: str, verbose: Optional[bool] = False
) -> List[Dict[str, str]]:
    """Fetch all raw message rows for a session, parse JSON-like fields, and sort them chronologically."""
    # get entries from messages where session_id is a match
    rows = _fetch_rows(cursor, MESSAGE_TABLE, "session_id = ?", (session_id,))
    column_names = _get_table_columns(cursor)

    message_logs = []
    for row in rows:
        message_json = _clean_db_row(column_names, row)
        _validate_message_log(message_json)
        message_logs.append(message_json)
        if verbose:
            print(json.dumps(message_json, indent=4))

    message_logs.sort(key=lambda message: message["data"]["time"]["created"])
    return message_logs


def _validate_message_log(message_log: Dict[str, Union[str, Dict]]) -> None:
    """
    Validate structure of individual message logs
    -- ensure that log structure supports downstream analysis
    -- ensure that our logging pipeline is robust to any opencode updates that might change structure
    """

    validation_prefix = "\n[DEBUG] Message log validation issue: "
    validation_errors = []

    # Ensure that expected log keys are present
    # - following keys should be present for all opencode.db message logs
    for log_key in ["time_created", "time_updated", "data", "id", "session_id"]:
        if log_key not in message_log:
            validation_errors.append(
                validation_prefix + f"'{log_key}' key not present in log"
            )

    data = message_log.get("data")
    if data is not None and not isinstance(data, dict):
        validation_errors.append(validation_prefix + "'data' value expected to be a dict")
        data = None

    if data is not None:
        # Ensure that expected data keys are present
        for log_key in ["time", "role", "agent"]:
            if log_key not in data:
                validation_errors.append(
                    validation_prefix + f"'{log_key}' key not present in log data"
                )

        agent = data.get("agent")
        if agent is not None and not isinstance(agent, str):
            validation_errors.append(
                validation_prefix + "'data.agent' value expected to be a string"
            )

        message_time = data.get("time")
        if message_time is not None and not isinstance(message_time, dict):
            validation_errors.append(
                validation_prefix + "'data.time' value expected to be a dict"
            )
            message_time = None

        if message_time is not None:
            if "created" not in message_time:
                validation_errors.append(
                    validation_prefix + "time dictionary missing required key 'created'"
                )

            # Ensure that "data"->"time" dictionary includes what we expect
            for k in message_time.keys():
                if k not in ["created", "completed"]:
                    validation_errors.append(
                        validation_prefix + f"time dictionary contains key '{k}'"
                    )

        role = data.get("role")
        if isinstance(role, str):
            # ==== Role-specific validation
            if role == "user":
                # Ensure that "model" key is present in all user messages
                if "model" not in data:
                    validation_errors.append(
                        validation_prefix + "'model' not present in log data for role 'user'"
                    )
            elif role == "assistant":
                # Ensure that "modelID" and "providerID" are present in assistant messages
                for log_key in ["modelID", "providerID"]:
                    if log_key not in data:
                        validation_errors.append(
                            validation_prefix
                            + f"'{log_key}' key not present in log data for role 'assistant'"
                        )
            else:
                # Unexpected role encountered
                validation_errors.append(
                    validation_prefix + f"unexpected role encountered: {role}"
                )
        elif role is not None:
            validation_errors.append(validation_prefix + "'data.role' value expected to be a string")

    # Ensure that the following keys don't have data in their values
    # - more so a sanity check for opencode's logging behavior
    for log_key in ["time_created", "time_updated"]:
        log_value = message_log.get(log_key)
        if isinstance(log_value, str) and len(log_value) > 0:
            validation_errors.append(
                validation_prefix + f"'{log_key}' value expected to be empty string"
            )

    if len(validation_errors) > 0:
        print("\n======== Validation errors encountered ==========")
        for ix, error in enumerate(validation_errors):
            print(f"\t{ix + 1}) {error}")
        print("\nMessage log:")
        print(message_log)
        raise AttributeError("Log unable to be correctly validated.")



def _clean_message_log(
    message_log: Dict[str, Union[str, Dict]],
) -> Dict[str, Union[str, Dict]]:
    """Normalize a raw message row into the simplified structure used downstream."""
    # debugging / sanity checks
    _validate_message_log(message_log)

    message_data = message_log["data"]
    message_time = message_data["time"]
    message_role = message_data["role"]

    # initialize simplified message log
    new_message_log = {
        "role": message_role,
        "metadata": {
            "message_id": message_log["id"],
            "session_id": message_log["session_id"],
            "time_created_unix_ms": None,
            "agent": message_data["agent"],
            "model": None,
        },
        "metrics": {},
        "parts": [],
    }

    # populate metadata: start time + model information
    new_message_log["metadata"]["time_created_unix_ms"] = message_time["created"]
    if message_role == "user":
        new_message_log["metadata"]["model"] = message_data["model"]
    elif message_role == "assistant":
        new_message_log["metadata"]["model"] = {
            "modelID": message_data["modelID"],
            "providerID": message_data["providerID"],
        }

    # populate metrics if available (unavailable when role="user", i.e. user sends a message)
    if "completed" in message_time:
        new_message_log["metrics"]["latency_ms"] = (
            message_time["completed"] - message_time["created"]
        )
    if "tokens" in message_data:
        tokens = message_data["tokens"]
        input_tokens = tokens.get("input", 0)
        output_tokens = tokens.get("output", 0)
        reasoning_tokens = tokens.get("reasoning", 0)
        cache_tokens = tokens.get("cache", {})

        new_message_log["metrics"]["tokens_total"] = tokens.get(
            "total", input_tokens + output_tokens + reasoning_tokens
        )
        new_message_log["metrics"]["tokens_input"] = input_tokens
        new_message_log["metrics"]["tokens_output"] = output_tokens
        new_message_log["metrics"]["tokens_reasoning"] = reasoning_tokens
        new_message_log["metrics"]["tokens_cache_read"] = cache_tokens.get("read", 0)
        new_message_log["metrics"]["tokens_cache_write"] = cache_tokens.get("write", 0)
    if "cost" in message_data:
        new_message_log["metrics"]["cost_dollars"] = message_data["cost"]

    return new_message_log


def _get_part_logs(
    cursor: sqlite3.Cursor,
    session_id: str,
    message_id: str,
    verbose: Optional[bool] = False,
) -> List[Dict[str, Union[str, Dict]]]:
    """Fetch all raw part rows for a message and parse JSON-like fields."""
    # get entries from messages where session_id and message_id are matches
    rows = _fetch_rows(
        cursor,
        PART_TABLE,
        "session_id = ? AND message_id = ?",
        (session_id, message_id),
    )
    column_names = _get_table_columns(cursor)

    part_logs = []
    for row in rows:
        part_json = _clean_db_row(column_names, row)
        part_logs.append(part_json)
        if verbose:
            print(json.dumps(part_json, indent=4))
    return part_logs


def _filter_part_logs(
    part_logs: List[Dict[str, Union[str, Dict]]],
) -> List[Dict[str, Union[str, Dict]]]:
    """Drop part records that are bookkeeping markers rather than meaningful steps."""

    def _keep_part_log(part_log: Dict[str, Union[str, Dict]]) -> bool:
        if "data" not in part_log:
            print("\n[DEBUGGING] Part log doesn't contain key 'data' -- investigate:")
            print(part_log)
            raise AttributeError("Log unable to be correctly validated.")
        part_data = part_log["data"]

        # debugging / sanity check
        # -- monitoring access to 'type' key:
        try:
            part_type = part_data["type"]
        except KeyError:
            # debugging / sanity check -- unexpected role
            print("\n[DEBUGGING] Part log data has no 'type' key -- investigate:")
            print(part_log)
            raise AttributeError("Log unable to be correctly validated.")

        if part_type in SKIP_PART_TYPES:
            return False
        return True

    return [part for part in part_logs if _keep_part_log(part)]


def _validate_part_log(part: Dict[str, Union[str, Dict]]) -> None:
    """Validate structure of individual part log -- ensure that log structure supports downstream analysis"""

    validation_prefix = "\n[DEBUG] Part log validation issue: "
    validation_errors = []

    def _validate_required_and_optional_keys(
        obj: Dict[str, Union[str, Dict]],
        required_keys: List[str],
        optional_keys: List[str],
        object_name: str,
        prohibit_unexpected_keys: Optional[bool] = True,
    ) -> None:
        if not isinstance(obj, dict):
            validation_errors.append(
                validation_prefix + f"'{object_name}' expected to be a dictionary"
            )
            return

        present_keys = set(obj.keys())
        required_keys_set = set(required_keys)
        allowed_keys = required_keys_set.union(optional_keys)

        missing_keys = required_keys_set.difference(present_keys)
        for missing_key in missing_keys:
            validation_errors.append(
                validation_prefix
                + f"'{object_name}' missing required key '{missing_key}'"
            )

        if prohibit_unexpected_keys:
            unexpected_keys = present_keys.difference(allowed_keys)
            for unexpected_key in unexpected_keys:
                validation_errors.append(
                    validation_prefix
                    + f"'{object_name}' contains unexpected key '{unexpected_key}'"
                )

    # Ensure that expected log keys are present
    # - following keys should be present for all opencode.db message logs
    _validate_required_and_optional_keys(
        part,
        required_keys=[
            "time_created",
            "time_updated",
            "data",
            "id",
            "message_id",
            "session_id",
        ],
        optional_keys=[],
        object_name="part log",
    )

    # Ensure that expected data keys are present
    # - keys here are tool type dependent, but all should have "type"
    part_data = part.get("data")
    if isinstance(part_data, dict):
        _validate_required_and_optional_keys(
            part_data,
            required_keys=["type"],
            optional_keys=[],
            object_name="part data",
            prohibit_unexpected_keys=False,
        )
    elif "data" in part:
        validation_errors.append(
            validation_prefix + "'part data' expected to be a dictionary"
        )

    # Ensure that "data"->"type" reflects the values that we expect (and type-specific values)
    EXPECTED_TYPE_VALUES = ["reasoning", "text", "tool"]
    part_type = part_data.get("type") if isinstance(part_data, dict) else None
    if part_type is not None and part_type not in EXPECTED_TYPE_VALUES:
        validation_errors.append(
            validation_prefix + f"unexpected part type present in log data: {part_type}"
        )

    if part_type == "text":
        # required keys in part["data"]:
        #   - "text" (str)
        # optional keys:
        #   - "time": {"start" (int), "end" (int)}
        #   - "metadata" {... model provider specific ...}
        _validate_required_and_optional_keys(
            part_data,
            required_keys=["type", "text"],
            optional_keys=["time", "metadata"],
            object_name='part["data"]',
        )

    elif part_type == "reasoning":
        # required keys in part["data"]:
        #   - "text" (str)
        #   - "time": {"start" (int), "end" (int)}
        # optional keys:
        #   - "metadata" {... model provider specific ...}
        _validate_required_and_optional_keys(
            part_data,
            required_keys=["type", "text", "time"],
            optional_keys=["metadata"],
            object_name='part["data"]',
        )
    elif part_type == "tool":
        # required keys in part["data"]:
        #   - "callID" (str)
        #   - "tool" (str) -- examples: read, ftprofile-stats-server_get_ftprofile_structure, ...
        #   - "state": {"start" (int), "end" (int)}
        # optional keys:
        #   - metadata {... model provider specific ...}

        # required keys in part["data"]["state"]:
        #   - "status" (str)
        #   - "input": {... tool specific input parameters ...}
        #   - "output": (str)
        #   - "title": (str)
        #   - "metadata": {... tool specific metadata ...}
        #   - "time": {"start" (int), "end" (int)}
        # optional keys:
        #   - "attachments": (List)

        _validate_required_and_optional_keys(
            part_data,
            required_keys=["type", "callID", "tool", "state"],
            optional_keys=["metadata"],
            object_name='part["data"]',
        )

        tool_state = part_data.get("state")
        if isinstance(tool_state, dict):
            if "error" in tool_state:
                _validate_required_and_optional_keys(
                tool_state,
                required_keys=[
                    "error",
                    "status",
                    "input",
                    "time",
                ],
                optional_keys=["attachments", "metadata", "title"],
                object_name='part["data"]["state"]',
            )
            else:
                _validate_required_and_optional_keys(
                    tool_state,
                    required_keys=[
                        "status",
                        "input",
                        "output",
                        "title",
                        "metadata",
                        "time",
                    ],
                    optional_keys=["attachments"],
                    object_name='part["data"]["state"]',
                )

    # Ensure that the following keys don't have data in their values
    # - more so a sanity check for opencode's logging behavior
    for log_key in ["time_created", "time_updated"]:
        log_value = part.get(log_key)
        if isinstance(log_value, str) and len(log_value) > 0:
            validation_errors.append(
                validation_prefix + f"'{log_key}' value expected to be empty string"
            )

    if len(validation_errors) > 0:
        print("\n======== Validation errors encountered ==========")
        for ix, error in enumerate(validation_errors):
            print(f"\t{ix + 1}) {error}")
        print("\nPart log:")
        print(part)
        raise AttributeError("Log unable to be correctly validated.")


def _clean_part_log(
    part_log: Dict[str, Union[str, Dict]],
    message_log: Dict[str, Union[str, Dict]],
    truncate_step_output: bool = False,
    max_step_output_chars: int = DEFAULT_MAX_STEP_OUTPUT_CHARS,
) -> Dict[str, Union[str, Dict]]:
    """Normalize a raw part row into a simplified input/output span representation."""
    _validate_part_log(part_log)

    message_role = message_log["role"]

    # create new part log object with simplified structure
    part_data = part_log["data"]
    part_type = part_data["type"]

    new_part_log = {
        "metadata": {
            "part_id": part_log["id"],
            "message_id": part_log["message_id"],
            "session_id": part_log["session_id"],
            "time_created_unix_ms": None,
        },
        "metrics": {},
        "type": part_type,
        "input": {},
        "output": {},
    }

    # type-specific parsing
    if part_type == "text":
        if message_role == "user":
            new_part_log["input"]["text"] = part_data["text"]
        else:
            new_part_log["output"]["text"] = part_data["text"]

    elif part_type == "reasoning":
        # ignore model-specific metadata -- consists of info that we can't use
        # -- log["data"]["metadata"]["anthropic"] = {"signature": ... encrypted ...} for minimax
        # -- log["data"]["metadata"]["openai"] = {"reasoningEncryptedContent": ... encrypted ..., "itemId": ... hash ...} for gpt models
        new_part_log["output"]["text"] = part_data["text"]

    elif part_type == "tool":
        # required keys in part["data"]:
        #   - "callID" (str)
        #   - "tool" (str) -- examples: read, ftprofile-stats-server_get_ftprofile_structure, ...
        #   - "state": {"start" (int), "end" (int)}
        # optional keys:
        #   - metadata {... model provider specific ...}

        # required keys in part["data"]["state"]:
        #   - "status" (str)
        #   - "input": {... tool specific input parameters ...}
        #   - "output": (str)
        #   - "title": (str)
        #   - "metadata": {... tool specific metadata ...}
        #   - "time": {"start" (int), "end" (int)}
        # optional keys:
        #   - "attachments": (List)
        tool_type = part_data["tool"]
        tool_state = part_data["state"]

        new_tool_input = {"tool_type": tool_type}
        tool_input = tool_state.get("input")
        if isinstance(tool_input, dict):
            new_tool_input.update(tool_input)
        elif tool_input is not None:
            new_tool_input["input"] = tool_input

        if "error" in tool_state:
            new_tool_output = {"text": tool_state.get("error")}
        else:
            new_tool_output = {"text": tool_state.get("output")}
        tool_metadata = tool_state.get("metadata")
        if isinstance(tool_metadata, dict):
            new_tool_output.update(tool_metadata)
        elif tool_metadata is not None:
            new_tool_output["metadata"] = tool_metadata

        new_part_log["input"] = new_tool_input
        new_part_log["output"] = new_tool_output



    # parse step timing (parse strategy changed based on tool type)
    if part_type == "tool":
        new_part_log["metadata"]["time_created_unix_ms"] = tool_state["time"]["start"]
        if "end" in tool_state["time"]:
            new_part_log["metrics"]["latency_ms"] = (
                tool_state["time"]["end"] - tool_state["time"]["start"]
            )
    else:
        if "time" not in part_data:
            new_part_log["metadata"]["time_created_unix_ms"] = message_log["metadata"][
                "time_created_unix_ms"
            ]
        else:
            new_part_log["metadata"]["time_created_unix_ms"] = part_data["time"][
                "start"
            ]
            if "end" in part_data["time"]:
                new_part_log["metrics"]["latency_ms"] = (
                    part_data["time"]["end"] - part_data["time"]["start"]
                )

    if truncate_step_output:
        new_part_log["output"], _ = _truncate_output_value(
            new_part_log["output"], max_step_output_chars
        )

    return new_part_log


def _update_message_logs_with_parts(
    cursor: sqlite3.Cursor,
    session_id: str,
    message_logs: List[Dict[str, Union[str, Dict]]],
    verbose: Optional[bool] = False,
    truncate_step_output: bool = False,
    max_step_output_chars: int = DEFAULT_MAX_STEP_OUTPUT_CHARS,
) -> Tuple[List[Dict[str, Union[str, Dict]]], Dict[str, str]]:
    """Attach cleaned, chronologically ordered part logs to each cleaned message log."""

    new_message_logs = []
    subagent_part_id_to_session_id = {}
    for message_log in message_logs:
        message_id = message_log["id"]

        if verbose:
            print(f"\n======== Message id: {message_id}")
            print(message_log)

        message_log = _clean_message_log(message_log)
        part_logs = _get_part_logs(cursor, session_id, message_id, verbose)
        part_logs = _filter_part_logs(part_logs)
        part_logs = [
            _clean_part_log(
                part_log,
                message_log,
                truncate_step_output=truncate_step_output,
                max_step_output_chars=max_step_output_chars,
            )
            for part_log in part_logs
        ]

        if verbose:
            for part_log in part_logs:
                print("----")
                print(part_log)

        # make sure parts are sorted in chronological order
        part_logs.sort(key=lambda part: part["metadata"]["time_created_unix_ms"])
        message_log["parts"] = part_logs
        new_message_logs.append(message_log)

        # TODO: refactor into separate function
        for part in part_logs:
            part_type = part.get("type")
            if part_type == "tool":
                tool_input = part.get("input")
                tool_type = tool_input.get("tool_type")
                if tool_type == "task":
                    # signifies that this is an agent call
                    part_metadata = part.get("metadata")
                    part_id = part_metadata.get("part_id")

                    tool_output = part.get("output")

                    subagent_session_id = tool_output.get("sessionId")
                    if subagent_session_id is not None:
                        subagent_part_id_to_session_id[part_id] = subagent_session_id
    return new_message_logs, subagent_part_id_to_session_id


# endregion opencode.db retrieval functions


def _process_logs_for_opik(
    message_logs: List[Dict[str, Union[str, Dict]]],
) -> List[Dict]:  # TODO: output format typing
    """Package opencode logs into a structure best suited for Opik's expected structures: threads/traces/spans"""

    user_message_indices = [
        ix for ix, msg in enumerate(message_logs) if msg["role"] == "user"
    ]
    n_messages = len(message_logs)

    trace_logs = []
    for turn_index, start_ix in enumerate(user_message_indices, start=1):
        user_msg = message_logs[start_ix]
        user_parts = user_msg.get("parts")
        if not isinstance(user_parts, list) or len(user_parts) == 0:
            raise ValueError(
                "User message must have at least one part containing the prompt"
            )

        trace_input = _get_first_user_text(user_msg)
        if not trace_input:
            raise ValueError(
                "User message is missing prompt text in parts (expected a part with input.text)"
            )

        session_id = user_msg["metadata"]["session_id"]
        trace_start_time = _unix_ms_to_datetime(
            user_msg["metadata"]["time_created_unix_ms"]
        )
        trace_spans = []
        assistant_messages = []
        trace_log = {
            "input": trace_input,
            "output": {},
            "start_time": trace_start_time,
            "end_time": None,
            "metadata": {},
        }

        last_message_ix = None
        for msg_ix in range(start_ix + 1, n_messages):
            if msg_ix in user_message_indices:
                break
            last_message_ix = msg_ix
            msg = message_logs[msg_ix]
            assistant_messages.append(msg)

            provider = msg["metadata"]["model"]["providerID"]
            model = msg["metadata"]["model"]["modelID"]
            span_metadata = _metadata_with_metrics(msg["metadata"], msg["metrics"])

            usage_metrics = {}

            # map first three keys below from the standard opencode naming format into UI-expected Opik format
            #   - rest of keys aren't visible in Opik UI, but renamed to fit Opik naming conventions
            usage_key_map = [
                ("tokens_total", "total_tokens"),
                ("tokens_input", "prompt_tokens"),
                ("tokens_output", "completion_tokens"),
                ("tokens_reasoning", "reasoning_tokens"),
                ("tokens_cache_read", "cache_read_tokens"),
                ("tokens_cache_write", "cache_write_tokens")
            ]
            for old_key, new_key in usage_key_map:
                if old_key in msg["metrics"]:
                    usage_metrics[new_key] = msg["metrics"][old_key]

            span = {
                "provider": provider,
                "model": model,
                "start_time": _unix_ms_to_datetime(
                    msg["metadata"]["time_created_unix_ms"]
                ),
                "end_time": _build_end_time(
                    msg["metadata"]["time_created_unix_ms"],
                    msg["metrics"].get("latency_ms"),
                ),
                "input": {},
                "output": {},
                "usage": usage_metrics,
                # name="llm_call",
                # type="llm",  # “general”, “tool”, “llm”, “guardrail”
                "tags": [],  # TODO: tags?
                "metadata": span_metadata,
            }

            if "cost_dollars" in msg["metrics"]:
                span["total_cost"] = float(msg["metrics"]["cost_dollars"])

            sub_spans = []

            for part in msg["parts"]:
                part_metadata = _metadata_with_metrics(
                    part["metadata"], part["metrics"]
                )

                sub_span = {
                    "provider": provider,
                    "model": model,
                    "start_time": _unix_ms_to_datetime(
                        part["metadata"]["time_created_unix_ms"]
                    ),
                    "end_time": _build_end_time(
                        part["metadata"]["time_created_unix_ms"],
                        part["metrics"].get("latency_ms"),
                    ),
                    "input": part["input"],
                    "output": part["output"],
                    "metadata": part_metadata,
                }

                sub_span_tags = [part["type"]]
                if part["type"] == "reasoning":
                    sub_span_type = "llm"
                elif part["type"] == "text":
                    sub_span_type = "llm"
                elif part["type"] == "tool":
                    sub_span_type = "tool"
                    sub_span_tags.append(part["input"]["tool_type"])
                else:
                    sub_span_type = "general"

                sub_span["tags"] = sub_span_tags
                sub_span["type"] = sub_span_type

                sub_spans.append(sub_span)

            span["spans"] = sub_spans
            trace_spans.append(span)

        if last_message_ix is None:
            # Sessions can end with a user message or include consecutive user messages.
            # In those cases, emit an empty trace rather than crashing.
            trace_log["metadata"] = _build_turn_metadata(
                session_id=session_id,
                turn_index=turn_index,
                user_message=user_msg,
                assistant_messages=[],
            )
            trace_log["end_time"] = trace_start_time
            trace_log["output"] = {}
            trace_log["spans"] = []
            trace_logs.append(trace_log)
            continue

        last_message = message_logs[last_message_ix]
        trace_log["metadata"] = _build_turn_metadata(
            session_id=session_id,
            turn_index=turn_index,
            user_message=user_msg,
            assistant_messages=assistant_messages,
        )
        trace_end_time = _build_end_time(
            last_message["metadata"]["time_created_unix_ms"],
            last_message["metrics"].get("latency_ms"),
        )
        trace_output = _get_last_assistant_output(last_message)

        trace_log["end_time"] = trace_end_time
        trace_log["output"] = trace_output
        trace_log["spans"] = trace_spans

        # TODO: trace -- define metadata setup

        # TODO: trace -- define trace tags?

        trace_logs.append(trace_log)

    return trace_logs


def _aggregate_trace_usage(trace_log: Dict[str, Any]) -> Dict[str, Any]:
    """Aggregate parent-span usage into a trace-level usage payload."""
    usage_totals = {
        "original_usage.total_tokens": 0,
        "original_usage.prompt_tokens": 0,
        "original_usage.completion_tokens": 0,
        "original_usage.reasoning_tokens": 0,
        "original_usage.cache_read_tokens": 0,
        "original_usage.cache_write_tokens": 0,
    }
    for parent_span in trace_log.get("spans", []):
        parent_usage = parent_span.get("usage", {})
        for trace_key, parent_key in [
            ("original_usage.total_tokens", "total_tokens"),
            ("original_usage.prompt_tokens", "prompt_tokens"),
            ("original_usage.completion_tokens", "completion_tokens"),
            ("original_usage.reasoning_tokens", "reasoning_tokens"),
            ("original_usage.cache_read_tokens", "cache_read_tokens"),
            ("original_usage.cache_write_tokens", "cache_write_tokens"),
        ]:
            usage_totals[trace_key] += int(parent_usage.get(parent_key, 0) or 0)
    return usage_totals


def _build_parent_span_name(agent_name: str, step_ix: int) -> str:
    """Return the display name used for a top-level reasoning step span."""
    return f"[{agent_name}: reasoning step {step_ix + 1}]"


def _build_child_span_name(sub_span: Dict[str, Any], output_label: str) -> str:
    """Return the display name used for a child span."""
    child_part_id = sub_span.get("metadata", {}).get("part_id", "#TODO")

    if sub_span["type"] == "llm":
        llm_output = sub_span.get("output", {}).get("text", "")
        is_reasoning_type = "reasoning" in sub_span.get("tags", [])

        if is_reasoning_type:
            if llm_output == "":
                return "Reasoning [unavailable]"
            header_text = llm_output.split("\n")[0]
            if header_text.startswith("**") and header_text.endswith("**"):
                return f"Reasoning [{header_text.replace('**', '')}]"
            return "Reasoning [ ... ]"
        return output_label

    if sub_span["type"] == "tool":
        tool_type = sub_span["input"]["tool_type"]
        if tool_type == "read":
            read_path = sub_span["input"]["filePath"].split("/")[-1]
            return f"Read: /{read_path}"
        if tool_type == "grep":
            grep_matches = sub_span["output"]["matches"]
            return f"Grep: {grep_matches} match" + ("" if grep_matches == 1 else "es")
        if tool_type == "glob":
            return f"Glob: {sub_span['input']['pattern']}"
        if tool_type == "bash":
            bash_description = sub_span["input"].get("description") or sub_span["input"].get(
                "command", ""
            )
            return f"Bash: {bash_description}"
        if tool_type == "task":
            return (
                f"Subagent ({sub_span['input']['subagent_type']}): "
                f"{sub_span['input']['description']}"
            )
        return f"Tool: {tool_type}"

    return str(child_part_id)


def _append_flat_trace_spans(
    flat_spans: List[Dict[str, Any]],
    span_parent_id: Optional[str],
    trace_spans: List[Dict[str, Any]],
    output_label: str,
) -> List[Dict[str, str]]:
    """Append nested span data to a flat span list and return queued subagent tasks."""
    subagent_queue = []
    for step_ix, parent_span in enumerate(trace_spans):
        parent_id = _new_local_span_id()
        parent_record = {
            "id": parent_id,
            "parent_span_id": span_parent_id,
            "name": _build_parent_span_name(parent_span["metadata"]["agent"], step_ix),
            "tags": parent_span["tags"],
            "type": "general",
            "provider": parent_span["provider"],
            "model": parent_span["model"],
            "start_time": parent_span["start_time"],
            "end_time": parent_span["end_time"],
            "input": parent_span["input"],
            "output": parent_span["output"],
            "usage": parent_span["usage"],
            "total_cost": parent_span.get("total_cost"),
            "metadata": parent_span["metadata"],
        }
        flat_spans.append(parent_record)

        for sub_span in parent_span["spans"]:
            child_uuid = _new_local_span_id()
            child_part_id = sub_span.get("metadata", {}).get("part_id")
            if child_part_id in (None, "#TODO"):
                child_part_id = "#TODO"

            if child_part_id is not None:
                maybe_session_id = sub_span.get("metadata", {}).get("session_id")
                if (
                    sub_span["type"] == "tool"
                    and sub_span["input"].get("tool_type") == "task"
                    and maybe_session_id is not None
                ):
                    pass

            child_record = {
                "id": child_uuid,
                "parent_span_id": parent_id,
                "name": _build_child_span_name(sub_span, output_label),
                "tags": sub_span["tags"],
                "type": sub_span["type"],
                "provider": sub_span["provider"],
                "model": sub_span["model"],
                "start_time": sub_span["start_time"],
                "end_time": sub_span["end_time"],
                "input": sub_span["input"],
                "output": sub_span["output"],
                "metadata": sub_span["metadata"],
            }
            flat_spans.append(child_record)

            if (
                sub_span["type"] == "tool"
                and sub_span["input"].get("tool_type") == "task"
                and isinstance(child_part_id, str)
            ):
                subagent_queue.append(
                    {
                        "uuid": child_uuid,
                        "part_id": child_part_id,
                    }
                )

    return subagent_queue


def _build_local_trace_record(
    session_id: str,
    trace_log: Dict[str, Any],
    thread_id: str,
) -> Dict[str, Any]:
    """Return a local trace record mirroring the information logged to Opik."""
    duration_ms = None
    if trace_log["start_time"] is not None and trace_log["end_time"] is not None:
        duration_ms = int(
            (trace_log["end_time"] - trace_log["start_time"]).total_seconds() * 1000
        )

    return {
        "id": f"{session_id}:turn_{trace_log['metadata']['turn_index']}",
        "session_id": session_id,
        "name": f"turn_{trace_log['metadata']['turn_index']}",
        "thread_id": thread_id,
        "input": trace_log["input"],
        "output": trace_log["output"],
        "start_time": trace_log["start_time"],
        "end_time": trace_log["end_time"],
        "duration": duration_ms,
        "usage": _aggregate_trace_usage(trace_log),
        "metadata": trace_log["metadata"],
        "spans": [],
    }


def _build_export_trace_records(
    cursor: sqlite3.Cursor,
    session_id: str,
    message_logs_opik: List[Dict[str, Any]],
    subagent_id_to_session: Dict[str, str],
    verbose: bool,
    truncate_step_output: bool = False,
    max_step_output_chars: int = DEFAULT_MAX_STEP_OUTPUT_CHARS,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Build local trace records matching the Opik export structure."""
    trace_records = []
    subagent_logs = {}
    thread_id = session_id

    for trace_log in message_logs_opik:
        trace_record = _build_local_trace_record(session_id, trace_log, thread_id)
        trace_spans = trace_record["spans"]
        subagent_queue = _append_flat_trace_spans(
            flat_spans=trace_spans,
            span_parent_id=None,
            trace_spans=trace_log["spans"],
            output_label="Agent Output",
        )

        for queued_subagent in subagent_queue:
            subagent_session_id = subagent_id_to_session.get(queued_subagent["part_id"])
            if subagent_session_id is None:
                continue

            subagent_message_list = _get_message_logs(
                cursor=cursor, session_id=subagent_session_id, verbose=verbose
            )
            subagent_message_list, nested_subagent_id_to_session = _update_message_logs_with_parts(
                cursor=cursor,
                session_id=subagent_session_id,
                message_logs=subagent_message_list,
                verbose=verbose,
                truncate_step_output=truncate_step_output,
                max_step_output_chars=max_step_output_chars,
            )

            subagent_logs[queued_subagent["part_id"]] = subagent_message_list
            subagent_trace_logs = _process_logs_for_opik(subagent_message_list)
            for subagent_trace_log in subagent_trace_logs:
                _append_flat_trace_spans(
                    flat_spans=trace_spans,
                    span_parent_id=queued_subagent["uuid"],
                    trace_spans=subagent_trace_log["spans"],
                    output_label="Subagent Output",
                )

        trace_records.append(trace_record)

    return trace_records, subagent_logs


def _write_local_export_bundle(
    root: Union[str, Path],
    workspace: str,
    project: str,
    session_id: str,
    export_bundle: Dict[str, Any],
) -> Path:
    """Write a session export bundle to the local backend."""
    project_dir = _local_project_dir(root, workspace, project)
    sessions_dir = project_dir / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)

    output_path = sessions_dir / f"{session_id}.json"
    with output_path.open("w") as file_handle:
        json.dump(
            export_bundle,
            file_handle,
            indent=4,
            default=_json_default_serializer,
        )
    return output_path


def _write_json_export_bundle(output_path: Union[str, Path], export_bundle: Dict[str, Any]) -> Path:
    """Write a session export bundle to a specific JSON file path."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w") as file_handle:
        json.dump(
            export_bundle,
            file_handle,
            indent=4,
            default=_json_default_serializer,
        )
    return output_path


def _default_json_output_path(session_id: str) -> Path:
    """Return the default JSON export path for a session trajectory bundle."""
    return DEFAULT_SESSION_TRAJECTORIES_DIR / f"{session_id}_export.json"


def _truncate_output_string(value: str, max_chars: int) -> Tuple[str, bool]:
    """Return a truncated string plus a flag indicating whether truncation occurred."""
    if len(value) <= max_chars:
        return value, False

    if max_chars <= len(TRUNCATED_OUTPUT_SUFFIX):
        return TRUNCATED_OUTPUT_SUFFIX[:max_chars], True
    return value[: max_chars - len(TRUNCATED_OUTPUT_SUFFIX)] + TRUNCATED_OUTPUT_SUFFIX, True


def _truncate_output_value(value: Any, max_chars: int) -> Tuple[Any, bool]:
    """Recursively truncate long string values inside an output payload."""
    if isinstance(value, str):
        return _truncate_output_string(value, max_chars)
    if isinstance(value, list):
        truncated_items = []
        was_truncated = False
        for item in value:
            truncated_item, item_was_truncated = _truncate_output_value(item, max_chars)
            truncated_items.append(truncated_item)
            was_truncated = was_truncated or item_was_truncated
        return truncated_items, was_truncated
    if isinstance(value, dict):
        truncated_dict = {}
        was_truncated = False
        for key, item in value.items():
            truncated_item, item_was_truncated = _truncate_output_value(item, max_chars)
            truncated_dict[key] = truncated_item
            was_truncated = was_truncated or item_was_truncated
        if was_truncated:
            truncated_dict["truncate"] = True
        return truncated_dict, was_truncated
    return value, False


def get_opencode_logs(
    session_id: str,
    opik_api_key: Optional[str],
    opik_workspace: str,
    opik_project: str,
    save_as_json: Optional[bool] = False,
    json_output_path: Optional[Union[str, Path]] = None,
    verbose: Optional[bool] = False,
    log_local: Optional[bool] = False,
    local_logs_root: Union[str, Path] = DEFAULT_LOCAL_LOGS_ROOT,
    truncate_step_output: bool = False,
    max_step_output_chars: int = DEFAULT_MAX_STEP_OUTPUT_CHARS,
):
    """Load one OpenCode session from the local database and export it.

    The function reads message and part rows from `opencode.db`, reshapes them
    into a thread/trace/span payload, and either logs them to Opik or saves
    them to the local backend before returning.

    Args:
        session_id (str): OpenCode session identifier to load from the local database.
        opik_api_key (Optional[str]): API key used to authenticate with Opik.
        opik_workspace (str): Opik workspace that should receive the logged data.
        opik_project (str): Opik project name used when creating the client or local export path.
        save_as_json (Optional[bool]): If True, save collected logs as a json.
            -- saves to "session_trajectories/{session_id}_export.json"
        json_output_path (Optional[Union[str, Path]]): Optional explicit path for the
            exported json bundle. If provided, this path is used instead of the default
            path under "session_trajectories/".
        verbose (Optional[bool]): If True, print raw and transformed logs during processing.
        log_local (Optional[bool]): If True, bypass Opik and write to the local backend.
        local_logs_root (Union[str, Path]): Root directory used for local exports.
        truncate_step_output (bool): If True, truncate oversized step outputs before export.
        max_step_output_chars (int): Maximum characters allowed per string value inside a
            step output before it is truncated.
    """

    # get opencode.db path
    opencode_db_path = _get_opencode_db_path()

    with sqlite3.connect(str(opencode_db_path)) as conn:
        cur = conn.cursor()

        # retrieve logs: messages, message parts (e.g. tool calls)
        message_info_list = _get_message_logs(
            cursor=cur, session_id=session_id, verbose=verbose
        )
        message_info_list, subagent_id_to_session = _update_message_logs_with_parts(
            cursor=cur,
            session_id=session_id,
            message_logs=message_info_list,
            verbose=verbose,
            truncate_step_output=truncate_step_output,
            max_step_output_chars=max_step_output_chars,
        )

        message_logs_opik = _process_logs_for_opik(message_info_list)
        trace_records, subagent_logs = _build_export_trace_records(
            cursor=cur,
            session_id=session_id,
            message_logs_opik=message_logs_opik,
            subagent_id_to_session=subagent_id_to_session,
            verbose=bool(verbose),
            truncate_step_output=truncate_step_output,
            max_step_output_chars=max_step_output_chars,
        )

        if log_local:
            export_bundle = {
                "backend": "local",
                "workspace": opik_workspace,
                "project": opik_project,
                "thread_id": session_id,
                "session": _get_session_logs(cursor=cur, session_id=session_id, verbose=verbose),
                "messages": message_logs_opik,
                "turns": message_logs_opik,
                "traces": trace_records,
                "subagent_sessions": subagent_logs,
            }
            _write_local_export_bundle(
                root=local_logs_root,
                workspace=opik_workspace,
                project=opik_project,
                session_id=session_id,
                export_bundle=export_bundle,
            )
        else:
            if not opik_api_key:
                raise ValueError("opik_api_key is required unless --local-only is used.")

            from opik import Opik, configure

            configure(api_key=opik_api_key, workspace=opik_workspace, force=True)
            client = Opik(project_name=opik_project)
            for trace_record in trace_records:
                active_trace = client.trace(
                    name=trace_record["name"],
                    input=trace_record["input"],
                    output=trace_record["output"],
                    start_time=trace_record["start_time"],
                    end_time=trace_record["end_time"],
                    metadata=trace_record["metadata"],
                    thread_id=trace_record["thread_id"],
                )
                for span_record in trace_record["spans"]:
                    active_trace.span(
                        id=span_record["id"],
                        parent_span_id=span_record["parent_span_id"],
                        name=span_record["name"],
                        tags=span_record["tags"],
                        type=span_record.get("type"),
                        provider=span_record["provider"],
                        model=span_record["model"],
                        start_time=span_record["start_time"],
                        end_time=span_record["end_time"],
                        input=span_record["input"],
                        output=span_record["output"],
                        usage=span_record.get("usage"),
                        total_cost=span_record.get("total_cost"),
                        metadata=span_record["metadata"],
                    )
            client.flush()

        # optionally, save logs to a local json file
        if save_as_json or json_output_path:
            logs = {
                "session": _get_session_logs(
                    cursor=cur, session_id=session_id, verbose=verbose
                ),
                "messages": message_logs_opik,
                "turns": message_logs_opik,
                "traces": trace_records,
                "subagent_sessions": subagent_logs,
            }
            output_path = json_output_path or _default_json_output_path(session_id)
            _write_json_export_bundle(output_path, logs)


def main() -> None:
    """Parse CLI arguments and start the OpenCode export."""
    parser = argparse.ArgumentParser(
        description="Load OpenCode session logs into Opik or local storage."
    )
    parser.add_argument("session_id", help="OpenCode session ID to load.")
    parser.add_argument("opik_key", nargs="?", help="Opik API key.")
    parser.add_argument("opik_workspace", nargs="?", help="Opik workspace name.")
    parser.add_argument("opik_project", nargs="?", help="Opik project name.")
    parser.add_argument("--workspace", dest="workspace_flag", help="Workspace name.")
    parser.add_argument("--project", dest="project_flag", help="Project name.")
    parser.add_argument(
        "--local-only",
        action="store_true",
        help="Bypass Opik and write the session export to local storage instead.",
    )
    parser.add_argument(
        "--local-logs-root",
        default=str(DEFAULT_LOCAL_LOGS_ROOT),
        help="Root directory used for local session exports.",
    )
    parser.add_argument(
        "--save_as_json",
        action="store_true",
        help="Save session logs to session_trajectories/<session_id>_export.json.",
    )
    parser.add_argument(
        "--json-output-path",
        help="Write the full session export bundle to this JSON file path.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print verbose log details during processing.",
    )
    parser.add_argument(
        "--truncate-step-output",
        action="store_true",
        help="Truncate oversized step outputs and mark them with truncate=true.",
    )
    parser.add_argument(
        "--max-step-output-chars",
        type=int,
        default=DEFAULT_MAX_STEP_OUTPUT_CHARS,
        help="Maximum characters allowed per string value inside a step output before truncation.",
    )
    args = parser.parse_args()

    # validate session_id: used for both input validation and to protect against sql injection
    args.session_id = args.session_id.strip()
    if not SESSION_ID_PATTERN.fullmatch(args.session_id):
        parser.error(
            "session_id must be a single ses_-prefixed token containing only letters and digits."
        )

    workspace = args.workspace_flag or args.opik_workspace
    project = args.project_flag or args.opik_project
    if not workspace or not project:
        parser.error("Provide a workspace and project via positional args or --workspace/--project.")
    if not args.local_only and not args.opik_key:
        parser.error("Provide an Opik API key unless --local-only is used.")
    if args.max_step_output_chars <= 0:
        parser.error("--max-step-output-chars must be greater than zero.")

    get_opencode_logs(
        args.session_id,
        opik_api_key=args.opik_key,
        opik_workspace=workspace,
        opik_project=project,
        save_as_json=args.save_as_json,
        json_output_path=args.json_output_path,
        verbose=args.verbose,
        log_local=args.local_only,
        local_logs_root=args.local_logs_root,
        truncate_step_output=args.truncate_step_output,
        max_step_output_chars=args.max_step_output_chars,
    )


if __name__ == "__main__":
    main()
