"""Permission catalog and the default organization roles.

Permissions are global codes; roles belong to an organization so each tenant
can later adjust its own roles (Stage 3) without affecting anyone else.
Codes for modules built in later stages are declared now so roles are stable.
"""

PERMISSIONS: dict[str, tuple[str, str]] = {
    # code: (module, description)
    "org.view": ("Organization", "View organization profile"),
    "org.manage": ("Organization", "Edit organization profile and settings"),
    "users.view": ("People", "View team members"),
    "users.manage": ("People", "Invite, edit and deactivate team members"),
    "roles.view": ("People", "View roles and permissions"),
    "roles.manage": ("People", "Create roles and change permissions"),
    "subscription.view": ("Subscription", "View plan and usage"),
    "subscription.manage": ("Subscription", "Change plan and billing"),
    "audit.view": ("Audit", "View the activity and audit log"),
    "projects.view": ("Projects", "View projects"),
    "projects.manage": ("Projects", "Create and manage projects"),
    "portfolio.view": ("Projects", "View portfolio-wide analytics"),
    "tasks.view": ("Tasks", "View tasks"),
    "tasks.manage": ("Tasks", "Create, assign and update tasks"),
    "tasks.approve": ("Tasks", "Approve completed tasks"),
    "budget.view": ("Finance", "View budgets and expenditure"),
    "budget.manage": ("Finance", "Create budgets and record expenditure"),
    "budget.approve": ("Finance", "Approve financial requests"),
    "hr.view": ("Team", "View staff, workload and timesheets"),
    "hr.manage": ("Team", "Manage staff records, teams and timesheets"),
    "documents.view": ("Documents", "View and download documents"),
    "documents.manage": ("Documents", "Upload and manage documents"),
    "risks.view": ("Risk", "View risks and issues"),
    "risks.manage": ("Risk", "Manage risks and issues"),
    "procurement.view": ("Procurement", "View procurement requests"),
    "procurement.manage": ("Procurement", "Manage procurement and vendors"),
    "me.view": ("M&E", "View indicators and results"),
    "me.manage": ("M&E", "Manage results frameworks and indicators"),
    "field.report": ("Field", "Submit field reports and visits"),
    "reports.view": ("Reports", "View and export reports"),
}

_ALL = set(PERMISSIONS)
_ALL_VIEW = {p for p in PERMISSIONS if p.endswith(".view")}

# key: (display name, description, permissions)
DEFAULT_ROLES: dict[str, tuple[str, str, set[str]]] = {
    "org_admin": ("Organization Admin", "Manages the organization and its workspace", _ALL),
    "executive_director": (
        "Executive Director",
        "Organizational oversight",
        _ALL_VIEW | {"budget.approve", "tasks.approve"},
    ),
    "project_director": (
        "Project Director",
        "Project portfolio oversight",
        _ALL_VIEW - {"audit.view"}
        | {"projects.manage", "tasks.manage", "tasks.approve", "risks.manage", "budget.approve"},
    ),
    "project_manager": (
        "Project Manager",
        "Project execution",
        {"org.view", "users.view", "projects.view", "projects.manage", "tasks.view", "tasks.manage",
         "tasks.approve", "budget.view", "documents.view", "documents.manage", "risks.view",
         "risks.manage", "me.view", "reports.view", "hr.view", "procurement.view"},
    ),
    "finance_officer": (
        "Finance Officer",
        "Project budgets",
        {"org.view", "projects.view", "budget.view", "budget.manage", "procurement.view",
         "documents.view", "reports.view", "subscription.view"},
    ),
    "me_officer": (
        "M&E Officer",
        "Monitoring and evaluation",
        {"org.view", "projects.view", "tasks.view", "me.view", "me.manage", "field.report",
         "documents.view", "documents.manage", "reports.view"},
    ),
    "hr_officer": (
        "HR Officer",
        "Team and staff management",
        # Covers everything Staff Members and Field Officers have, so HR can manage them
        {"org.view", "users.view", "users.manage", "roles.view", "hr.view", "hr.manage",
         "projects.view", "tasks.view", "documents.view", "me.view", "field.report", "reports.view"},
    ),
    "procurement_officer": (
        "Procurement Officer",
        "Procurement activities",
        {"org.view", "projects.view", "procurement.view", "procurement.manage", "budget.view",
         "documents.view", "documents.manage"},
    ),
    "field_officer": (
        "Field Officer",
        "Field activities and reporting",
        {"org.view", "projects.view", "tasks.view", "field.report", "me.view", "documents.view", "risks.view"},
    ),
    "staff": (
        "Staff Member",
        "Assigned tasks",
        {"org.view", "projects.view", "tasks.view", "documents.view"},
    ),
    "auditor": ("Auditor", "Read-only audit access", _ALL_VIEW),
}
