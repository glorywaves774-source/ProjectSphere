# Laari ProjectSphere

**Enterprise Project Management & Portfolio Intelligence** — a multi-tenant SaaS by Kayron Global Tech Services.
*Plan. Execute. Monitor. Deliver.*

This repository contains **Stage 1: Foundation and Architecture**, **Stage 2: SaaS Administration**, **Stage 3: Organization Management**, **Stage 4: Project Management**, **Stage 5: Task and Scheduling Management**, **Stage 6: Budget and Financial Management**, **Stage 7: Resource and Team Management**, **Stage 8: Documents and Collaboration**, **Stage 9: Monitoring and Evaluation**, **Stage 10: Risk and Procurement Management** and **Stage 11: SaaS Billing and Integrations** (see `CHANGELOG.md`).

---

## What Stage 1 delivers

| Area | What works today |
|---|---|
| Multi-tenant SaaS core | Organizations (tenants), each with an isolated workspace |
| Tenant isolation | Two layers: automatic ORM filtering **and** PostgreSQL row-level security (RLS) |
| Sign-up | Self-service organization registration → 14-day trial, 11 default roles, first admin |
| Authentication | Sign-in, sign-out (POST only), "keep me signed in", account lockout, session invalidation on password change |
| RBAC | 30 permissions, 11 organization roles, Super Admin as a separate platform level |
| Subscriptions | 4 plans (Starter GH₵99, Professional GH₵249, Business GH₵499, Enterprise custom), usage limits, trial expiry job |
| Organization workspace | Dashboard (live data), Team, Departments, Roles & access, Activity log, Organization profile, Subscription, Help & support, My account |
| Projects | Register (auto IDs), list & Kanban board, filters, CSV export, status updates & health, team, closure & lessons learned, categories |
| Tasks & scheduling | Work breakdown, sub-tasks, milestones, dependencies, critical path, approvals, auto progress, Gantt chart, calendar, My tasks |
| Finance | Budget lines & approval, expenses with approval and payment, over-budget control, multi-currency, budget vs actual, CPI/SPI, finance overview, Excel reports |
| Resources & team | Allocation %, weekly capacity, weekly timesheets with approval, workload, team performance, actual vs estimated hours |
| Documents | Organization & project library, version history, checked uploads (type, contents, macros, size), fingerprint-verified downloads, comments, archive, plan storage limits, local disk or S3-compatible storage |
| Collaboration | Notifications bell, direct & group messages, project discussions, announcements, meetings with minutes, calendar & .ics invites |
| Monitoring & evaluation | Results frameworks (goal → outcomes → outputs), indicators with baseline / target / endline, field results with verification, achievement and on-track status, field monitoring visits with GPS and photos, evaluations rated on OECD-DAC criteria, data-due reminders, donor-ready PDF reports and Excel indicator tracking tables |
| Risk & issues | 5×5 risk registers with heat map, owners, mitigation and contingency, dated reviews with history, review reminders, escalation of risks into issues, issue log with assignment and resolution, Excel export |
| Procurement | Vendor register with approval / suspension / blacklisting, purchase requests with items and budget-line checks, value-based methods (single quote, RFQ, tender), segregation of duties, quotations in any currency, award controls, numbered purchase orders (PDF), receipt with vendor rating that posts an approved expense to the budget |
| Billing | Plans chosen and paid in-app via Paystack (card and mobile money), numbered invoices with PDFs, credit for unused time on plan changes, renewal invoices, grace period and past-due handling, offline payments and manual invoices in the Super Admin console, revenue collected |
| Email | Transactional outbox: notifications and billing emails queued with the change that causes them, sent by the worker every minute; each person can turn emails off |
| API | Per-organization API keys (hashed, scoped, expiring, revocable), REST endpoints for projects, tasks, budgets, expenses, indicators, risks and issues, plus submitting indicator results for verification; 120 requests a minute per key |
| Portfolio | Live executive KPIs, health/status/category charts, delayed projects, deadlines, department performance |
| People | Add staff, email invitations, staff profiles, departments, custom roles, editable permissions, escalation safeguards |
| Super Admin console | Overview (orgs, MRR, trials, sign-ups, plan mix, needs-attention), Organizations (create, edit, search/filter, suspend/reactivate, change subscription, manage users), Plans (create/edit/retire), Support desk, Super Admins, Platform settings, Audit log |
| Support | Organizations raise requests; Kayron staff reply, add internal notes, triage and assign |
| Platform settings | Support/sales emails, self-service sign-up on/off, trial length, announcement banner |
| Audit trail | Sign-ins, failures, lockouts, profile changes, platform actions — with IP and device |
| Help assistant | "ProjectSphere Assistant" answers from `docs/help.md`, only links to pages your role can open |
| UI | Apple-inspired spatial UI, navy sidebar, purple/blue accents, light & dark mode, phone menu + bottom tabs |
| Security | CSRF, strict CSP (no inline scripts), HSTS, secure cookies, rate limits, scrypt password hashing |
| Background jobs | Celery + Redis worker with hourly trial-expiry schedule |
| Tests | 285 automated tests against real PostgreSQL, using the real migrations |

Final testing, security hardening and launch preparation follow in Stage 12. They are shown in the menu as "Coming next" with their stage number.

---

## How tenant isolation works

Every organization's rows carry `organization_id`. Isolation is enforced twice, so a bug in one layer can't leak data:

1. **Application layer** (`app/tenancy/guard.py`): every ORM `SELECT` on tenant tables is automatically filtered to the signed-in user's organization. Before anything is saved, rows belonging to another organization are refused (`TenantIsolationError`).
2. **Database layer** (`app/tenancy/rls.py`, applied by the migration): PostgreSQL row-level security, **forced** even for the table owner. Each transaction is told which organization it serves (`app.current_tenant`); PostgreSQL itself hides and blocks every other organization's rows — even for hand-written SQL.

Three narrow scopes exist:
- **Tenant scope** — normal signed-in users.
- **Platform scope** — Super Admins on `/admin`, can see all organizations.
- **Auth lookup** — read-only, switched on only for the instant needed to find a user by email at sign-in.

> **Important:** the app must never connect to PostgreSQL as a *superuser* — superusers ignore RLS. Render's default database user is not a superuser. A test (`test_app_role_is_not_superuser`) checks this.

**Rule for every future module:** business models must inherit `TenantScopedMixin`. Add the new tables as a `STAGEn_TABLES` group in `app/tenancy/rls.py` (and to `TENANT_TABLES`), then call `table_policies(STAGEn_TABLES)` in that stage's migration, as `0002` does.

---

## Run it locally

Requirements: Python 3.12+, PostgreSQL 14+, Redis (only for the background worker).

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env            # then edit SECRET_KEY

# Create a non-superuser database role (required for row-level security)
psql -U postgres -c "CREATE ROLE ps_app LOGIN PASSWORD 'ps_app' NOSUPERUSER NOBYPASSRLS;"
psql -U postgres -c "CREATE DATABASE projectsphere OWNER ps_app;"
psql -U postgres -c "CREATE DATABASE projectsphere_test OWNER ps_app;"

flask db upgrade                # tables + row-level security
flask seed                      # permissions and plans (safe to repeat)
flask create-superadmin --email you@yourcompany.com --name "Your Name"   # prompts for password
flask run                       # http://127.0.0.1:5000
```

Background worker (trial expiry every hour):

```bash
celery -A celery_worker.celery worker --beat --loglevel=info
```

Run the tests:

```bash
pytest -q
```

### Useful commands

| Command | Purpose |
|---|---|
| `flask seed` | Load / refresh permissions and plans |
| `flask create-superadmin --email …` | Create or reset a Platform Super Admin (password from prompt or `SUPERADMIN_PASSWORD`) |
| `flask expire-trials` | Run the trial-expiry job once |
| `flask db migrate -m "…"` / `flask db upgrade` | Schema migrations |

---

## Deploy to Render

1. Push this folder to a GitHub repository.
2. In Render: **New → Blueprint**, choose the repository. `render.yaml` creates:
   - `projectsphere-web` (Gunicorn), `projectsphere-worker` (Celery + beat),
   - `projectsphere-db` (PostgreSQL), `projectsphere-redis` (Key Value).
3. `SECRET_KEY` is generated automatically. Migrations and `flask seed` run before each deploy (`preDeployCommand`).
4. Create your Super Admin once from the web service **Shell**: `flask create-superadmin --email you@yourcompany.com`.
5. Add your domain in Render; HTTPS and HSTS are enforced in production.

Email (optional): to send invitation emails, set `MAIL_SERVER`, `MAIL_PORT`, `MAIL_USERNAME`, `MAIL_PASSWORD` and `MAIL_FROM` on the web service, using any SMTP provider. Without them, ProjectSphere shows each invitation link on screen to share yourself.

Document storage: Render's own disk is wiped on every deploy, so the blueprint sets `STORAGE_BACKEND=s3`. Create a bucket with any S3-compatible provider (AWS S3, Cloudflare R2, Backblaze B2, DigitalOcean Spaces) and fill in `S3_BUCKET`, `S3_REGION`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, and `S3_ENDPOINT_URL` for non-AWS providers. Keep the bucket private: files are only ever served through ProjectSphere, after a permission check and a fingerprint check, and are stored encrypted. Alternatively attach a Render persistent disk, set `STORAGE_BACKEND=local` and point `UPLOAD_ROOT` at the disk.

Payments: create a Paystack account, then set `PAYSTACK_SECRET_KEY` (use the `sk_test_` key until you go live) and `APP_BASE_URL`. In the Paystack dashboard set the webhook URL to `<APP_BASE_URL>/api/webhooks/paystack`. Every payment is re-checked with Paystack's API (status, amount and currency must match the invoice) before a plan is switched on, and webhooks are rejected unless their signature matches your secret key. `PLATFORM_BANK_DETAILS` is printed on invoices for customers who pay by transfer; record those payments in Admin → Billing.

Email: set the `MAIL_*` variables on the web service; the worker reuses them to send the outbox every minute and to issue renewal invoices daily. You can also run `flask send-mail` and `flask run-renewals` by hand.

Backups: Render takes daily backups on paid PostgreSQL plans; enable point-in-time recovery on the database for production.

---

## Project layout

```
app/
  __init__.py          application factory, request → tenant binding, errors
  config.py            environment-driven configuration (no secrets in code)
  models/              Organization, Plan, Subscription, User, Role, Permission, AuditLog
  tenancy/             context (who we act for), guard (ORM layer), rls (database layer)
  security/            permission catalog + default roles, route guards
  services/            onboarding, limits, audit, seed, subscription jobs, projects, tasks, finance,
                       resources, storage (local / S3), documents, notify, collab, me (M&E), risks, procurement, billing (Paystack), email_outbox, apikeys, pdf (ReportLab), excel
  blueprints/          public, auth, workspace (/app), admin (/admin), help
  templates/ static/   UI (CSS: app.css → dark.css → mobile.css; JS files only, no inline code)
migrations/            Alembic migrations (0001 = Stage 1 schema + RLS)
docs/help.md           guide used by the ProjectSphere Assistant
tests/                 pytest suite (runs the real migrations on PostgreSQL)
celery_worker.py       background jobs
render.yaml            Render deployment blueprint
```

## Design system

- Colours are CSS variables in `app/static/css/app.css` (`--ps-navy-*`, `--ps-purple`, `--ps-blue`); dark mode overrides them in `dark.css`.
- Theme follows the device until the person chooses; the choice is remembered (localStorage + cookie). Printouts are always white.
- Phones (≤ 760px): menu slides in from the left (closes with ×, tapping outside, Escape or Android Back), bottom tab bar with up to 4 destinations + Menu, 16px form text, safe-area aware. Larger screens keep the sidebar.
- Icons: Lucide. Charts: Chart.js. All vendor files are self-hosted in `app/static/vendor/`.
