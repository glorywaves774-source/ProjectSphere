"""Application-level tenant isolation (the first of the two isolation layers)."""
import uuid

from sqlalchemy import event
from sqlalchemy.orm import Session, with_loader_criteria

from .context import apply_to_connection, get_context

_NO_TENANT = uuid.UUID(int=0)  # matches nothing


class TenantIsolationError(PermissionError):
    """Raised when code tries to write a row that belongs to another tenant."""


def _tenant_classes():
    from app.extensions import db

    return [m.class_ for m in db.Model.registry.mappers if getattr(m.class_, "__tenant_scoped__", False)]


def _owner_of(obj):
    from app.models import Organization

    if isinstance(obj, Organization):
        return obj.id
    return getattr(obj, "organization_id", None)


def install(db) -> None:
    from app.models import Organization

    tenant_classes = None

    @event.listens_for(Session, "after_begin")
    def _set_db_context(session, transaction, connection):
        apply_to_connection(connection)

    @event.listens_for(Session, "do_orm_execute")
    def _filter_reads(state):
        nonlocal tenant_classes
        if not state.is_select or state.execution_options.get("skip_tenant_filter"):
            return
        ctx = get_context()
        if ctx.platform or (ctx.auth_lookup and ctx.tenant_id is None):
            return
        tid = ctx.tenant_id or _NO_TENANT
        if tenant_classes is None:
            tenant_classes = _tenant_classes()
        opts = [with_loader_criteria(Organization, lambda c: c.id == tid, include_aliases=True)]
        for cls in tenant_classes:
            opts.append(
                with_loader_criteria(cls, lambda c: c.organization_id == tid, include_aliases=True)
            )
        state.statement = state.statement.options(*opts)

    @event.listens_for(Session, "before_flush")
    def _check_writes(session, flush_context, instances):
        ctx = get_context()
        if ctx.platform:
            return
        tid = ctx.tenant_id
        for obj in list(session.new):
            if isinstance(obj, Organization):
                if obj.id is None:
                    obj.id = uuid.uuid4()
                if obj.id != tid:
                    raise TenantIsolationError("Organizations can only be created inside their own tenant scope")
                continue
            if getattr(type(obj), "__tenant_scoped__", False):
                if obj.organization_id is None:
                    if tid is None:
                        raise TenantIsolationError(f"No tenant in scope for new {type(obj).__name__}")
                    obj.organization_id = tid
                elif obj.organization_id != tid:
                    raise TenantIsolationError(f"Cross-tenant write blocked for {type(obj).__name__}")
        for obj in list(session.dirty) + list(session.deleted):
            if isinstance(obj, Organization) or getattr(type(obj), "__tenant_scoped__", False):
                if _owner_of(obj) != tid:
                    raise TenantIsolationError(f"Cross-tenant change blocked for {type(obj).__name__}")
