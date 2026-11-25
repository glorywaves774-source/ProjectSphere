"""Tenant context: which organization the current code is acting for.

Isolation is enforced twice:

1. Application level - every query on a TenantScoped model gets an automatic
   ``organization_id = <current tenant>`` filter, and every write is checked
   before flush (see ``app.tenancy.guard``).
2. Database level - PostgreSQL row-level security policies read the
   transaction-local settings written here (see ``app.tenancy.rls``).

The context lives in a ContextVar so it works the same in web requests,
CLI commands and background jobs.
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace

from sqlalchemy import text


@dataclass(frozen=True)
class TenantContext:
    tenant_id: uuid.UUID | None = None
    platform: bool = False  # Platform Super Administrator scope (all tenants)
    auth_lookup: bool = False  # Narrow read-only scope used to find a user at sign-in


_EMPTY = TenantContext()
_current: ContextVar[TenantContext] = ContextVar("tenant_context", default=_EMPTY)


def get_context() -> TenantContext:
    return _current.get()


def current_tenant_id() -> uuid.UUID | None:
    return _current.get().tenant_id


def is_platform_scope() -> bool:
    return _current.get().platform


def set_context(ctx: TenantContext) -> None:
    _current.set(ctx)
    sync_db_context()


def clear_context() -> None:
    _current.set(_EMPTY)


@contextmanager
def tenant_scope(tenant_id=None, *, platform: bool = False, auth_lookup: bool = False):
    """Temporarily act as a tenant / platform / auth lookup, then restore."""
    previous = _current.get()
    token = _current.set(
        TenantContext(
            tenant_id=uuid.UUID(str(tenant_id)) if tenant_id else None,
            platform=platform,
            auth_lookup=auth_lookup,
        )
    )
    sync_db_context()
    try:
        yield
    finally:
        _current.reset(token)
        if _current.get() != previous:  # pragma: no cover - defensive
            _current.set(previous)
        sync_db_context()


@contextmanager
def auth_lookup_scope():
    """Allow reading user rows across tenants only to identify who is signing in."""
    previous = _current.get()
    token = _current.set(replace(previous, auth_lookup=True))
    sync_db_context()
    try:
        yield
    finally:
        _current.reset(token)
        sync_db_context()


_SET_SQL = text(
    "SELECT set_config('app.current_tenant', :tenant, true),"
    " set_config('app.platform', :platform, true),"
    " set_config('app.auth_lookup', :auth, true)"
)


def _params():
    ctx = _current.get()
    return {
        "tenant": str(ctx.tenant_id) if ctx.tenant_id else "",
        "platform": "on" if ctx.platform else "off",
        "auth": "on" if ctx.auth_lookup else "off",
    }


def apply_to_connection(connection) -> None:
    if connection.dialect.name != "postgresql":
        return
    connection.execute(_SET_SQL, _params())


def sync_db_context() -> None:
    """Push the current context into the open DB transaction, if there is one."""
    from app.extensions import db

    try:
        session = db.session()
    except RuntimeError:  # no app context
        return
    if session.in_transaction():
        connection = session.connection()
        if _transaction_failed(connection):
            return  # the caller must roll back; the next transaction gets the context
        apply_to_connection(connection)


def _transaction_failed(connection) -> bool:
    try:
        info = connection.connection.dbapi_connection.info
        from psycopg.pq import TransactionStatus

        return info.transaction_status == TransactionStatus.INERROR
    except Exception:  # pragma: no cover - other drivers
        return False
