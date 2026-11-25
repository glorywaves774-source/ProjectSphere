# Changelog

## Stage 11: SaaS Billing and Integrations (19 Nov 2026)

### Added
- **Plans and payment.**
  - The Subscription page shows each plan's monthly and yearly price (yearly = two months free). Choosing a plan issues a numbered invoice (INV-2026-000001).
  - "Pay with card or mobile money" hands over to Paystack's secure checkout and comes straight back.
  - Plans you've outgrown can't be chosen, and Enterprise goes to sales.
- **Safe payment handling.**
  - Each payment is confirmed with Paystack's API, and the status, amount and currency must match the invoice. A tampered return link or a short payment can't switch a plan on.
  - The webhook (`/api/webhooks/paystack`) is rejected unless its HMAC-SHA512 signature matches. Even signed events are re-checked with the API rather than trusted.
  - Each payment reference is applied once, however often it arrives.
  - Paystack is given the organization's billing email, never a person's own address. Card details never touch ProjectSphere.
- **Subscriptions follow payments.**
  - A paid invoice moves the organization to its plan and sets the paid-until date.
  - Changing plan part-way through a paid period credits the unused time.
  - Paying ahead extends from the current end date.
- **Renewals.**
  - A daily job issues renewal invoices a week before each period ends and emails the subscription admins.
  - Seven days after an unpaid period ends, the subscription becomes past due: new work pauses and nothing is deleted. Paying restores it.
- **Invoices:** history on the Subscription page, an invoice page with payment attempts, and PDF invoices with bank-transfer details.
- **Super Admin billing console.**
  - Shows money actually collected this month and year, outstanding and overdue totals, and searchable invoices across organizations.
  - Offline payments (bank, mobile money, cheque, cash) can be recorded with a reference, and invoices can be voided with a reason.
  - Manual invoices can be issued, for example an Enterprise contract, optionally switching the plan on when paid. The console also shows the email outbox status.
- **Email notifications.**
  - Notifications (assignments, approvals, messages, meetings, reminders, announcements) and billing emails are written to an outbox in the same transaction as the change, so nothing is emailed for work that was undone.
  - The worker sends the outbox every minute (`flask send-mail` by hand). Emails carry only a title and a link.
  - Each person can turn emails off in My account.
- **API and integrations** (Professional plan and above):
  - Organization Admins create API keys with chosen access (projects, finance, indicators, submitting results, risks) and an expiry.
  - A key is shown once; only its SHA-256 hash is stored. Last use and call counts are tracked, and keys can be revoked instantly.
  - REST endpoints cover projects, tasks, budgets, expenses, indicators, results, risks and issues, with pagination and exact decimal amounts.
  - Results submitted from tools like KoboToolbox always wait for an M&E Officer to verify them.
  - Every API request runs under the key's organization with the same two-layer isolation. The limit is 120 requests a minute per key, and lapsed plans get a clear 402 response.
- **Deployment.**
  - New settings: `APP_BASE_URL`, `PAYSTACK_SECRET_KEY`, `PLATFORM_BANK_DETAILS`, `INVOICE_DUE_DAYS`, `RENEWAL_NOTICE_DAYS` and `BILLING_GRACE_DAYS`.
  - The worker now shares the mail settings and runs the outbox and renewal jobs.
  - The CSP allows forms to hand over only to `checkout.paystack.com`.
- **Database.** Migration `0011` adds four tables with row-level security, a platform-wide invoice number sequence, API access per plan and each user's email preference.
- **23 new automated tests** (285 in total). Paystack is simulated: checkout, return, mismatched amounts, forged and replayed webhooks.

## Stage 10: Risk and Procurement Management (19 Nov 2026)

### Added
- **Risk register** for every project (new Risks tab) and across the organization.
  - Each risk has a cause and consequence, a category, likelihood and impact (1–5), a score and level (low, medium, high, critical), a response (reduce, avoid, transfer, accept), mitigation and contingency plans, an owner and a next review date.
  - High and critical risks need a mitigation plan unless the risk is accepted.
  - A 5×5 heat map shows where open risks sit; tap a square to list them.
- **Risk reviews.**
  - Owners and managers reassess risks with a note, and every review is kept as dated history.
  - The project manager is told when a risk becomes critical.
  - A daily job reminds owners once when a review is overdue (also `flask risk-reminders`).
- **Issue log.**
  - Problems happening now, each with a priority, assignee, resolve-by date and comments.
  - A risk that happens can be escalated into a linked issue in one step.
  - The assignee resolves an issue (a resolution is required); only managers close it. Overdue issues are highlighted.
  - Field officers on the project team can log issues from the field.
- **Vendor register.**
  - Records the name, category, contacts, tax ID and registration number.
  - A different procurement officer approves each vendor, and a tax ID is required first.
  - Vendors can be suspended or blacklisted (with a reason), which blocks awards.
  - Each vendor shows its orders, total value and average rating.
- **Purchase requests.**
  - Items with quantities and estimated costs (live totals as you type), linked to a budget line.
  - The method follows the estimate: under 5,000 a single quotation, under 50,000 at least three quotations, otherwise an open tender.
  - Drafts stay private to the requester and procurement.
- **Approval.**
  - The requester can never approve their own request, and the project budget must be approved first.
  - The approver sees exactly what's left on the budget line after spending, pending expenses and other open purchases.
  - Going over budget needs an explicit override and a written reason.
- **Quotations and award.**
  - Procurement officers record quotations in any currency, converted at finance's rate or an entered rate. Expired quotations and blacklisted vendors are refused.
  - Awards go only to approved vendors. Fewer quotations than the method needs, or choosing anything but the cheapest valid one, requires a justification.
  - Awarding issues a numbered purchase order with a printable PDF.
- **Receipt.**
  - The requester or procurement confirms delivery and rates the vendor (1–5).
  - The purchase becomes an approved expense on the budget line, credited to the original approver and ready for finance to pay, so budgets and spending reports stay accurate automatically.
- **Exports:** Excel risk register with issue log, and Excel procurement register (estimates, awards, savings, ratings, linked expenses).
- **Integration.**
  - Project overview pages show a Risks & issues summary with the top risks.
  - Notifications cover risk ownership, critical risks, issue assignment and updates, requests waiting for approval, decisions, purchase orders and payments due.
  - The activity log and help assistant cover all of this.
- **Roles.** Project Managers can now raise purchase requests (procurement view), and Field Officers can see risks and log issues. Existing organizations are updated by the migration.
- **Database.** Migration `0010` adds eight tables with row-level security.
- **32 new automated tests** (262 in total).

## Stage 9: Monitoring and Evaluation (19 Nov 2026)

### Added
- **Results framework (logframe)** on a new M&E tab for each project.
  - Goal → outcomes → outputs, with assumptions, numbered automatically (G, 1, 2, 1.1, 1.2…).
  - Rules keep it well formed: one goal per project, each output sits under an outcome, and a result with outputs under it can't be removed.
  - M&E Officers manage every project's framework; project managers manage their own.
- **Indicators.**
  - Each has a name, definition, unit, whether it's a number or a percentage, whether higher or lower is better, and baseline and target values with their dates.
  - Also records the endline, how often data is collected, who is responsible, the means of verification and how results are broken down (for example by sex or district).
  - Indicators can be retired (history kept) or deleted while they have no verified results.
- **Reporting and verification.**
  - Field officers on the project team report actual values per period, and M&E Officers verify or reject them (rejecting needs a note).
  - Only verified results count. Nobody verifies their own result, a period can't be reported twice, and a future period or out-of-range value is refused.
- **Achievement and status.**
  - Achievement is how far each indicator has moved from baseline to target, and works for "lower is better" too.
  - Status (achieved, on track, at risk, off track, no data) compares achievement with how much time had passed when the result was measured.
  - Shown with progress bars, a "time passed" marker and a chart of actual results against the target over time.
- **Data-due reminders.** The next data date is worked out from each indicator's frequency. A daily background job reminds the responsible person once when data is overdue (also available as `flask me-reminders`).
- **Field monitoring visits.**
  - Each visit records the date, place, GPS coordinates ("Use my location" on phones, or typed in), purpose, people met, findings, recommendations and an overall assessment.
  - Photos and scanned forms (JPG, PNG, PDF) are checked like any upload and kept in the project's documents.
  - Visits start as private drafts. They're then submitted, and accepted or returned with a note by an M&E Officer or the project manager. Every visit gets a reference (VIS-2026-0001) and its own PDF.
- **Evaluations.**
  - Records the type (baseline, mid-term, endline, impact, special), evaluator (internal or external), dates, scope, key findings, recommendations and management response.
  - Ratings use the six OECD-DAC criteria (1–4), with an overall average, and the report can be linked from the project's documents.
  - Completed evaluations need findings, and the project team is notified.
- **M&E results page.** Organization-wide indicator counts and status chart, each project's average achievement, results waiting for verification, recent visits and evaluations in progress.
- **Reports.**
  - A donor-ready project PDF (ReportLab) covering:
    - overview and objectives;
    - a budget snapshot, only for people who can see budgets;
    - indicator performance and the full results framework table;
    - visits and evaluations with their ratings.
  - Excel indicator tracking tables for one project or all open projects, with every reported value and the visits.
  - The bundled DejaVu font prints names with accents and the cedi sign correctly.
- **Integration.**
  - Project overview pages show a Results summary. Notifications cover indicator responsibility, results to verify, verification decisions, visits to review, visit decisions and completed evaluations.
  - The activity log shows M&E actions. The assistant answers M&E questions.
- **Phones.**
  - Long page titles now wrap onto two lines instead of being cut off.
  - The results framework stacks neatly.
  - Location permission is limited to this site's own pages (Permissions-Policy).
- **Database.** Migration `0009` adds five tables with row-level security and links documents to visits.
- **45 new automated tests** (230 in total).

## Stage 8: Documents and Collaboration (19 Nov 2026)

### Added
- **Document library.**
  - One library for the organization plus a Documents tab on every project.
  - Search, and filter by category (report, proposal, work plan, budget, minutes, contract, policy, media, other), project, or archived files.
  - Each file shows its type, size, version and date, with a one-tap download.
- **Safe uploads.**
  - Accepts PDF, Word, Excel, PowerPoint, CSV, text, PNG and JPG files, up to 20 MB (adjustable).
  - Each file's contents are checked against its name. A program renamed to `.pdf`, an Office file containing macros, a broken Office file or a binary file posing as text is refused.
  - File names are cleaned, and files are stored under random names.
- **Version history.**
  - Uploading a new version keeps every earlier copy, with a "what changed" note.
  - Old versions can be downloaded, or deleted to save space.
- **Secure downloads.**
  - Every download is checked against the file's fingerprint (SHA-256), so altered files are caught.
  - Downloads are recorded in the activity log.
  - Files are always sent as downloads with locked-down headers, never opened inside ProjectSphere.
- **Document permissions.**
  - Project team members can upload to their projects. Document Managers and admins can upload anywhere.
  - The uploader, the project manager or a Document Manager can archive or delete a document.
  - Each organization only ever sees its own documents, enforced in the app and in the database.
- **Storage limits.** Storage used is measured from real file sizes and enforced per plan (2 / 10 / 50 GB). The library shows usage against the plan.
- **Cloud storage.** Local disk for development; any S3-compatible store (AWS S3, Cloudflare R2, Backblaze B2, DigitalOcean Spaces) in production, with server-side encryption.
- **Notifications.** A bell on every page shows unread notifications, and each one opens the right page. They cover:
  - tasks assigned, sent for review, approved or returned;
  - budgets submitted or decided; expenses submitted, decided or paid;
  - timesheets submitted or decided; invitations accepted; support replies;
  - new project documents; messages; announcements; meeting invitations, changes and cancellations.
- **Messages.**
  - Private one-to-one chats and named group chats, with unread counts in the menu bar and inbox.
  - Only participants can read a conversation.
  - On phones the inbox and the conversation show one at a time, and the message box stays just above the tab bar.
- **Project discussions.** Every project has a Discussion tab shared by its manager and team. The team is kept in sync automatically.
- **Announcements.** Organization Admins post news to everyone and can pin it. Pinned and recent announcements appear on every dashboard.
- **Meetings.**
  - Schedule a meeting with a date, time (in the organization's time zone), length, place or video link, agenda and invitees. Choosing a project fills in its team.
  - Any attendee can record minutes. The organiser, the project manager or an admin can edit or cancel the meeting, and attendees are told.
  - "Add to calendar" downloads an `.ics` file for Outlook, Google or Apple Calendar.
  - Meetings show on the calendar and on the dashboard.
- **Help.** The ProjectSphere Assistant answers questions about documents, versions, storage, notifications, messages, announcements and meetings.
- **Database.** Migration `0008` adds ten tables with row-level security.
- **31 new automated tests** (185 in total). They include S3 storage (simulated with moto) and checks for disguised and dangerous files.

### Deployment
- `boto3` added to `requirements.txt`; `moto` added to `requirements-dev.txt`.
- `render.yaml` adds the storage settings. Production should use S3-compatible storage, because Render's own disk is wiped on every deploy.

## Stage 7: Resource and Team Management (19 Nov 2026)

### Added
- **Project allocations.** Project managers set what share of each team member's week (0–100%) a project needs, right from the project's team list.
- **Weekly capacity.** Each person has normal weekly hours (default 40), set from their page in Team.
- **Weekly timesheets.**
  - Log hours per day against a project or one of its tasks, in quarter-hour steps, with an optional note per row.
  - Totals update as you type. Save as you go, then submit the week.
  - Limited to open projects you're part of, 0–24 hours per entry and no more than 24 hours a day.
  - Submitted and approved weeks are locked. On phones each row becomes a card with the seven days side by side.
- **Timesheet approval.**
  - HR (and admins) approve anyone's timesheet, heads of department approve their department's, and project managers approve timesheets with hours on their projects.
  - Nobody approves their own.
  - Returning a week needs a note, which the person sees.
- **Workload.**
  - Each person's total allocation across open projects, and the remaining estimated work on tasks due in the next four weeks against capacity.
  - Open and overdue tasks, and hours logged in the last seven days.
  - People over capacity are highlighted, and "room for more" is counted.
- **Team performance.** For 30, 90, 180 or 365 days: tasks completed, the share finished on time, what's overdue now, and hours logged (total and per week). It's built only from real task and timesheet data.
- **Actual hours.**
  - Task pages show hours logged against the estimate, with a warning when over.
  - Project pages show hours logged by each person.
  - Each person's page shows a workload summary.
- **Help.** The ProjectSphere Assistant answers questions about timesheets, approvals, allocation, workload and performance.
- **Database.** Migration `0007` adds the timesheets and time entries tables with row-level security, plus allocation and weekly hours.
- **16 new automated tests** (154 in total).


## Stage 6: Budget and Financial Management (19 Nov 2026)

### Added
- **Project budgets.**
  - Budget lines with an automatic code (BL-01, BL-02…), category (personnel, travel, equipment, supplies, services, training, operations, other), quantity, unit and unit cost, plus an optional link to a task.
  - The page shows the total allocated against the project's planned budget, and how much is left unallocated.
- **Budget approval.** Draft → submitted → approved. Approvers (with budget-approve rights) can return a submitted budget or reopen an approved one, and must give a reason. An approved budget is locked.
- **Expenses.**
  - Recorded against an approved budget line with a date, payee, invoice number, amount and currency.
  - Each gets an automatic reference (EXP-2026-0001).
  - Save as a draft, or send for approval.
- **Spending approval and payment.**
  - Approve or reject (rejection needs a reason). Nobody can approve their own expense.
  - Finance staff record payment by bank transfer, mobile money, cheque, cash or card, with a reference.
  - The full trail of who requested, decided and paid, and when, is kept.
- **Over-budget control.** Expenses that would exceed a line are flagged when submitted. Approving them needs an explicit override and a written reason, which are recorded.
- **Multiple currencies.**
  - Projects keep their own currency, and expenses can be in any currency.
  - Finance sets exchange rates to the organization's base currency, and these convert expenses and total figures across projects.
  - A rate can be overridden for a single expense.
- **Variance and performance.**
  - Budget versus actual for every line (spent, awaiting approval, remaining, percentage used, over-budget lines in red).
  - Earned-value figures: CPI (value of work done against money spent) and SPI (work done against time elapsed).
  - A budget-and-spending-by-category chart.
- **Finance overview.** For finance, directors and auditors:
  - Totals across open projects in the base currency.
  - Expenses and budgets waiting for a decision.
  - Monthly spending and spending by category.
  - Utilisation by project, comparing money used with work done.
  - A warning when a currency has no exchange rate.
- **Expense register.** Search, filter by status, project or date, and see "my requests".
- **Excel reports.**
  - Budget vs actual (with live totals and a performance sheet) and the expense register, both protected against formula injection.
  - PDF reports arrive with the reporting module in Stage 9.
- **Access rules.**
  - Project managers and finance staff build budgets and record spending.
  - Approvers approve, and only finance staff record payments.
  - Auditors can see everything but change nothing.
  - Staff can't see budgets.
- **Help.** The ProjectSphere Assistant answers questions about budgets, expenses, approvals, payments, variance and exchange rates.
- **Database.** Migration `0006` adds the budget lines, expenses and exchange rates tables with row-level security, plus budget approval fields on projects.
- **13 new automated tests** (138 in total).


## Stage 5: Task and Scheduling Management (19 Nov 2026)

### Added
- **Tasks with a work breakdown.** Add tasks and sub-tasks to any project, numbered automatically by their place in the breakdown (1, 1.2, 1.2.1). Each task can have an assignee, priority, start and due dates, an estimate in hours and a description. Assigning someone adds them to the project team.
- **Milestones.** Key dates (such as a funder report or a handover) shown as diamonds.
- **Dependencies.** "This task waits for that one" links. A task can't be completed until everything it waits for is done. Loops, self-links, duplicates and links between a sub-task and its own parent are refused.
- **Critical path.** Standard critical-path calculation marks the chain of tasks with no slack (in red), so managers know which delays would push the end date.
- **Automatic progress.** Project progress is now calculated from its tasks, weighted by estimated hours (or equally when there are no estimates). Parent tasks summarise their sub-tasks. A parent can't be marked done while sub-tasks are open.
- **Approvals.** Tasks can be marked "needs approval". Finishing one sends it for review. Approvers (project managers, directors and admins) approve it or send it back with a required note, and who approved it and when is recorded.
- **Task page.** Status and progress updates with an optional note, comments, sub-tasks, a "waits for" and "blocks" list, and edit and delete.
- **Gantt chart.**
  - Bars filled to show progress, critical-path colouring, milestones, dependency arrows, a "today" line and the planned project end.
  - The task names stay pinned on the left while the timeline scrolls. Zoom by days, weeks or months.
  - It can be printed, and opens zoomed to months on phones. It is drawn without any third-party code, so the strict security policy stays in place.
- **My tasks.** Everything assigned to you as a list or board, with tabs for open, overdue and done, plus a "To approve" tab for approvers.
- **Calendar.** A month view of task deadlines, milestones and project end dates. Filter to one project or only your own tasks. On phones it shows as an agenda list.
- **Project tabs.** Each project now has Overview, Tasks, Gantt and Calendar tabs.
- **Rules.**
  - The project team (with task-manage rights) plans tasks.
  - The person assigned can update their own task.
  - Team members can comment.
  - Closed projects' tasks are read-only.
- **Help.** The ProjectSphere Assistant answers questions about tasks, milestones, dependencies, the critical path, the Gantt chart, approvals and the calendar.
- **Database.** Migration `0005` adds the tasks, task dependencies and task comments tables with row-level security.
- **17 new automated tests** (125 in total), including a worked critical-path example.

### Changed
- Manual progress on project status updates is ignored once a project has tasks, because progress then comes from the tasks.


## Stage 4: Project Management (19 Nov 2026)

### Added
- **Project registration.**
  - Each project gets an automatic ID (`PRJ-2026-0001`, numbered separately for each organization).
  - A project records its name, one-line summary, description, objectives, category, department, project manager, status, priority, start and planned end dates, planned budget and currency, location, and funder or client.
- **Project categories.** Ten common categories are created for every organization, including existing ones. Organization Admins can add colour-coded categories or delete them.
- **Projects list and board.**
  - Search by name, ID, location or funder.
  - Filter by status, category, department, priority, "my projects" or "delayed only", and sort by update date, due date, priority, name or newest.
  - Switch between a list and a Kanban board grouped by status.
  - On phones, filters fold behind a Filters button.
- **CSV export.** Downloads exactly the filtered list and opens cleanly in Excel. It is protected against spreadsheet formula injection.
- **Project page.** Shows status, health and priority; progress next to time elapsed; due date with an overdue warning; budget; overview and objectives; details; and the team.
- **Status updates.** The team posts dated updates with health (on track, at risk, off track) and progress. Only the project manager can change the status, and every change is kept in a timeline.
- **Project team.** Add people with a role on the project. The project manager can't be removed until another manager is set.
- **Closure.** Record the outcome (completed or cancelled), the actual end date, a closure summary and lessons learned. Closing frees a project slot on the plan, and a closed project can be reopened.
- **Access rules.**
  - Anyone with project access can view projects.
  - People with manage rights can register projects, and edit the ones they manage or registered.
  - Directors and admins with portfolio oversight can edit every project.
  - Team members can post updates.
- **Plan limits.** Open projects (planning, active, on hold) count towards the plan's project limit, which the usage meters now show.
- **Portfolio.** For directors, admins and auditors:
  - Totals, active, delayed and planned budget.
  - Health, status and category charts.
  - Open projects needing attention, deadlines in the next 30 days, the on-time completion rate, and performance by department.
- **Executive dashboard.** Live project figures (total, active, delayed, completed), a "My projects" list, and a new "Register your first project" step in the getting-started list.
- **Help.** The ProjectSphere Assistant answers questions about projects, the board, updates, closure, the portfolio and categories.
- **Database.** Migration `0004` adds the projects, project categories, project members and project updates tables with row-level security.
- **16 new automated tests** (108 in total).


## Stage 3: Organization Management (19 Nov 2026)

### Added
- **Departments.** Create departments with a short code, a head and a description. See who belongs to each one and filter the team by department. Deleting a department keeps its people.
- **Staff profiles.** Records now hold department, staff ID, employment type (full-time, part-time, contract, consultant, volunteer, intern or national service), start date, phone and job title.
- **Add staff directly.** Organization Admins and HR Officers can create an account and hand over a one-time password (shown once). The person must choose their own password at first sign-in.
- **Email invitations.**
  - Invite someone with their roles, department and job title. They open a link, choose their password and land in the workspace.
  - Links work once, expire after 7 days, and can be resent (the old link stops working) or revoked.
  - Only a fingerprint of each link is stored, so a database leak can't be used to join.
  - Without an email server, you get the link to send yourself.
  - Open invitations reserve a seat on the plan.
- **Edit team members.** Change details, department and roles; deactivate (signs them out everywhere), reactivate, or issue a one-time password.
- **Custom roles and editable permissions.** Create roles, duplicate an existing role as a starting point, change built-in roles' permissions, and delete unused custom roles. Changes take effect on each person's next page.
- **Safeguards against privilege escalation.**
  - You can only give roles, or grant permissions, that you already have.
  - You can't change, reset or deactivate someone with more access than you.
  - The Organization Admin role always keeps full access.
  - An organization always keeps at least one active Organization Admin.
  - You can't deactivate yourself.
- **Optional email sending** through any SMTP provider (`MAIL_SERVER`, `MAIL_PORT`, `MAIL_USERNAME`, `MAIL_PASSWORD`, `MAIL_FROM`).
- **Help.** The ProjectSphere Assistant now answers questions about invitations, members, departments and custom roles.
- **Database.** Migration `0003` adds the departments and invitations tables with row-level security, plus the new staff fields.
- **22 new automated tests** (92 in total). They cover escalation attempts, isolation between organizations, invitation expiry and reuse, and plan limits.

### Changed
- HR Officers now also have the view permissions that Staff Members and Field Officers hold, so they can manage those people. The upgrade applies this to existing organizations too.


## Stage 2: SaaS Administration (19 Nov 2026)

### Added
- **Plan management.** Super Admins can create plans and edit prices, limits, features and visibility. Changes apply straight away to the pricing page, sign-up form and every subscriber's limits. Plans with subscribers can be retired but never deleted, and a plan's code can't change.
- **Create organizations for customers.** Set the plan and whether it starts as a trial or paying, and name the first admin. A strong one-time password is shown once and is never cached by the browser.
- **Must choose own password.** Anyone given a one-time password (new organizations, password resets, new Super Admins) is held on the password page until they choose their own.
- **Subscription control per organization.** Change the plan, status (trial, active, past due, cancelled), billing cycle, trial end date or paid-until date. This is useful for bank-transfer and mobile-money customers until online payments arrive in Stage 11.
- **Edit organization details** from the console.
- **User support tools.** Unlock, reset password, deactivate (signs the person out everywhere) and reactivate. The last active Organization Admin of an organization can't be deactivated.
- **Support desk.**
  - Organizations raise requests under Help & support; staff see their own requests and Organization Admins see the whole organization's.
  - Kayron staff see every organization's requests, filter and search them (including by PS-number), reply, add internal notes the customer never sees, set status and priority, and assign them.
  - A customer reply reopens the request.
- **Platform settings.** Support and sales emails, self-service sign-up on or off, free-trial length (0 = start as paying), and an announcement banner (information, warning or good news) shown in every workspace.
- **Super Admin team.** Add colleagues (one-time password), deactivate or reactivate them, and change your own password. There is always at least one active Super Admin, and you can't deactivate yourself.
- **"Needs attention" on the overview.** Trials ending within 7 days, past-due customers and open support requests.
- **Assistant.** The ProjectSphere Assistant now answers questions about every Stage 2 feature.
- **Database.** Migration `0002` adds support tables with row-level security, plus the "must change password" flag.
- **22 new automated tests** (70 in total).

### Changed
- The Stage 1 row-level-security helper now takes a list of tables per stage, so older migrations never touch tables created later.
- Super Admins can't be created from an existing organization user's email.


## Stage 1 — Foundation and Architecture (18 Nov 2026)

### Added
- **Multi-tenant SaaS core.** Each subscribing organization gets its own isolated workspace.
- **Two-layer tenant isolation.** Automatic filtering in the application, plus PostgreSQL row-level security that the database enforces on its own (even for hand-written SQL).
- **Self-service sign-up.** An organization registers, picks a plan and starts a 14-day free trial. Eleven ready-made roles and the first Organization Admin are created automatically.
- **Secure sign-in.** Lockout after 5 wrong passwords (15 minutes), same message for wrong password and unknown email, sign-out on other devices after a password change, sign-out only by button (not by link).
- **Roles and permissions.** 30 permissions across all planned modules; 11 roles from Executive Director to read-only Auditor. The Platform Super Admin is kept separate from organization roles.
- **Subscription plans.** Starter (GH₵99), Professional (GH₵249), Business (GH₵499), Enterprise (custom), with user, project and storage limits. Trials end automatically through an hourly background job.
- **Organization workspace.** Live dashboard, team list with search and filters, roles and permissions, activity log, organization profile, plan and usage, and personal account settings.
- **Super Admin console.** Platform overview with monthly revenue, trials, sign-ups chart and plan mix; organization search, filters and detail; suspend and reactivate; plans; platform-wide audit log.
- **ProjectSphere Assistant.** Answers "how do I…" questions from the help guide and only links to pages your role can open. Nothing is sent to outside services.
- **Premium interface.** Spatial glass design with deep navy sidebar and purple-blue accents, light and dark modes, phone menu and bottom tab bar, and sign-in pages that blend soft neumorphism with glass.
- **Security.** CSRF protection, strict content security policy, HTTPS and HSTS in production, secure cookies, rate limits, scrypt password hashing and audit logging.
- **Deployment.** Render blueprint for the web app, background worker, PostgreSQL and Redis.
- **48 automated tests** run against real PostgreSQL using the real migrations.

### Planned for later stages
- Stages 4–12: projects, tasks and Gantt, budgets, resources, documents, M&E, risk and procurement, Paystack billing, final security audit.
