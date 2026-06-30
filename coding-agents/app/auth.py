"""Authentication: resolve the caller's API key to a Tenant.

Accepts either:
    Authorization: Bearer <api_key>
    X-API-Key: <api_key>
"""
from __future__ import annotations

from typing import Optional

from fastapi import Depends, Header, HTTPException, status

from .config import Settings, get_settings
from .tenancy import Tenant, TenantRegistry

# Built once at process start.
_registry: Optional[TenantRegistry] = None


def get_registry(settings: Settings = Depends(get_settings)) -> TenantRegistry:
    global _registry
    if _registry is None:
        _registry = TenantRegistry.from_settings(settings)
    return _registry


def _extract_key(authorization: Optional[str], x_api_key: Optional[str]) -> str:
    if x_api_key:
        return x_api_key.strip()
    if authorization:
        parts = authorization.split(" ", 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1].strip()
    return ""


def get_current_tenant(
    authorization: Optional[str] = Header(default=None),
    x_api_key: Optional[str] = Header(default=None),
    registry: TenantRegistry = Depends(get_registry),
) -> Tenant:
    presented = _extract_key(authorization, x_api_key)
    tenant = registry.authenticate(presented)
    if tenant is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid API key.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return tenant
