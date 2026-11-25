"""Sidebar, phone drawer and bottom-tab navigation, filtered by role."""
from dataclasses import dataclass


@dataclass(frozen=True)
class NavItem:
    label: str
    icon: str
    endpoint: str | None
    permission: str | None = None
    tab_label: str | None = None  # short one-word label for the phone tab bar
    soon: str | None = None  # stage in which a planned module arrives


WORKSPACE_NAV = [
    ("Workspace", [
        NavItem("Dashboard", "layout-dashboard", "workspace.dashboard", None, "Home"),
        NavItem("Projects", "folder-kanban", "workspace.projects", "projects.view", "Projects"),
        NavItem("My tasks", "list-checks", "workspace.my_tasks", "tasks.view", "Tasks"),
        NavItem("Calendar", "calendar-days", "workspace.calendar_view", "tasks.view"),
        NavItem("My timesheet", "clock", "workspace.timesheet", None),
        NavItem("Messages", "messages-square", "workspace.messages", None),
        NavItem("Meetings", "video", "workspace.meetings", None),
        NavItem("Documents", "folder-open", "workspace.documents", "documents.view"),
        NavItem("Announcements", "megaphone", "workspace.announcements", None),
        NavItem("M&E results", "target", "workspace.me_overview", "me.view"),
        NavItem("Field visits", "map-pin", "workspace.visits", "me.view"),
        NavItem("Risks", "shield-alert", "workspace.risks", "risks.view"),
        NavItem("Issues", "circle-alert", "workspace.issues", "risks.view"),
        NavItem("Procurement", "shopping-cart", "workspace.procurement", "procurement.view"),
        NavItem("Vendors", "store", "workspace.vendors", "procurement.view"),
        NavItem("Portfolio", "chart-pie", "workspace.portfolio", "portfolio.view"),
        NavItem("Finance", "wallet", "workspace.finance", "budget.view"),
        NavItem("Expenses", "receipt", "workspace.expenses", "budget.view"),
        NavItem("Team", "users", "workspace.team", "users.view", "Team"),
        NavItem("Departments", "network", "workspace.departments", "org.view"),
        NavItem("Workload", "gauge-circle", "workspace.workload", "hr.view"),
        NavItem("Performance", "trophy", "workspace.performance", "hr.view"),
        NavItem("Roles & access", "shield-check", "workspace.roles", "roles.view"),
        NavItem("Activity log", "history", "workspace.activity", "audit.view"),
    ]),
    ("Organization", [
        NavItem("Organization profile", "building-2", "workspace.organization", "org.view", "Org"),
        NavItem("Subscription", "credit-card", "workspace.subscription", "subscription.view", "Plan"),
        NavItem("Integrations & API", "plug", "workspace.integrations", "org.manage"),
        NavItem("Help & support", "life-buoy", "workspace.support", None),
        NavItem("My account", "user-round", "workspace.account", None),
    ]),
    ("Coming next", [
        NavItem("Security & launch", "rocket", None, soon="Stage 12"),
    ]),
]

ADMIN_NAV = [
    ("Platform", [
        NavItem("Overview", "gauge", "admin.dashboard", None, "Overview"),
        NavItem("Organizations", "building", "admin.organizations", None, "Orgs"),
        NavItem("Support desk", "life-buoy", "admin.support", None, "Support"),
        NavItem("Billing", "receipt", "admin.billing_home", None, "Billing"),
        NavItem("Plans", "layers", "admin.plans", None),
    ]),
    ("Administration", [
        NavItem("Super Admins", "shield-check", "admin.admins", None),
        NavItem("Platform settings", "settings", "admin.settings", None),
        NavItem("Audit log", "history", "admin.audit", None),
        NavItem("My account", "user-round", "admin.account", None),
    ]),
]


def nav_for(user) -> dict:
    groups = ADMIN_NAV if user.is_platform_admin else WORKSPACE_NAV
    sections = []
    tabs = []
    for title, items in groups:
        visible = [i for i in items if i.soon or i.permission is None or user.can(i.permission)]
        if visible:
            sections.append((title, visible))
        for i in visible:
            if i.tab_label and i.endpoint and len(tabs) < 4:
                tabs.append(i)
    return {"sections": sections, "tabs": tabs, "is_admin": user.is_platform_admin}


def allowed_endpoints(user) -> set[str]:
    nav = nav_for(user)
    return {i.endpoint for _, items in nav["sections"] for i in items if i.endpoint}
