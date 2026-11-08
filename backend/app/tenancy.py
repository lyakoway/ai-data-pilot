"""Tenant context — P0.2 (ADR-003/004, application-level isolation).

Explicit, pass-down context: no global mutable state. The FastAPI dependency
`require_tenant` is the ONLY place where a workspace is derived from the
request; everything below the API layer receives a validated workspace_id
and fails closed when it is missing.

DEV vs FUTURE AUTHN (P1a, ADR-006): today the workspace comes from the
explicitly-optional ``X-Workspace-Id`` header (dev mechanism, separated by
design). After OIDC lands, the SAME dependency will derive the workspace
from the JWT membership table instead — service/DB layers will not change.

Isolation level: SQLite application-level filtering (WHERE workspace_id=…).
This is NOT PostgreSQL RLS — see docs/SAAS_ARCHITECTURE.md (P0/P1 roadmap).
"""
from __future__ import annotations

import threading
from dataclasses import dataclass

from fastapi import Header, HTTPException

DEFAULT_WORKSPACE_ID = "ws_default"
DEFAULT_WORKSPACE_NAME = "Локальная разработка (dev)"


class TenantContextError(RuntimeError):
    """Raised when a tenant-scoped operation runs without a valid workspace."""


@dataclass(frozen=True)
class TenantContext:
    """Validated tenant context for one request/job.

    workspace_id — REQUIRED for any tenant-data access (fail-closed below).
    user — None in dev; will carry the OIDC `sub` in P1a.
    origin — how the workspace was derived ('dev-header' | 'dev-default' |
             'oidc' in the future) — for audit/log clarity.
    """

    workspace_id: str
    user: str | None = None
    origin: str = "dev"


def require_workspace(workspace_id: str | None) -> None:
    """Fail-closed guard for DB/service layers: a missing workspace must
    never silently widen to global access."""
    if not workspace_id:
        raise TenantContextError(
            "workspace context required: tenant-scoped operation refused to run "
            "globally (P0.2 fail-closed contract)"
        )


_warned_default = threading.Event()


def resolve_dev_workspace(header_value: str | None, *, production: bool) -> str:
    """DEV WORKSPACE RESOLUTION ONLY (replaced by OIDC in P1a).

    - header present → must reference an EXISTING workspace, else 403;
    - header absent  → dev: the default dev workspace (warned once);
                       production: refusal (authn arrives in P1a — until then
                       a production deployment must not guess tenants).
    """
    if header_value:
        from app.db import app_db

        if not app_db.workspace_exists(header_value):
            raise HTTPException(status_code=403, detail=f"Unknown workspace: {header_value!r}")
        return header_value
    if production:
        raise HTTPException(
            status_code=403,
            detail="X-Workspace-Id header required (authn lands in P1a; "
            "production must not fall back to a default workspace)",
        )
    if _warned_default.is_set() is False:
        _warned_default.set()
        import logging

        logging.getLogger("app.tenancy").warning(
            "DEV fallback: no X-Workspace-Id header — using %s (production refuses this)",
            DEFAULT_WORKSPACE_ID,
        )
    return DEFAULT_WORKSPACE_ID


async def require_tenant(
    x_workspace_id: str | None = Header(default=None, alias="X-Workspace-Id"),
) -> TenantContext:
    """FastAPI dependency: the single workspace-resolution point."""
    from app.config import get_settings

    ws = resolve_dev_workspace(x_workspace_id, production=get_settings().app_env == "production")
    origin = "dev-header" if x_workspace_id else "dev-default"
    return TenantContext(workspace_id=ws, origin=origin)
