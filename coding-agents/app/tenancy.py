"""Tenancy layer.

Everything that makes the agent *per-tenant* lives here:

  * Tenant            - immutable description of one customer org.
  * TenantRegistry    - maps a presented API key -> Tenant.
  * resolve_local_workspace / resolve_workspace_override - which real host
    directory a tenant's agent works in, and whether a requested override
    is allowed (allowed_roots / ALLOW_ANY_WORKSPACE_PATH).

Execution model: direct host execution — no sandbox. The agent's tools run in
the service process, confined to the tenant's workspace directory by path
checks in app/agent.py. Process-level isolation is handled outside this
service.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .config import Settings


@dataclass(frozen=True)
class Tenant:
    id: str
    name: str
    # "local_dir": workspace_source is a directory path (absolute, or relative
    #              to settings.workspaces_root).
    # "git_repo":  workspace_source is "owner/repo" cloned at git_ref.
    workspace_kind: str
    workspace_source: str
    git_ref: str = "main"
    # Optional per-tenant overrides.
    model: Optional[str] = None
    openai_api_key: Optional[str] = None
    max_turns: Optional[int] = None
    # Host paths under which this tenant is allowed to point the agent via the
    # request-level `workspace_path` override. Paths (and their symlinks) must
    # resolve under one of these. Empty means overrides are refused unless the
    # ALLOW_ANY_WORKSPACE_PATH dev flag is set.
    allowed_roots: list[str] = field(default_factory=list)


def _hash_key(api_key: str) -> str:
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


# Demo tenants used only when no tenants_file is configured, so the service
# runs out of the box. Replace with a real store (DB / secrets manager) in prod.
_DEMO_TENANTS = [
    {
        "id": "acme",
        "name": "Acme Corp",
        "api_key": "demo-key-acme",
        "workspace_kind": "local_dir",
        "workspace_source": "acme",
    },
    {
        "id": "globex",
        "name": "Globex Inc",
        "api_key": "demo-key-globex",
        "workspace_kind": "local_dir",
        "workspace_source": "globex",
    },
]


class TenantRegistry:
    """Resolves API keys to tenants.

    Keys are matched by SHA-256 hash so plaintext keys never need to be kept in
    memory once loaded. A tenants file entry may provide either:
        "api_key":         a plaintext key (convenient for dev), or
        "api_key_sha256":  the precomputed hash (preferred for prod).
    """

    def __init__(self, records: list[dict]) -> None:
        self._by_hash: dict[str, Tenant] = {}
        for rec in records:
            if "api_key_sha256" in rec:
                key_hash = rec["api_key_sha256"]
            elif "api_key" in rec:
                key_hash = _hash_key(rec["api_key"])
            else:
                raise ValueError(
                    f"Tenant {rec.get('id')!r} has no api_key or api_key_sha256"
                )
            tenant = Tenant(
                id=rec["id"],
                name=rec.get("name", rec["id"]),
                workspace_kind=rec.get("workspace_kind", "local_dir"),
                workspace_source=rec.get("workspace_source", rec["id"]),
                git_ref=rec.get("git_ref", "main"),
                model=rec.get("model"),
                openai_api_key=rec.get("openai_api_key"),
                max_turns=rec.get("max_turns"),
                allowed_roots=list(rec.get("allowed_roots", [])),
            )
            self._by_hash[key_hash] = tenant

    @classmethod
    def from_settings(cls, settings: Settings) -> "TenantRegistry":
        if settings.tenants_file:
            data = json.loads(Path(settings.tenants_file).read_text("utf-8"))
            records = data["tenants"] if isinstance(data, dict) else data
            return cls(records)
        return cls(_DEMO_TENANTS)

    def authenticate(self, presented_key: str) -> Optional[Tenant]:
        if not presented_key:
            return None
        # Dict lookup keyed by the hash of the presented key. The stored keys
        # are hashes, so this does not leak timing about the secret itself.
        return self._by_hash.get(_hash_key(presented_key))


def resolve_local_workspace(tenant: Tenant, settings: Settings) -> Path:
    """Return (creating if needed) the host directory for a local_dir tenant."""
    src = Path(tenant.workspace_source)
    path = src if src.is_absolute() else Path(settings.workspaces_root) / src
    path.mkdir(parents=True, exist_ok=True)
    return path


class WorkspacePathError(ValueError):
    """Requested workspace_path is not allowed for this tenant."""


def resolve_workspace_override(
    tenant: Tenant, requested: str, settings: Settings
) -> Path:
    """Resolve and authorize a per-request workspace_path.

    Rules, in order:
      1. The path (and any symlinks) must resolve to a real, existing directory.
      2. If ALLOW_ANY_WORKSPACE_PATH is on, that's enough — dev mode.
      3. Otherwise the resolved path must be under one of `tenant.allowed_roots`
         (also symlink-resolved). Root list empty -> refused.

    We use Path.resolve(strict=True) so symlinks are followed before the
    subpath check, closing the "symlink inside allowed_root that points out"
    escape.
    """
    try:
        target = Path(requested).expanduser().resolve(strict=True)
    except FileNotFoundError as exc:
        raise WorkspacePathError(
            f"workspace_path does not exist: {requested!r}"
        ) from exc
    if not target.is_dir():
        raise WorkspacePathError(f"workspace_path is not a directory: {target}")

    if settings.allow_any_workspace_path:
        return target

    if not tenant.allowed_roots:
        raise WorkspacePathError(
            "This tenant has no allowed_roots configured; workspace_path "
            "overrides are refused. Set allowed_roots on the tenant, or turn "
            "on ALLOW_ANY_WORKSPACE_PATH for dev."
        )

    for root in tenant.allowed_roots:
        try:
            root_p = Path(root).expanduser().resolve(strict=True)
        except FileNotFoundError:
            continue
        try:
            target.relative_to(root_p)
        except ValueError:
            continue
        return target

    raise WorkspacePathError(
        f"workspace_path {target} is not under any of this tenant's allowed_roots."
    )
