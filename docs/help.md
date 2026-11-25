# Laari ProjectSphere — Help guide

This guide powers the ProjectSphere Assistant. Each `##` section is one answer.
`endpoint` is the page the assistant may link to (only shown to roles that can open it).

## Dashboard: your workspace at a glance
<!-- endpoint: workspace.dashboard who: everyone -->
<!-- keywords: home overview start dashboard getting started checklist -->
The dashboard shows your team size, your plan and trial days left, how much of your plan you are using, who holds which role, and recent activity. The getting-started checklist ticks itself off as you complete each step.

## Team: adding and viewing team members
<!-- endpoint: workspace.team who: an Organization Admin or HR Officer -->
<!-- keywords: team add invite staff member people colleague user users members -->
Open Team to see everyone in your organization. Search by name, email, job title or staff ID, and filter by department or status. To bring someone in, choose Invite to send them a link where they set their own password, or Add staff to create the account yourself and hand over a one-time password. Your plan sets how many members you can have, and open invitations count towards it.

## Roles: what each role can do
<!-- endpoint: workspace.roles who: an Organization Admin, HR Officer, Executive Director or Auditor -->
<!-- keywords: role roles permission permissions access rbac can allowed rights -->
Each person has one or more roles: Organization Admin, Executive Director, Project Director, Project Manager, Finance Officer, M&E Officer, HR Officer, Procurement Officer, Field Officer, Staff Member and Auditor. Roles & access lists every role and the exact permissions it includes. The Auditor role is read-only. With permission to manage roles you can create custom roles, duplicate one as a starting point, or change a built-in role's permissions. The Organization Admin role always keeps full access.

## Organization profile: updating your details
<!-- endpoint: workspace.organization who: an Organization Admin -->
<!-- keywords: organization profile name address phone currency timezone details settings company ngo -->
Organization profile holds your name, type, country, contact details, default currency and time zone. Everyone can view it; only an Organization Admin can change it. Every change is recorded in the activity log.

## Subscription: your plan and usage
<!-- endpoint: workspace.subscription who: an Organization Admin, Finance Officer or Executive Director -->
<!-- keywords: plan plans subscription billing upgrade change price pay payment trial usage limit limits -->
Subscription shows your current plan, trial days left and how much of each limit you are using. Plans are Starter (GH₵99/month), Professional (GH₵249), Business (GH₵499) and Enterprise (custom). Online payment with Paystack arrives in Stage 11; until then raise a support request in Help & support to upgrade and our team will switch your plan.

## Activity log: who did what
<!-- endpoint: workspace.activity who: an Organization Admin, Executive Director or Auditor -->
<!-- keywords: activity audit log history trail who changed events -->
The activity log records sign-ins, failed sign-ins, profile changes and other important events with the time, person and device address. Search it by keyword.

## My account: profile and password
<!-- endpoint: workspace.account who: everyone -->
<!-- keywords: account password change reset my profile name phone job title sign out devices -->
In My account you can update your name, job title and phone, and change your password. Changing your password signs you out on every other device.

## Dark mode and light mode
<!-- keywords: dark light mode theme night colours colors appearance -->
Use the moon or sun button in the top bar (or on the sign-in page) to switch. ProjectSphere follows your device setting until you choose, then remembers your choice on this device. Printouts are always white.

## Signing in and account lockout
<!-- keywords: sign in login log locked lockout forgot password attempts -->
Sign in with your work email and password. After 5 wrong passwords in a row the account is locked for 15 minutes to protect it. If your organization has been suspended, members cannot sign in until it is reactivated.

## Platform: suspending or reactivating an organization
<!-- endpoint: admin.organizations who: the Platform Super Administrator -->
<!-- keywords: suspend reactivate organization tenant block disable platform admin -->
Super Admins open Organizations, choose an organization and use Suspend or Reactivate. Members of a suspended organization cannot sign in. The action is recorded in the audit log.

## Platform: revenue and growth
<!-- endpoint: admin.dashboard who: the Platform Super Administrator -->
<!-- keywords: revenue mrr growth signups analytics platform overview -->
The Super Admin overview shows monthly recurring revenue from paying subscriptions, organizations by subscription status, sign-ups over the last six months and plan mix. Trials do not count as revenue.

## Platform: subscription plans
<!-- endpoint: admin.plans who: the Platform Super Administrator -->
<!-- keywords: plans packages pricing limits create plan -->
Plans lists each package with its price, limits and number of subscribers. Use New plan to add one, or Edit to change prices, limits, features and visibility. Changes apply straight away to the pricing page and to every subscriber's limits. A plan with subscribers can be retired (no new sign-ups) but not deleted.

## Help & support: asking the Kayron team for help
<!-- endpoint: workspace.support who: everyone -->
<!-- keywords: support help ticket request contact problem issue bug question kayron upgrade -->
Open Help & support and choose New request. Pick a category and priority, describe the problem and send it. Replies from the support team appear on the request; replying again reopens it. Organization Admins see every request from the organization; other people see the ones they raised.

## Temporary password: first sign-in
<!-- keywords: temporary password first sign in new account one-time change -->
If a Super Admin set up your account or reset your password, you'll be asked to choose your own password the first time you sign in. Until you do, the other pages stay locked.

## Platform: creating an organization for a customer
<!-- endpoint: admin.organization_new who: the Platform Super Administrator -->
<!-- keywords: create new organization tenant customer onboard add workspace setup -->
Super Admins open Organizations and choose New organization. Enter the organization, the plan, whether it starts on a trial or as paying, and the first admin's name and email. A one-time password is shown once; share it privately. The admin must choose their own password at first sign-in.

## Platform: changing a customer's plan or payment status
<!-- endpoint: admin.organizations who: the Platform Super Administrator -->
<!-- keywords: change plan upgrade downgrade activate paying payment paid until extend trial subscription status -->
Open the organization and use the Subscription panel. Choose the plan and status (Trial, Active, Past due or Cancelled), the billing cycle, the trial end date or the date they've paid up to. Every change is recorded in the audit log.

## Platform: helping a locked-out user
<!-- endpoint: admin.organizations who: the Platform Super Administrator -->
<!-- keywords: unlock locked reset password user deactivate reactivate forgot -->
Open the user's organization. In the Users table you can Unlock, Reset password (a one-time password is shown once), Deactivate or Reactivate. An organization's last active Organization Admin can't be deactivated.

## Platform: answering support requests
<!-- endpoint: admin.support who: the Platform Super Administrator -->
<!-- keywords: support desk tickets requests reply answer internal note assign priority -->
The Support desk lists open requests from every organization. Open one to reply, add an internal note the customer can't see, change its status and priority, or assign it to a colleague.

## Platform: settings, announcement banner and sign-up
<!-- endpoint: admin.settings who: the Platform Super Administrator -->
<!-- keywords: settings announcement banner maintenance signup sign-up trial length support email sales email -->
Platform settings control the support and sales email addresses, whether organizations can sign up by themselves, the free-trial length, and an announcement banner shown in every workspace.

## Platform: adding another Super Admin
<!-- endpoint: admin.admins who: the Platform Super Administrator -->
<!-- keywords: super admin add colleague staff team platform administrator -->
Open Super Admins and use Add a Super Admin. They receive a one-time password and must change it at first sign-in. There must always be at least one active Super Admin.

## Inviting someone by email
<!-- endpoint: workspace.invite who: an Organization Admin or HR Officer -->
<!-- keywords: invite invitation email link join new member colleague add someone resend revoke expired -->
Go to Team and choose Invite. Enter their email, optionally their name, job title and department, and pick their roles. They get a link valid for 7 days to join and choose their own password. If email isn't set up yet, you'll get the link to send them yourself. Pending invitations appear under Team, where you can resend or revoke them.

## Editing a team member, their roles or department
<!-- endpoint: workspace.team who: an Organization Admin or HR Officer -->
<!-- keywords: edit member change role department staff id employment start date deactivate reactivate one-time password reset colleague -->
Open Team and choose the person's name. You can update their details, department and roles, issue a one-time password, or deactivate and reactivate them. You can only give roles whose access you already have, and you can't change someone who has more access than you. An organization always keeps at least one active Organization Admin.

## Departments
<!-- endpoint: workspace.departments who: an Organization Admin -->
<!-- keywords: department departments unit units office programme team structure head of department -->
Departments group your people, for example Programmes, Finance or Field Operations. An Organization Admin can create departments, set a head and description, and delete them. Deleting a department keeps its people, who are left without a department. Assign people to departments from their page in Team.

## Creating a custom role
<!-- endpoint: workspace.roles who: an Organization Admin -->
<!-- keywords: custom role new role create role permissions duplicate copy role access level -->
In Roles & access choose New role, or Duplicate an existing role to start from it. Give it a name, tick its permissions and save. You can only grant permissions you have yourself. Custom roles with nobody assigned can be deleted. Changes take effect on each person's next page.

## Projects: registering a new project
<!-- endpoint: workspace.project_new who: a Project Manager, Project Director or Organization Admin -->
<!-- keywords: new project register create project add project start project id code -->
Open Projects and choose New project. Give it a name, summary, description and objectives, then its dates, planned budget, category, department, manager, location and funder. A project ID such as PRJ-2026-0001 is assigned automatically. Open projects count towards your plan's project limit.

## Projects: list, board and finding projects
<!-- endpoint: workspace.projects who: everyone with access to projects -->
<!-- keywords: projects list board kanban filter search export csv excel my projects delayed overdue category -->
Projects shows every project as a list or a board grouped by status. Search by name, ID, location or funder, and filter by status, category, department, priority, your own projects, or delayed projects only. Export CSV downloads exactly what you're looking at, ready for Excel.

## Projects: posting a status update
<!-- endpoint: workspace.projects who: the project team -->
<!-- keywords: update status health progress report on track at risk off track post update -->
Open a project and use Status updates to share progress, blockers or decisions. You can also set health (on track, at risk, off track) and progress. The project manager can change the status (planning, active, on hold). Every change is kept in the project's timeline.

## Projects: team, editing and closing
<!-- endpoint: workspace.projects who: the project manager -->
<!-- keywords: project team member add remove edit project close project complete cancel reopen lessons learned closure -->
The project manager, the person who registered the project, or anyone with portfolio oversight can edit it, add or remove team members, and close it. Closing records whether it was completed or cancelled, the actual end date, a closure summary and lessons learned, and frees a project slot on your plan. Closed projects can be reopened.

## Portfolio: performance across all projects
<!-- endpoint: workspace.portfolio who: an Executive Director, Project Director, Organization Admin or Auditor -->
<!-- keywords: portfolio overview analytics performance delayed health budget department executive dashboard -->
Portfolio brings every project together: totals, active and delayed projects, planned budget, health and status charts, projects by category, deadlines in the next 30 days, and performance by department. Everything comes from live project data.

## Project categories
<!-- endpoint: workspace.project_categories who: an Organization Admin -->
<!-- keywords: category categories project type classification colour -->
Each organization starts with common categories such as Water & sanitation, Health and Education. An Organization Admin can add categories with a colour, or delete them; projects in a deleted category become uncategorised.

## Tasks: breaking a project into tasks and sub-tasks
<!-- endpoint: workspace.projects who: the project team -->
<!-- keywords: task tasks add task sub-task subtask work breakdown wbs structure estimate assign assignee -->
Open a project and choose the Tasks tab, then Task to add one. Put a task under another to build the work breakdown; numbers like 1.2.1 show where each task sits. Give tasks an assignee, dates, priority and an estimate in hours. Project progress is calculated from its tasks, weighted by their estimates.

## Milestones
<!-- endpoint: workspace.projects who: the project team -->
<!-- keywords: milestone milestones key date deliverable diamond -->
A milestone marks a key date, such as a report due to a funder, rather than work. Add one with Milestone on the project's Tasks tab. Milestones appear as diamonds on the Gantt chart and calendar.

## Dependencies and the critical path
<!-- endpoint: workspace.projects who: the project team -->
<!-- keywords: dependency dependencies depends waits for blocked predecessor successor critical path slack -->
On a task's page, use Add dependency to say it waits for another task. A task can't be marked done until everything it waits for is done, and ProjectSphere refuses loops. The critical path is the chain of tasks with no slack: if any of them slips, the project finishes late. These tasks are marked in red.

## Gantt chart
<!-- endpoint: workspace.projects who: everyone with access to projects -->
<!-- keywords: gantt chart schedule timeline bars zoom print -->
The Gantt tab draws each task as a bar from its start to its due date, filled to show progress, with arrows for dependencies, a red dashed line for today and diamonds for milestones. Zoom by days, weeks or months, and print it for meetings.

## Updating my tasks and approvals
<!-- endpoint: workspace.my_tasks who: everyone with tasks -->
<!-- keywords: my tasks assigned to me update status progress done approve approval review send back overdue -->
My tasks lists everything assigned to you, as a list or a board, with tabs for open, overdue and done. Open a task to change its status and progress or add a comment. If a task needs approval, finishing it sends it for review; approvers find it under To approve and can approve it or send it back with a note.

## Calendar
<!-- endpoint: workspace.calendar_view who: everyone with tasks -->
<!-- keywords: calendar month deadlines due dates schedule agenda -->
Calendar shows task due dates, milestones and project end dates by month. Filter to one project or only your own tasks. On phones it shows as an agenda list.

## Budget: building and approving a project budget
<!-- endpoint: workspace.projects who: the project manager or a Finance Officer -->
<!-- keywords: budget budget lines allocation build budget category quantity unit cost submit approve budget envelope -->
Open a project and choose the Budget tab. Add budget lines with a category (personnel, travel, equipment and so on), quantity, unit and unit cost, optionally linked to a task. When it's ready, choose Submit for approval. An approver (such as the Executive Director or Project Director) approves it, or returns it with a reason. An approved budget is locked; an approver can reopen it for revision.

## Expenses: recording spending
<!-- endpoint: workspace.expenses who: the project manager or a Finance Officer -->
<!-- keywords: expense expenses spending record expense receipt invoice payee pay spend money cost request -->
Once a project's budget is approved, choose Record expense on the Budget tab. Pick the budget line, date, description, payee, invoice number and amount, then save a draft or send it for approval. Expenses in another currency are converted with the rate set by finance, or one you enter.

## Approving or rejecting spending
<!-- endpoint: workspace.finance who: an approver (Executive Director, Project Director or Organization Admin) -->
<!-- keywords: approve expense reject expense approval spending over budget segregation own expense decision -->
Expenses waiting for a decision appear on Finance and in Expenses under Awaiting approval. Open one to approve or reject it; rejecting needs a reason. You can never approve your own expense. If approving would take a budget line over budget, you must tick the over-budget box and explain why, and the expense is flagged.

## Recording payment
<!-- endpoint: workspace.expenses who: a Finance Officer -->
<!-- keywords: pay paid payment bank transfer mobile money momo cheque mark paid -->
Finance staff open an approved expense and record the payment date, method (bank transfer, mobile money, cheque, cash or card) and reference. The expense is then marked Paid.

## Budget vs actual and variance
<!-- endpoint: workspace.finance who: everyone with access to budgets -->
<!-- keywords: variance budget vs actual utilisation remaining over budget cpi spi earned value performance excel report export -->
Each project's Budget tab compares every line's budget with what's been spent and what's awaiting approval, with remaining amounts and lines over budget in red. CPI (cost performance index) compares the value of work done with money spent: below 1.00 means spending is running ahead of progress. Download the full report, or the expense register, as Excel.

## Exchange rates and multiple currencies
<!-- endpoint: workspace.exchange_rates who: a Finance Officer -->
<!-- keywords: exchange rate currency currencies usd eur ghs conversion multi-currency base currency -->
Projects can have their own currency, and expenses can be paid in any currency. Finance staff set how much one unit of each currency is worth in the organization's base currency under Finance, then Exchange rates. These rates convert expenses and total projects across currencies.

## Timesheets: logging my hours
<!-- endpoint: workspace.timesheet who: everyone -->
<!-- keywords: timesheet timesheets log hours time tracking hours worked week submit attendance -->
Open My timesheet. For each piece of work, choose the project or task, then enter hours for each day in quarter-hour steps (for example 7.5). Totals update as you type. Save as you go, then choose Submit week. Once submitted, the week is locked unless an approver returns it.

## Timesheets: approving
<!-- endpoint: workspace.timesheet_approvals who: HR, heads of department and project managers -->
<!-- keywords: approve timesheet return timesheet approvals hours review -->
HR Officers and Organization Admins can approve anyone's timesheet; heads of department approve their department's; project managers approve timesheets with hours on their projects. Nobody approves their own. Open To approve on My timesheet, check the week, then approve it or return it with a note saying what to correct.

## Allocating people to projects
<!-- endpoint: workspace.projects who: the project manager -->
<!-- keywords: allocate allocation resource planning percent share of week capacity assign people -->
On a project's Overview, each team member has a "% of week" box. The project manager sets how much of each person's week the project needs. Allocations across all open projects add up on the Workload page, where anyone over 100% is flagged.

## Workload: who is over capacity
<!-- endpoint: workspace.workload who: HR, directors, admins and auditors -->
<!-- keywords: workload capacity overloaded over capacity availability resources balance -->
Workload shows each person's total allocation across open projects, the remaining estimated work on their tasks due in the next four weeks against their weekly hours, open and overdue tasks, and hours logged in the last seven days. People over capacity are highlighted. Weekly hours are set on each person's page in Team.

## Team performance
<!-- endpoint: workspace.performance who: HR, directors, admins and auditors -->
<!-- keywords: performance staff performance delivery on time completed tasks productivity monitoring -->
Performance shows, for each person over 30, 90, 180 or 365 days, the tasks they completed, how many of them were finished on time, what's overdue now, and hours logged. It's built from real task and timesheet data, for supportive conversations and fair workload decisions.

## Documents: uploading and finding files
<!-- endpoint: workspace.documents who: everyone who can view documents -->
<!-- keywords: documents upload file library pdf word excel powerpoint attachment store share report proposal contract -->
Open Documents to see the organization library and every project's files. Search by title, or filter by category, project or archived files. To add a file choose Upload, pick the file, a category and where it belongs (a project or the organization library). PDF, Word, Excel, PowerPoint, CSV, text, PNG and JPG files up to 20 MB are accepted. Files are checked when uploaded: a file whose contents don't match its name, or an Office file containing macros, is refused for safety. Project team members can upload to their projects; Document Managers and admins can upload anywhere.

## Documents: versions, comments and downloads
<!-- endpoint: workspace.documents who: everyone who can view documents -->
<!-- keywords: version history new version previous version download comment review archive delete fingerprint -->
Open a document to see all its versions. Upload new version keeps the old copies, so you can always download an earlier one. Add comments for review notes. Each download is checked against the file's fingerprint, so you know it hasn't been altered, and every download is recorded in the activity log. The person who uploaded a document, the project manager or a Document Manager can archive it (hide it from the library) or delete it.

## Storage space
<!-- endpoint: workspace.documents who: everyone -->
<!-- keywords: storage space full limit quota gb mb plan upgrade -->
The Documents page shows how much storage your organization uses against your plan (2 GB on Starter, 10 GB on Professional, 50 GB on Enterprise). Every version counts. When you're near the limit, delete old versions you no longer need or upgrade your plan on the Subscription page.

## Notifications
<!-- endpoint: workspace.notifications who: everyone -->
<!-- keywords: notifications bell alerts unread updates assigned approval reminder -->
The bell at the top of every page shows how many notifications you haven't read. You're notified when a task is assigned to you or approved, when something needs your approval (tasks, budgets, expenses, timesheets), when a decision is made on your request, when someone messages you, when a document is added to your project, about announcements and meeting invitations. Tap a notification to open it, or choose Mark all read.

## Messages and project discussions
<!-- endpoint: workspace.messages who: everyone -->
<!-- keywords: message chat inbox direct message group chat discussion conversation talk team -->
Open Messages and choose New. Pick one person for a private chat, or two or more people and a group name for a group chat. Only the people in a conversation can read it. Every project also has a Discussion (the tab on the project page) shared by its manager and team. The messages icon at the top shows unread messages. On a computer, Enter sends and Shift+Enter starts a new line.

## Announcements
<!-- endpoint: workspace.announcements who: Organization Admins post; everyone reads -->
<!-- keywords: announcement news notice broadcast everyone pinned memo -->
Announcements are organization-wide news. Organization Admins post them from the Announcements page, and everyone is notified. Pinned announcements and those from the last two weeks also appear on everyone's dashboard. Admins can pin, unpin or delete them.

## Meetings, minutes and calendar invites
<!-- endpoint: workspace.meetings who: everyone -->
<!-- keywords: meeting schedule invite minutes agenda calendar ics outlook google calendar video call cancel -->
Open Meetings and choose Schedule. Set the date, start time (in your organization's time zone), length, place or video-call link and agenda, then pick who to invite; choosing a project fills in its team. Invited people are notified and see the meeting on their calendar and dashboard. Add to calendar downloads an .ics file for Outlook, Google or Apple Calendar. Any attendee can record the minutes. The organiser, the project manager or an admin can edit or cancel the meeting, and attendees are told.

## M&E: results framework (logframe)
<!-- endpoint: workspace.me_overview who: M&E Officers and project managers set it up; anyone with M&E access can view -->
<!-- keywords: results framework logframe logical framework goal outcome output impact theory of change m&e monitoring evaluation -->
Open a project and choose the M&E tab. Add the goal (the long-term change you contribute to), then the outcomes (what changes for people) and the outputs (what the project delivers). Each output sits under an outcome and each outcome under the goal. Codes are numbered automatically: G, 1, 2 for outcomes and 1.1, 1.2 for outputs. Record assumptions, the things outside your control that must hold true. M&E Officers can manage every project's framework; a project manager can manage their own.

## M&E: indicators, baselines and targets
<!-- endpoint: workspace.me_overview who: M&E Officers and project managers -->
<!-- keywords: indicator indicators baseline target endline kpi measure percentage means of verification disaggregation frequency -->
On the M&E tab choose Add, then Indicator. Give it a name, link it to the result it measures, and choose whether it's a number or a percentage and whether higher or lower is better. Enter the baseline value and date, the target value and date, how often data is collected, who is responsible and the means of verification. When the endline study is done, edit the indicator and add the endline value. Retire an indicator you no longer track; its history is kept.

## M&E: reporting results and verification
<!-- endpoint: workspace.me_overview who: field officers on the project team report; M&E Officers verify -->
<!-- keywords: report result actual value data collection verify verification reject pending field data -->
Open an indicator and use Report a result: choose the end of the reporting period, enter the actual value and the source. Results from field officers wait for an M&E Officer to verify them; only verified results count. An M&E Officer can verify or reject (with a note saying what to fix), but never their own result. Results recorded by an M&E Officer count straight away. The person responsible for an indicator gets a reminder when its data is overdue.

## M&E: how achievement and status are calculated
<!-- endpoint: workspace.me_overview who: everyone with M&E access -->
<!-- keywords: achievement percent on track at risk off track status calculation how is progress calculated -->
Achievement is how far the latest verified result has moved from the baseline towards the target. For example, baseline 0, target 12 and actual 6 is 50%, and for "lower is better" it works the same way in reverse. Status compares achievement with the share of time between the baseline and target dates that had passed when the result was measured: achieved at 100% or more, on track within 90% of that share, at risk within 60%, otherwise off track. "No data yet" means there's no verified result.

## Field monitoring visits
<!-- endpoint: workspace.visits who: field officers on the project team, M&E Officers and project managers -->
<!-- keywords: field visit monitoring visit site visit gps location coordinates photos findings recommendations review -->
Open Field visits and choose Record a visit. Enter the project, date, place, purpose, people met, findings, recommendations and how the work is going. On a phone, Use my location fills in the GPS coordinates; you can also type them. The visit saves as a draft that only you and the M&E team can see. Attach photos (JPG or PNG) or scanned forms (PDF); they're kept in the project's documents. Then choose Submit for review. An M&E Officer or the project manager accepts it or returns it with a note. Every visit can be downloaded as a PDF.

## Evaluations
<!-- endpoint: workspace.me_overview who: M&E Officers and project managers -->
<!-- keywords: evaluation baseline study midterm review endline final evaluation impact evaluation dac criteria rating management response -->
On a project's M&E tab choose Add under Evaluations. Record the type (baseline, mid-term, endline, impact or special review), the evaluator, dates, scope, key findings, recommendations and the management response. Rate the six OECD-DAC criteria (relevance, coherence, effectiveness, efficiency, impact and sustainability) from 1 to 4; the overall rating is their average. Link the report from the project's documents. Findings are required before an evaluation can be marked completed, and the project team is told when it is.

## M&E reports: PDF and Excel
<!-- endpoint: workspace.me_overview who: everyone with M&E access -->
<!-- keywords: report pdf excel export download donor report indicator tracking table itt m&e report -->
On a project's M&E tab, PDF report creates a donor-ready report. It covers the project overview, a budget snapshot (if you can see budgets), indicator performance, the full results framework with baseline, target, actual and status, recent field visits and evaluations. Excel downloads the indicator tracking table, every reported value and the visits. The M&E results page has an Excel export covering all open projects. Each monitoring visit also has its own PDF.

## Risk register: logging and scoring risks
<!-- endpoint: workspace.risks who: project managers and risk managers add risks; field officers and others with risk access can view -->
<!-- keywords: risk register risk log add risk likelihood impact score heat map matrix mitigation contingency risk owner -->
Open a project and choose the Risks tab, then Add risk. Describe what could happen and why, pick a category, and rate the likelihood and impact from 1 to 5. The score is likelihood × impact: 1–4 low, 5–9 medium, 10–15 high, 16–25 critical. Choose a response (reduce, avoid, transfer or accept), a risk owner and the next review date. High and critical risks need a mitigation plan unless you accept the risk. The heat map shows where open risks sit; tap a square to list them. Risks lists every project's register, and Excel downloads the register and issue log.

## Reviewing risks
<!-- endpoint: workspace.risks who: the risk owner and the project's managers -->
<!-- keywords: review risk reassess update score trend risk history review date reminder overdue -->
Open a risk and use Review this risk to record today's likelihood and impact, a note on what changed and the next review date. Every review is kept in the history, so you can see whether a risk is growing or shrinking. Owners get a reminder when a review date passes, and the project manager is told when a risk becomes critical. Close a risk (with a reason) when it no longer applies.

## Issues: problems happening now
<!-- endpoint: workspace.issues who: anyone on the project team with risk access can log; the assignee and managers update -->
<!-- keywords: issue log problem incident escalate assign resolve close priority overdue -->
An issue is a problem that is already happening. Log one from Issues or the project's Risks tab, with a priority, the person to deal with it and a resolve-by date. If a risk on the register happens, open it and use It has happened to turn it into a linked issue. The assignee moves it to in progress and then resolved (describing the resolution); only the project's managers can close it. Comments keep everyone up to date, and the project manager is told about high and critical issues.

## Purchase requests and approval
<!-- endpoint: workspace.procurement who: project managers and procurement officers raise requests; approvers with financial-approval rights approve -->
<!-- keywords: purchase request procurement requisition buy order approve approval budget line quotation method tender rfq -->
Open Procurement and choose New request. Pick the project and budget line, list the items with quantities and estimated unit costs, say why they're needed, and send the request for approval. The estimate sets the method: under 5,000 one quotation, under 50,000 at least three quotations, and above that an open tender. An approver (never the person who asked) checks the budget line: it shows what's left after spending and other open purchases. Going over budget needs the over-budget box and a reason.

## Quotations, purchase orders and receiving goods
<!-- endpoint: workspace.procurement who: procurement officers record quotations and award; the requester or procurement confirms receipt -->
<!-- keywords: quotation quote bid award purchase order po vendor selection cheapest receive goods delivery receipt expense -->
After approval, procurement officers record a quotation from each vendor (amount, currency, delivery time, validity and the quotation document). Award the order to one quotation to issue a numbered purchase order (PO PDF). Awards only go to approved vendors. Fewer quotations than the method needs, or choosing anything but the cheapest valid quote, needs a written justification. When the goods arrive, the requester or procurement confirms receipt and rates the vendor. The purchase then becomes an approved expense on the budget line, ready for finance to pay.

## Vendors
<!-- endpoint: workspace.vendors who: procurement officers manage; others with procurement access can view -->
<!-- keywords: vendor supplier contractor register approve blacklist suspend tin rating performance -->
Vendors lists your suppliers with their orders, total value and average rating from receipts. Procurement officers register vendors (name, category, contacts, tax ID and registration number). A different procurement officer approves them, and a tax ID is required first. Suspend or blacklist a vendor with a reason; they then can't be awarded new orders.

## Subscription, plans and paying
<!-- endpoint: workspace.subscription who: everyone can see the plan; people who manage the subscription change plan and pay -->
<!-- keywords: subscription plan upgrade downgrade pay payment paystack card mobile money momo price billing yearly monthly -->
Open Subscription to see your plan, what you've used against its limits and your invoices. Choose a plan and monthly or yearly billing (yearly gives two months free) and an invoice is created. On the invoice choose Pay with card or mobile money: you finish on Paystack's secure page and come straight back, and the plan switches on as soon as Paystack confirms. Switching plan part-way through a paid period credits the unused time. You can't move to a plan smaller than what you already use. Paystack sends its receipt to your organization's billing email, set in Organization profile.

## Invoices, renewals and past-due accounts
<!-- endpoint: workspace.subscription who: people who manage the subscription -->
<!-- keywords: invoice receipt pdf renewal renew due overdue past due lapsed bank transfer -->
Every invoice can be downloaded as a PDF. A renewal invoice is created a week before your paid period ends, and your subscription admins are emailed. If a renewal isn't paid within seven days of the period ending, the subscription becomes past due: new work is paused but nothing is deleted, and paying the invoice switches everything back on. You can also pay by bank transfer using the details on the invoice; Kayron records the payment for you.

## Email notifications
<!-- endpoint: workspace.account who: everyone -->
<!-- keywords: email notifications emails turn off unsubscribe alerts inbox -->
Besides the bell, ProjectSphere emails you when something needs you: assignments, approvals, messages, meetings, reminders and announcements. Emails hold only a short title and a link; the details stay behind sign-in. Turn emails on or off in My account under Email notifications.

## API keys and integrations
<!-- endpoint: workspace.integrations who: Organization Admins (Professional plan and above) -->
<!-- keywords: api key integration power bi dashboard kobotoolbox odk export connect developer token rest -->
Open Integrations & API to let other systems use your data, for example a Power BI dashboard or a data-collection tool sending indicator results. Create a key, give it only the access it needs (projects, finance, indicators, results submission, risks) and an expiry. Copy it straight away: it's shown only once and only a scrambled fingerprint is kept. Indicator results sent through the API always wait for an M&E Officer to verify them. Revoke a key at any time and it stops working immediately. The page lists the endpoints with an example.
