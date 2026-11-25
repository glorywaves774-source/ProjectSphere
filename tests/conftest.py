import os

import pytest
from flask_migrate import upgrade
from sqlalchemy import text

os.environ.setdefault("APP_ENV", "testing")

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import Role, User, UserRole  # noqa: E402
from app.services.onboarding import create_platform_admin, register_organization  # noqa: E402
from app.services.seed import seed_reference_data  # noqa: E402
from app.tenancy import clear_context, tenant_scope  # noqa: E402

PASSWORD = "Str0ngPassw0rd!"
TABLES = "email_outbox, payments, invoices, api_keys, quotations, procurement_items, procurement_requests, vendors, issue_comments, issues, risk_reviews, risks, indicator_values, indicators, result_items, evaluations, monitoring_visits, meeting_attendees, meetings, announcements, messages, conversation_participants, conversations, notifications, document_comments, document_versions, documents, time_entries, timesheets, expenses, budget_lines, exchange_rates, task_comments, task_dependencies, tasks, project_updates, project_members, projects, project_categories, invitations, departments, support_messages, support_tickets, audit_logs, user_roles, role_permissions, roles, users, subscriptions, organizations, plans, permissions, platform_settings"


@pytest.fixture(scope="session")
def app():
    app = create_app("testing")
    with app.app_context():
        with db.engine.begin() as conn:
            conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
            conn.execute(text("CREATE SCHEMA public"))
        upgrade()  # run the real migrations, including row-level security
    yield app


@pytest.fixture(autouse=True)
def clean_db(app):
    with app.app_context():
        clear_context()
        with db.engine.begin() as conn:
            conn.execute(text(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE"))
        seed_reference_data()
        yield
        db.session.rollback()
        db.session.remove()
        clear_context()


@pytest.fixture
def client(app):
    return app.test_client()


def make_org(name="Hope Foundation", email="admin@hope.org", plan="starter", org_type="ngo"):
    org, user = register_organization(org_name=name, org_type=org_type, country="Ghana", admin_name="Ama Mensah",
                                      admin_email=email, password=PASSWORD, plan_code=plan)
    return org.id, user.id


def add_member(org_id, email, role_key="staff", name="Kofi Boateng"):
    from sqlalchemy import select

    with tenant_scope(org_id):
        role = db.session.scalar(select(Role).where(Role.key == role_key))
        user = User(organization_id=org_id, email=email, full_name=name)
        user.set_password(PASSWORD)
        db.session.add(user)
        db.session.flush()
        db.session.add(UserRole(organization_id=org_id, user_id=user.id, role_id=role.id))
        db.session.commit()
        return user.id


def make_superadmin(email="root@kayron.com"):
    create_platform_admin(email, "Platform Admin", PASSWORD)
    return email


def login(client, email, password=PASSWORD, follow=False):
    return client.post("/auth/login", data={"email": email, "password": password}, follow_redirects=follow)
