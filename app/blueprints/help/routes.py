"""ProjectSphere Assistant: answers "how do I…" questions from the help guide.

Answers come only from docs/help.md. No records and no personal details are
sent anywhere; nothing leaves the server. Links are only offered for pages
the person's role can open.
"""
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

from flask import Blueprint, jsonify, request, url_for
from flask_login import current_user, login_required

from app.extensions import limiter
from app.navigation import allowed_endpoints

bp = Blueprint("help", __name__)

GUIDE = Path(__file__).resolve().parents[3] / "docs" / "help.md"
STOP = set("a an and are as at be by can do does for from how i in is it me my of on or the to what when where which who why will with you your".split())

# Redact anything that looks like personal details before matching or logging
_PII = [
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "[email]"),
    (re.compile(r"\+?\d[\d\s-]{7,}\d"), "[number]"),
    (re.compile(r"\bGHA-\d{9}-\d\b", re.I), "[id]"),
]


def redact(text: str) -> str:
    for pattern, repl in _PII:
        text = pattern.sub(repl, text)
    return text


def _norm(text: str) -> list[str]:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return [w for w in re.findall(r"[a-z0-9&]+", text) if w not in STOP and len(w) > 1]


@lru_cache(maxsize=1)
def load_guide() -> list[dict]:
    sections, current = [], None
    for line in GUIDE.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            current = {"title": line[3:].strip(), "endpoint": None, "who": "", "body": [], "keywords": ""}
            sections.append(current)
        elif current is None:
            continue
        elif m := re.match(r"<!--\s*endpoint:\s*([\w.]+)\s*(?:who:\s*(.*?))?\s*-->", line):
            current["endpoint"], current["who"] = m.group(1), (m.group(2) or "").strip()
        elif m := re.match(r"<!--\s*keywords:\s*(.*?)\s*-->", line):
            current["keywords"] = m.group(1)
        else:
            current["body"].append(line)
    for s in sections:
        s["body"] = "\n".join(s["body"]).strip()
        s["tokens"] = set(_norm(s["title"] + " " + s["keywords"])) | set(_norm(s["body"]))
        s["title_tokens"] = set(_norm(s["title"] + " " + s["keywords"]))
    return sections


def answer(question: str, allowed: set[str]) -> dict:
    words = set(_norm(redact(question)))
    best, best_score = None, 0.0
    for s in load_guide():
        score = len(words & s["title_tokens"]) * 3 + len(words & s["tokens"])
        if score > best_score:
            best, best_score = s, score
    if best is None or best_score < 2:
        return {"title": "I'm not sure yet",
                "answer": "I couldn't find that in the guide. Try asking about signing in, your team, roles, "
                          "your organization profile, your plan, or dark mode.", "links": []}
    links, note = [], ""
    if best["endpoint"]:
        if best["endpoint"] in allowed:
            links.append({"label": f"Open {best['title'].split(':')[0].lower()}", "url": url_for(best["endpoint"])})
        else:
            note = f"This is {best['who'] or 'another role'}'s job, so your role can't open that page."
    return {"title": best["title"], "answer": best["body"], "links": links, "note": note}


@bp.route("/ask", methods=["POST"])
@login_required
@limiter.limit("30 per minute")
def ask():
    data = request.get_json(silent=True) or {}
    question = str(data.get("q", ""))[:300].strip()
    if not question:
        return jsonify(error="Please type a question."), 400
    return jsonify(answer(question, allowed_endpoints(current_user)))


@bp.route("/suggestions")
@login_required
def suggestions():
    if current_user.is_platform_admin:
        items = ["How do I create an organization?", "How do I change a customer's plan?",
                 "How do I unlock a user?", "How do I answer support requests?"]
    else:
        items = ["How do I contact support?", "How do I add my team?", "What can each role do?", "How do I change my plan?",
                 "How do I switch to dark mode?", "How do I change my password?"]
    return jsonify(items=items)
