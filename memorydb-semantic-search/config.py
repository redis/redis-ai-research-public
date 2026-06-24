"""Configuration loader.

Resolution order for each value:
    1. environment variable (e.g. MEMORYDB_HOST)
    2. config.yaml in this directory
    3. config.example.yaml in this directory (so the code stays importable
       before the user creates their own config.yaml)

Usage:
    from config import cfg
    host = cfg.memorydb.host
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import yaml

_HERE = Path(__file__).resolve().parent
_USER_FILE = _HERE / "config.yaml"
_EXAMPLE_FILE = _HERE / "config.example.yaml"

# (yaml-dotted-key, env-var-name, cast)
_ENV_OVERRIDES = [
    ("memorydb.host", "MEMORYDB_HOST", str),
    ("memorydb.port", "MEMORYDB_PORT", int),
    ("memorydb.password", "MEMORYDB_PASSWORD", str),
    ("memorydb.tls", "MEMORYDB_TLS", lambda v: v.lower() in ("1", "true", "yes")),
    ("s3.bucket", "S3_BUCKET", str),
    ("s3.prefix", "S3_PREFIX", str),
    ("bastion.ip", "BASTION_IP", str),
    ("bastion.user", "BASTION_USER", str),
    ("bastion.ssh_key", "BASTION_SSH_KEY", str),
    ("server.host", "SERVER_HOST", str),
    ("server.port", "SERVER_PORT", int),
    ("server.pool_size", "POOL_SIZE", int),
    ("aws.region", "AWS_REGION", str),
]


def _set_dotted(d: dict, key: str, value) -> None:
    parts = key.split(".")
    cur = d
    for p in parts[:-1]:
        cur = cur.setdefault(p, {})
    cur[parts[-1]] = value


def _to_ns(obj):
    if isinstance(obj, dict):
        return SimpleNamespace(**{k: _to_ns(v) for k, v in obj.items()})
    return obj


def _load() -> SimpleNamespace:
    path = _USER_FILE if _USER_FILE.exists() else _EXAMPLE_FILE
    with open(path) as fh:
        data = yaml.safe_load(fh) or {}

    for key, env, cast in _ENV_OVERRIDES:
        raw = os.environ.get(env)
        if raw is not None and raw != "":
            try:
                _set_dotted(data, key, cast(raw))
            except (TypeError, ValueError):
                _set_dotted(data, key, raw)

    return _to_ns(data)


cfg = _load()
