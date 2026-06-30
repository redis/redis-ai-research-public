"""Tenancy layer.

Everything that makes the agent *per-tenant* lives here:

  * Tenant            - immutable description of one customer org.
  * TenantRegistry    - maps a presented API key -> Tenant.
  * build_manifest    - the per-tenant workspace handed to the sandbox.
  * make_sandbox_client - a *fresh* sandbox backend per task, so one tenant's
                          run can never see another's files.

Isolation model: one sandbox session per task. For the "docker" backend that
is one ephemeral container per request; for hosted providers it would be one
ephemeral micro-VM. The Manifest decides what (and only what) gets mounted into
that workspace for the tenant making the request.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from agents.sandbox import Manifest
from agents.sandbox.entries import GitRepo, LocalDir

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


def build_manifest(tenant: Tenant, settings: Settings) -> Manifest:
    """The workspace mounted into the tenant's sandbox session.

    The entry key ("repo") is the directory name the agent sees inside the
    sandbox, e.g. the model edits files under `repo/`.
    """
    if tenant.workspace_kind == "git_repo":
        return Manifest(
            entries={"repo": GitRepo(repo=tenant.workspace_source, ref=tenant.git_ref)}
        )
    if tenant.workspace_kind == "local_dir":
        path = resolve_local_workspace(tenant, settings)
        return Manifest(entries={"repo": LocalDir(src=str(path))})
    raise ValueError(f"Unknown workspace_kind: {tenant.workspace_kind!r}")


def make_sandbox_client(settings: Settings):
    """Create a FRESH sandbox client for a single task.

    A new client per task means a new isolated session per task, so tenants
    never share a workspace.
    """
    if settings.sandbox_backend == "docker":
        # NOTE: verify these option field names against your installed SDK
        # version (docs: ref/sandbox/sandboxes/docker). Requires
        # `pip install "openai-agents[docker]"` and a running Docker daemon.
        from agents.sandbox.sandboxes.docker import (
            DockerSandboxClient,
            DockerSandboxClientOptions,
        )

        return DockerSandboxClient(
            DockerSandboxClientOptions(image=settings.docker_image)
        )

    from agents.sandbox.sandboxes.unix_local import UnixLocalSandboxClient

    return UnixLocalSandboxClient()
