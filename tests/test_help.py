from app.blueprints.help.routes import load_guide, redact

from .conftest import add_member, login, make_org


def test_guide_parses():
    sections = load_guide()
    assert len(sections) >= 10
    assert all(s["body"] for s in sections)


def test_redaction():
    out = redact("call me on +233 24 123 4567 or mail kofi@example.com, card GHA-123456789-0")
    assert "kofi@" not in out and "4567" not in out and "123456789" not in out


def test_requires_sign_in(client):
    assert client.post("/help/ask", json={"q": "team"}).status_code in (302, 401)


def test_answer_links_only_to_pages_role_can_open(client):
    org_id, _ = make_org()
    add_member(org_id, "staff@hope.org", "staff")
    login(client, "staff@hope.org")
    data = client.post("/help/ask", json={"q": "How do I add my team?"}).json
    assert data["title"].startswith("Team")
    assert data["links"] == []
    assert "Organization Admin" in data["note"] and "job" in data["note"]
    data = client.post("/help/ask", json={"q": "change my password"}).json
    assert data["links"] and data["links"][0]["url"] == "/app/account"


def test_admin_gets_link_and_unknown_question_is_handled(client):
    make_org()
    login(client, "admin@hope.org")
    data = client.post("/help/ask", json={"q": "what can each role do"}).json
    assert data["links"][0]["url"] == "/app/roles"
    data = client.post("/help/ask", json={"q": "zzzz qqqq"}).json
    assert data["links"] == [] and "couldn't find" in data["answer"]
    assert client.post("/help/ask", json={"q": ""}).status_code == 400
    assert len(client.get("/help/suggestions").json["items"]) >= 3
