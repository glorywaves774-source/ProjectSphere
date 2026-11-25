"""PostgreSQL row-level security (database-level tenant isolation).

FORCE ROW LEVEL SECURITY is used so the policies also apply to the table
owner (the application's database user). The application must never connect
as a PostgreSQL superuser, because superusers bypass RLS.
"""

TENANT = "NULLIF(current_setting('app.current_tenant', true), '')::uuid"
PLATFORM = "current_setting('app.platform', true) = 'on'"
AUTH = "current_setting('app.auth_lookup', true) = 'on'"

# table -> column holding the tenant id, grouped by the migration that created them.
# Migrations pass their own group so old migrations never touch tables that
# don't exist yet. TENANT_TABLES is the full current list (used by tests).
STAGE1_TABLES = {
    "organizations": "id",
    "subscriptions": "organization_id",
    "users": "organization_id",
    "roles": "organization_id",
    "user_roles": "organization_id",
    "audit_logs": "organization_id",
}
STAGE2_TABLES = {
    "support_tickets": "organization_id",
    "support_messages": "organization_id",
}
STAGE3_TABLES = {
    "departments": "organization_id",
    "invitations": "organization_id",
}
STAGE4_TABLES = {
    "project_categories": "organization_id",
    "projects": "organization_id",
    "project_members": "organization_id",
    "project_updates": "organization_id",
}
STAGE5_TABLES = {
    "tasks": "organization_id",
    "task_dependencies": "organization_id",
    "task_comments": "organization_id",
}
STAGE6_TABLES = {
    "budget_lines": "organization_id",
    "expenses": "organization_id",
    "exchange_rates": "organization_id",
}
STAGE7_TABLES = {
    "timesheets": "organization_id",
    "time_entries": "organization_id",
}
STAGE8_TABLES = {t: "organization_id" for t in (
    "documents", "document_versions", "document_comments", "notifications", "conversations",
    "conversation_participants", "messages", "announcements", "meetings", "meeting_attendees")}
STAGE9_TABLES = {t: "organization_id" for t in (
    "result_items", "indicators", "indicator_values", "monitoring_visits", "evaluations")}
STAGE10_TABLES = {t: "organization_id" for t in (
    "risks", "risk_reviews", "issues", "issue_comments", "vendors", "procurement_requests", "procurement_items",
    "quotations")}
STAGE11_TABLES = {t: "organization_id" for t in ("invoices", "payments", "api_keys", "email_outbox")}
TENANT_TABLES = {**STAGE1_TABLES, **STAGE2_TABLES, **STAGE3_TABLES, **STAGE4_TABLES, **STAGE5_TABLES,
                 **STAGE6_TABLES, **STAGE7_TABLES, **STAGE8_TABLES,
                 **STAGE9_TABLES, **STAGE10_TABLES, **STAGE11_TABLES}


def auth_lookup_policy(table: str) -> list[str]:
    """Read-only lookup before the tenant is known (sign-in, accepting an invitation)."""
    return [
        f"DROP POLICY IF EXISTS auth_lookup ON {table}",
        f"CREATE POLICY auth_lookup ON {table} FOR SELECT USING ({AUTH})",
    ]


def table_policies(tables: dict[str, str]) -> list[str]:
    stmts: list[str] = []
    for table, col in tables.items():
        stmts += [
            f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
            f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
            f"DROP POLICY IF EXISTS tenant_isolation ON {table}",
            f"CREATE POLICY tenant_isolation ON {table} FOR ALL "
            f"USING ({col} = {TENANT} OR {PLATFORM}) "
            f"WITH CHECK ({col} = {TENANT} OR {PLATFORM})",
        ]
    return stmts


def drop_table_policies(tables: dict[str, str]) -> list[str]:
    stmts: list[str] = []
    for table in tables:
        stmts += [
            f"DROP POLICY IF EXISTS tenant_isolation ON {table}",
            f"DROP POLICY IF EXISTS auth_lookup ON {table}",
            f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY",
            f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY",
        ]
    return stmts


def rls_statements() -> list[str]:
    """Stage 1 policies (kept with this name for migration 0001)."""
    stmts: list[str] = []
    for table, col in STAGE1_TABLES.items():
        stmts += [
            f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
            f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
            f"DROP POLICY IF EXISTS tenant_isolation ON {table}",
            f"CREATE POLICY tenant_isolation ON {table} FOR ALL "
            f"USING ({col} = {TENANT} OR {PLATFORM}) "
            f"WITH CHECK ({col} = {TENANT} OR {PLATFORM})",
        ]
    # Sign-in needs to find a user (and their organization's status) before the
    # tenant is known. This is read-only and only switched on around that lookup.
    for table in ("users", "organizations", "user_roles", "roles"):
        stmts += [
            f"DROP POLICY IF EXISTS auth_lookup ON {table}",
            f"CREATE POLICY auth_lookup ON {table} FOR SELECT USING ({AUTH})",
        ]
    return stmts


def drop_rls_statements() -> list[str]:
    stmts: list[str] = []
    for table in STAGE1_TABLES:
        stmts += [
            f"DROP POLICY IF EXISTS tenant_isolation ON {table}",
            f"DROP POLICY IF EXISTS auth_lookup ON {table}",
            f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY",
            f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY",
        ]
    return stmts
