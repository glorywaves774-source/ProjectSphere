"""Tenant isolation: application layer and PostgreSQL row-level security."""
import uuid

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import ProgrammingError

from app.extensions import db
from app.models import AuditLog, Organization, Role, Subscription, User
from app.tenancy import TenantIsolationError, tenant_scope

from .conftest import add_member, make_org


@pytest.fixture
def two_orgs():
    a, _ = make_org("Alpha NGO", "a@alpha.org")
    b, _ = make_org("Beta Ltd", "b@beta.com", org_type="private")
    add_member(b, "staff@beta.com")
    return a, b


def test_orm_reads_are_scoped_to_current_tenant(two_orgs):
    a, b = two_orgs
    with tenant_scope(a):
        emails = set(db.session.scalars(select(User.email)))
        assert emails == {"a@alpha.org"}
        assert {o.name for o in db.session.scalars(select(Organization))} == {"Alpha NGO"}
        assert db.session.scalar(select(func.count(Role.id))) == 11
        assert db.session.scalar(select(func.count(Subscription.id))) == 1
        assert all(e.organization_id == a for e in db.session.scalars(select(AuditLog)))
    with tenant_scope(b):
        assert set(db.session.scalars(select(User.email))) == {"b@beta.com", "staff@beta.com"}


def test_get_by_id_of_other_tenant_returns_nothing(two_orgs):
    a, b = two_orgs
    with tenant_scope(b):
        other_user_id = db.session.scalar(select(User.id).where(User.email == "staff@beta.com"))
    db.session.expunge_all()
    with tenant_scope(a):
        assert db.session.scalar(select(User).where(User.id == other_user_id)) is None
        assert db.session.get(Organization, b) is None


def test_no_context_sees_nothing(two_orgs):
    assert db.session.scalar(select(func.count(User.id))) == 0
    assert db.session.scalar(select(func.count(Organization.id))) == 0


def test_database_rls_filters_raw_sql(two_orgs):
    """Raw SQL skips the ORM filter entirely; PostgreSQL RLS must still isolate."""
    a, b = two_orgs
    with tenant_scope(a):
        assert db.session.execute(text("SELECT count(*) FROM users")).scalar() == 1
        assert db.session.execute(text("SELECT count(*) FROM roles")).scalar() == 11
        assert db.session.execute(text("SELECT count(*) FROM organizations")).scalar() == 1
    db.session.commit()
    assert db.session.execute(text("SELECT count(*) FROM users")).scalar() == 0
    with tenant_scope(platform=True):
        assert db.session.execute(text("SELECT count(*) FROM users")).scalar() == 3


def test_database_rls_holds_when_orm_filter_skipped(two_orgs):
    a, _ = two_orgs
    with tenant_scope(a):
        rows = db.session.scalars(select(User).execution_options(skip_tenant_filter=True)).all()
        assert [u.email for u in rows] == ["a@alpha.org"]


def test_raw_cross_tenant_update_and_delete_affect_nothing(two_orgs):
    a, b = two_orgs
    with tenant_scope(a):
        res = db.session.execute(text("UPDATE users SET full_name='hacked' WHERE organization_id = :b"), {"b": b})
        assert res.rowcount == 0
        res = db.session.execute(text("DELETE FROM roles WHERE organization_id = :b"), {"b": b})
        assert res.rowcount == 0
        db.session.commit()
    with tenant_scope(b):
        assert db.session.scalar(select(func.count(Role.id))) == 11


def test_raw_cross_tenant_insert_rejected_by_rls(two_orgs):
    a, b = two_orgs
    with tenant_scope(a):
        with pytest.raises(ProgrammingError, match="row-level security"):
            db.session.execute(
                text("INSERT INTO roles (organization_id, key, name, description, is_system, created_at, updated_at) "
                     "VALUES (:b, 'evil', 'Evil', '', false, now(), now())"), {"b": b})
    db.session.rollback()


def test_orm_cross_tenant_write_blocked_by_guard(two_orgs):
    a, b = two_orgs
    with tenant_scope(a):
        db.session.add(Role(organization_id=b, key="x", name="X"))
        with pytest.raises(TenantIsolationError):
            db.session.flush()
    db.session.rollback()


def test_new_rows_get_current_tenant_automatically(two_orgs):
    a, _ = two_orgs
    with tenant_scope(a):
        role = Role(key="custom", name="Custom", is_system=False)
        db.session.add(role)
        db.session.commit()
        assert role.organization_id == a


def test_creating_rows_without_tenant_is_refused():
    db.session.add(Role(key="orphan", name="Orphan"))
    with pytest.raises(TenantIsolationError):
        db.session.flush()
    db.session.rollback()


def test_cannot_create_foreign_organization_inside_tenant(two_orgs):
    a, _ = two_orgs
    with tenant_scope(a):
        db.session.add(Organization(id=uuid.uuid4(), name="Sneaky", slug="sneaky"))
        with pytest.raises(TenantIsolationError):
            db.session.flush()
    db.session.rollback()


def test_rls_is_enabled_and_forced_on_all_tenant_tables():
    from app.tenancy.rls import TENANT_TABLES

    rows = db.session.execute(text(
        "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = ANY(:t)"),
        {"t": list(TENANT_TABLES)}).all()
    assert {r[0] for r in rows} == set(TENANT_TABLES)
    assert all(r[1] and r[2] for r in rows)


def test_app_role_is_not_superuser():
    """Superusers bypass RLS, so the application must never connect as one."""
    assert db.session.execute(text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")).one() == (False, False)
