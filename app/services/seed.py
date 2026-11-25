"""Idempotent reference data: permission catalog and subscription plans."""
from decimal import Decimal

from sqlalchemy import select

from app.extensions import db
from app.models import Permission, Plan
from app.security.catalog import PERMISSIONS

# Proposed launch prices from the product brief (not market-validated).
DEFAULT_PLANS = [
    dict(code="starter", name="Starter", audience="Small teams", sort_order=1,
         description="Everything a small team needs to run projects in one place.",
         price_monthly=Decimal("99"), price_annual=Decimal("990"),
         max_users=5, max_projects=5, max_storage_mb=2048,
         features=["Up to 5 team members", "5 active projects", "Tasks, milestones and Gantt",
                   "Budget tracking", "2 GB document storage"]),
    dict(code="professional", api_access=True, name="Professional", audience="Growing organizations", sort_order=2,
         description="For growing organizations running several projects at once.",
         price_monthly=Decimal("249"), price_annual=Decimal("2490"),
         max_users=15, max_projects=25, max_storage_mb=10240,
         features=["Up to 15 team members", "25 active projects", "Monitoring & evaluation",
                   "Risk registers", "PDF and Excel reports", "10 GB document storage"]),
    dict(code="business", api_access=True, name="Business", audience="Medium organizations", sort_order=3,
         description="Portfolio oversight, approvals and analytics for medium organizations.",
         price_monthly=Decimal("499"), price_annual=Decimal("4990"),
         max_users=50, max_projects=None, max_storage_mb=51200,
         features=["Up to 50 team members", "Unlimited projects", "Portfolio analytics",
                   "Approval workflows", "Procurement & donor management", "50 GB document storage"]),
    dict(code="enterprise", api_access=True, name="Enterprise", audience="Large institutions", sort_order=4,
         description="Custom limits, onboarding and integrations for large institutions.",
         price_monthly=None, price_annual=None,
         max_users=None, max_projects=None, max_storage_mb=None,
         features=["Unlimited team members", "Unlimited projects", "Custom integrations & API",
                   "Dedicated implementation support", "Custom storage"]),
]


def seed_permissions() -> int:
    existing = {p.code: p for p in db.session.scalars(select(Permission))}
    added = 0
    for code, (module, desc) in PERMISSIONS.items():
        if code in existing:
            existing[code].module, existing[code].description = module, desc
        else:
            db.session.add(Permission(code=code, module=module, description=desc))
            added += 1
    return added


def seed_plans() -> int:
    existing = {p.code for p in db.session.scalars(select(Plan))}
    added = 0
    for data in DEFAULT_PLANS:
        if data["code"] not in existing:
            db.session.add(Plan(currency="GHS", **data))
            added += 1
    return added


def seed_reference_data() -> dict:
    result = {"permissions": seed_permissions(), "plans": seed_plans()}
    db.session.commit()
    return result
