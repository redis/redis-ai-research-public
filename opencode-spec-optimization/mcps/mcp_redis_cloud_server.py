import os
from pathlib import Path
from typing import Any

import redis
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field


def _load_env_file(path: Path) -> dict[str, str]:
    if not path.exists() or not path.is_file():
        return {}

    out: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            out[key] = value
    return out


def _env(name: str, default: str, file_env: dict[str, str]) -> str:
    v = os.getenv(name)
    if v is not None and v != "":
        return v
    v = file_env.get(name)
    if v is not None and v != "":
        return v
    return default


def _init_redis_client() -> redis.Redis:
    # Read config once at process startup so tools don't re-init
    # the client on every call.
    file_env: dict[str, str] = {}
    file_env |= _load_env_file(Path(".venv"))
    file_env |= _load_env_file(Path(".venv") / ".env")
    file_env |= _load_env_file(Path(".env"))

    host = _env("REDIS_HOST", "localhost", file_env)
    port = int(_env("REDIS_PORT", "6379", file_env))
    username = _env("REDIS_USERNAME", "default", file_env)
    password = _env("REDIS_PASSWORD", "", file_env)

    # redis.Redis uses a connection pool internally; constructing it once
    # and reusing it is the intended pattern.
    return redis.Redis(
        host=host,
        port=port,
        username=username,
        password=password,
        decode_responses=True,
    )


def _pair_list_to_dict(v: Any) -> Any:
    # RedisSearch returns FT.INFO as an alternating key/value list.
    if isinstance(v, list) and len(v) % 2 == 0 and all(
        isinstance(v[i], (str, bytes)) for i in range(0, len(v), 2)
    ):
        out: dict[str, Any] = {}
        for i in range(0, len(v), 2):
            k = v[i].decode() if isinstance(v[i], (bytes, bytearray)) else str(v[i])
            out[k] = _pair_list_to_dict(v[i + 1])
        return out
    if isinstance(v, list):
        return [_pair_list_to_dict(x) for x in v]
    if isinstance(v, (bytes, bytearray)):
        return v.decode()
    return v


mcp = FastMCP("redis-cloud")

_REDIS_CLIENT = _init_redis_client()


class FTInfoResult(BaseModel):
    """Structured response for the `ft_info` tool (FT.INFO)."""

    result_type: str = Field(
        default="ft.info",
        description="Discriminator to indicate this payload comes from FT.INFO.",
    )
    index_name: str = Field(description="The RediSearch index name passed to FT.INFO.")
    info: dict[str, Any] = Field(
        description="Parsed FT.INFO reply (alternating key/value list converted to a dict)."
    )


@mcp.tool()
def ft_info(index_name: str) -> FTInfoResult:
    """Return FT.INFO output for a RediSearch index.
    Use this to get the information about the index.
    """
    res = _REDIS_CLIENT.execute_command("FT.INFO", index_name)
    info = _pair_list_to_dict(res)
    return FTInfoResult(index_name=index_name, info=info)


@mcp.tool()
def slowlog_get(count: int = 10) -> dict[str, Any]:
    """Return SLOWLOG entries (and total length)."""
    length = _REDIS_CLIENT.execute_command("SLOWLOG", "LEN")
    entries = _REDIS_CLIENT.execute_command("SLOWLOG", "GET", int(count))
    parsed = []
    for e in entries:
        # [id, unix_ts, duration_us, [cmd, arg1, ...], [optional additional fields]]
        parsed.append(
            {
                "id": e[0],
                "unix_time": e[1],
                "duration_us": e[2],
                "command": e[3],
                "extra": e[4:] if len(e) > 4 else [],
            }
        )
    return {"len": length, "entries": parsed}


@mcp.tool()
def ft_profile(
    index_name: str,
    kind: str,
    query: str,
    args: list[str] | None = None,
    limited: bool = False,
) -> dict[str, Any]:
    """Run FT.PROFILE for SEARCH or AGGREGATE.

    kind: "SEARCH" or "AGGREGATE"
    args: appended after QUERY <query> (e.g. ["NOCONTENT", "LIMIT", "0", "10"]).
    limited: if true, adds the LIMITED flag.
    """
    k = (kind or "").upper().strip()
    if k not in {"SEARCH", "AGGREGATE"}:
        raise ValueError("kind must be 'SEARCH' or 'AGGREGATE'")

    argv: list[str] = ["FT.PROFILE", index_name, k]
    if limited:
        argv.append("LIMITED")
    argv.extend(["QUERY", query])

    if args:
        argv.extend(args)
    else:
        if k == "SEARCH":
            argv.extend(["NOCONTENT", "LIMIT", "0", "10"])
        else:
            argv.extend(["GROUPBY", "0", "REDUCE", "COUNT", "0", "AS", "count"])

    res = _REDIS_CLIENT.execute_command(*argv)
    # Response format: [results, profile]
    return {"argv": argv, "results": res[0], "profile": res[1]}


@mcp.tool()
def redis_execute(command: str, args: list[str] | None = None) -> Any:
    """Execute an arbitrary Redis command and return argv + result.
    Run arbitrary Redis commands. Use this for commands such as FT.SEARCH, FT.AGGREGATE, FT.EXPLAIN, etc.
    Do not use this for FT.INFO, FT.PROFILE, SLOWLOG_GET since those are specialized tools."""
    argv = [command]
    if args:
        argv.extend(args)
    result = _REDIS_CLIENT.execute_command(*argv)
    return {"executed_command": " ".join(argv), "result": result}


if __name__ == "__main__":
    mcp.run()
