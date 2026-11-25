import pytest

from app.security.catalog import DEFAULT_ROLES, PERMISSIONS

from .conftest import add_member, login, make_org, make_superadmin

WORKSPACE_PAGES = ["/app/", "/app/team", "/app/roles", "/app/activity", "/app/organization", "/app/subscription", "/app/account"]
ADMIN_PAGES = ["/admin/", "/admin/organizations", "/admin/plans", "/admin/audit"]


def test_role_catalog_is_consistent():
    assert len(DEFAULT_ROLES) == 11
    for key, (_, _, perms) in DEFAULT_ROLES.items():
        assert perms <= set(PERMISSIONS), key
    assert all(p.endswith(".view") for p in DEFAULT_ROLES["auditor"][2])
    assert DEFAULT_ROLES["org_admin"][2] == set(PERMISSIONS)


def test_org_admin_can_open_every_workspace_page(client):
    make_org()
    login(client, "admin@hope.org")
    for url in WORKSPACE_PAGES:
        assert client.get(url).status_code == 200, url


@pytest.mark.parametrize("role,allowed,denied", [
    ("staff", ["/app/", "/app/organization", "/app/account"], ["/app/team", "/app/roles", "/app/activity", "/app/subscription"]),
    ("auditor", ["/app/team", "/app/roles", "/app/activity", "/app/subscription"], []),
    ("hr_officer", ["/app/team", "/app/roles"], ["/app/activity", "/app/subscription"]),
    ("finance_officer", ["/app/subscription"], ["/app/team", "/app/activity"]),
])
def test_role_based_page_access(client, role, allowed, denied):
    org_id, _ = make_org()
    add_member(org_id, f"{role}@hope.org", role)
    login(client, f"{role}@hope.org")
    for url in allowed:
        assert client.get(url).status_code == 200, (role, url)
    for url in denied:
        assert client.get(url).status_code == 403, (role, url)


def test_only_org_admin_can_edit_organization(client):
    org_id, _ = make_org()
    add_member(org_id, "aud@hope.org", "auditor")
    login(client, "aud@hope.org")
    r = client.post("/app/organization", data={"name": "Hijacked", "org_type": "ngo", "country": "Ghana",
                                               "currency": "GHS", "timezone": "Africa/Accra"})
    assert r.status_code == 403
    client.post("/auth/logout")
    login(client, "admin@hope.org")
    r = client.post("/app/organization", data={"name": "Hope Foundation Ghana", "org_type": "ngo", "country": "Ghana",
                                               "email": "info@hope.org", "phone": "+233 20 000 0000",
                                               "address": "Accra", "currency": "USD", "timezone": "Africa/Accra"})
    assert r.status_code == 302
    page = client.get("/app/organization").data
    assert b"Hope Foundation Ghana" in page
    assert b"Organization profile updated" in client.get("/app/activity").data


def test_team_page_only_lists_own_organization(client):
    a, _ = make_org("Alpha", "a@alpha.org")
    b, _ = make_org("Beta", "b@beta.org")
    add_member(b, "secret@beta.org", "staff", name="Beta Secret Person")
    login(client, "a@alpha.org")
    page = client.get("/app/team?status=all").data
    assert b"a@alpha.org" in page and b"secret@beta.org" not in page and b"Beta Secret" not in page
    assert client.get("/app/team?q=Beta").data.count(b"secret@beta.org") == 0


def test_org_user_cannot_reach_platform_console(client):
    make_org()
    login(client, "admin@hope.org")
    for url in ADMIN_PAGES:
        assert client.get(url).status_code == 403, url


def test_superadmin_can_open_every_admin_page(client):
    make_org()
    make_superadmin()
    login(client, "root@kayron.com")
    for url in ADMIN_PAGES:
        assert client.get(url).status_code == 200, url


def test_public_pages_and_health(client):
    make_org()
    r = client.get("/")
    assert r.status_code == 200 and b"GH\xe2\x82\xb599" in r.data and b"Professional" in r.data
    assert client.get("/auth/login").status_code == 200
    assert client.get("/auth/register?plan=business").status_code == 200
    assert client.get("/healthz").json == {"status": "ok"}
    assert client.get("/nope").status_code == 404


def test_security_headers(client):
    r = client.get("/auth/login")
    csp = r.headers["Content-Security-Policy"]
    assert "script-src 'self'" in csp and "unsafe-inline" not in csp and "frame-ancestors 'none'" in csp
    assert r.headers["X-Frame-Options"] == "DENY"
    assert r.headers["X-Content-Type-Options"] == "nosniff"


def test_pages_have_no_inline_scripts_or_styles(client):
    """CSP blocks inline code; make sure templates don't depend on it."""
    import re

    make_org()
    make_superadmin()
    pages = ["/", "/auth/login", "/auth/register", "/nope"] + WORKSPACE_PAGES
    for url in pages:
        if url == "/app/":
            login(client, "admin@hope.org")
        html = client.get(url).get_data(as_text=True)
        for tag in re.findall(r"<script\b[^>]*>", html):
            assert "src=" in tag or 'type="application/json"' in tag, (url, tag)
        assert ' style="' not in html, url
        assert 'name="viewport" content="width=device-width, initial-scale=1' in html
