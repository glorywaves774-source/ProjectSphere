from flask_wtf import FlaskForm
from wtforms import BooleanField, DecimalField, EmailField, PasswordField, SelectField, StringField, TextAreaField, URLField
from wtforms.validators import URL, DataRequired, Email, EqualTo, Length, NumberRange, Optional

from app.blueprints.auth.forms import strong_password
from app.models import ORG_TYPES, TICKET_CATEGORIES, TICKET_PRIORITIES

CURRENCIES = [("GHS", "GHS — Ghana cedi"), ("USD", "USD — US dollar"), ("EUR", "EUR — Euro"),
              ("GBP", "GBP — British pound"), ("NGN", "NGN — Nigerian naira"), ("KES", "KES — Kenyan shilling"),
              ("XOF", "XOF — West African CFA franc")]


class OrganizationForm(FlaskForm):
    name = StringField("Organization name", validators=[DataRequired(), Length(min=2, max=160)])
    org_type = SelectField("Organization type", choices=ORG_TYPES)
    country = StringField("Country", validators=[DataRequired(), Length(max=80)])
    email = EmailField("Contact email", validators=[Optional(), Email(), Length(max=255)])
    phone = StringField("Phone", validators=[Optional(), Length(max=40)])
    website = URLField("Website", validators=[Optional(), URL(), Length(max=255)])
    address = TextAreaField("Address", validators=[Optional(), Length(max=500)])
    currency = SelectField("Default currency", choices=CURRENCIES)
    timezone = StringField("Time zone", validators=[DataRequired(), Length(max=64)])


class ProfileForm(FlaskForm):
    full_name = StringField("Full name", validators=[DataRequired(), Length(min=2, max=120)])
    job_title = StringField("Job title", validators=[Optional(), Length(max=120)])
    phone = StringField("Phone", validators=[Optional(), Length(max=40)])


class PasswordChangeForm(FlaskForm):
    current_password = PasswordField("Current password", validators=[DataRequired()])
    new_password = PasswordField("New password", validators=[DataRequired(), strong_password])
    confirm = PasswordField("Confirm new password", validators=[DataRequired(), EqualTo("new_password", "Passwords don't match.")])


class TicketForm(FlaskForm):
    subject = StringField("Subject", validators=[DataRequired(), Length(min=4, max=160)])
    category = SelectField("Category", choices=TICKET_CATEGORIES)
    priority = SelectField("Priority", choices=TICKET_PRIORITIES, default="normal")
    body = TextAreaField("How can we help?", validators=[DataRequired(), Length(min=10, max=5000)])


class TicketReplyForm(FlaskForm):
    body = TextAreaField("Reply", validators=[DataRequired(), Length(min=2, max=5000)])


# ---------------------------------------------------------------- Stage 3: people
from wtforms import DateField, SelectMultipleField  # noqa: E402
from wtforms.widgets import CheckboxInput, ListWidget  # noqa: E402

from app.models import EMPLOYMENT_TYPES  # noqa: E402


class MultiCheckboxField(SelectMultipleField):
    widget = ListWidget(prefix_label=False)
    option_widget = CheckboxInput()


class _PersonFields(FlaskForm):
    full_name = StringField("Full name", validators=[DataRequired(), Length(min=2, max=120)])
    job_title = StringField("Job title", validators=[Optional(), Length(max=120)])
    department_id = SelectField("Department", coerce=int, default=0)
    # Role choices are checked by the people service, which explains *why* a role can't be given
    roles = MultiCheckboxField("Roles", coerce=int, validate_choice=False)


class StaffForm(_PersonFields):
    email = EmailField("Work email", validators=[DataRequired(), Email(), Length(max=255)])
    phone = StringField("Phone", validators=[Optional(), Length(max=40)])
    staff_number = StringField("Staff ID", validators=[Optional(), Length(max=40)])
    employment_type = SelectField("Employment type", choices=EMPLOYMENT_TYPES, default="")
    start_date = DateField("Start date", validators=[Optional()])
    weekly_hours = DecimalField("Weekly hours", places=1, default=40, validators=[Optional(), NumberRange(min=1, max=80)])


class MemberForm(_PersonFields):
    phone = StringField("Phone", validators=[Optional(), Length(max=40)])
    staff_number = StringField("Staff ID", validators=[Optional(), Length(max=40)])
    employment_type = SelectField("Employment type", choices=EMPLOYMENT_TYPES, default="")
    start_date = DateField("Start date", validators=[Optional()])
    weekly_hours = DecimalField("Weekly hours", places=1, default=40, validators=[Optional(), NumberRange(min=1, max=80)])


class InviteForm(FlaskForm):
    email = EmailField("Email", validators=[DataRequired(), Email(), Length(max=255)])
    full_name = StringField("Name (optional)", validators=[Optional(), Length(max=120)])
    job_title = StringField("Job title (optional)", validators=[Optional(), Length(max=120)])
    department_id = SelectField("Department", coerce=int, default=0)
    roles = MultiCheckboxField("Roles", coerce=int, validate_choice=False)


class AcceptInviteForm(FlaskForm):
    full_name = StringField("Your full name", validators=[DataRequired(), Length(min=2, max=120)])
    password = PasswordField("Choose a password", validators=[DataRequired(), strong_password, Length(max=200)])
    confirm = PasswordField("Confirm password", validators=[DataRequired(), EqualTo("password", "Passwords don't match.")])


class DepartmentForm(FlaskForm):
    name = StringField("Department name", validators=[DataRequired(), Length(min=2, max=120)])
    code = StringField("Short code", validators=[Optional(), Length(max=20)], description="e.g. FIN, PROG, M&E")
    head_id = SelectField("Head of department", coerce=int, default=0)
    description = TextAreaField("Description", validators=[Optional(), Length(max=1000)])


class RoleForm(FlaskForm):
    name = StringField("Role name", validators=[DataRequired(), Length(min=2, max=80)])
    description = StringField("What this role is for", validators=[Optional(), Length(max=255)])


# ---------------------------------------------------------------- Stage 4: projects
from wtforms import IntegerField  # noqa: E402
from wtforms.validators import NumberRange  # noqa: E402

from app.models import PROJECT_HEALTH, PROJECT_PRIORITIES, PROJECT_STATUSES  # noqa: E402
from wtforms import DecimalField  # noqa: E402


class ProjectForm(FlaskForm):
    name = StringField("Project name", validators=[DataRequired(), Length(min=3, max=200)])
    summary = StringField("One-line summary", validators=[Optional(), Length(max=300)])
    description = TextAreaField("Description", validators=[Optional(), Length(max=10000)])
    objectives = TextAreaField("Objectives", validators=[Optional(), Length(max=10000)],
                               description="One objective per line.")
    category_id = SelectField("Category", coerce=int, default=0)
    department_id = SelectField("Department", coerce=int, default=0)
    manager_id = SelectField("Project manager", coerce=int, default=0)
    status = SelectField("Status", choices=[c for c in PROJECT_STATUSES if c[0] in ("planning", "active", "on_hold")],
                         default="planning")
    priority = SelectField("Priority", choices=PROJECT_PRIORITIES, default="medium")
    start_date = DateField("Start date", validators=[Optional()])
    end_date = DateField("Planned end date", validators=[Optional()])
    budget = DecimalField("Planned budget", places=2, validators=[Optional(), NumberRange(min=0)])
    currency = SelectField("Currency", choices=CURRENCIES, default="GHS")
    location = StringField("Location", validators=[Optional(), Length(max=200)], description="e.g. Tamale, Northern Region")
    funder = StringField("Funder / client", validators=[Optional(), Length(max=200)])


class ProjectUpdateForm(FlaskForm):
    body = TextAreaField("What's happening?", validators=[Optional(), Length(max=5000)])
    status = SelectField("Status", choices=[c for c in PROJECT_STATUSES if c[0] in ("planning", "active", "on_hold")])
    health = SelectField("Health", choices=PROJECT_HEALTH)
    progress = IntegerField("Progress (%)", validators=[Optional(), NumberRange(min=0, max=100)])


class ProjectCloseForm(FlaskForm):
    outcome = SelectField("Outcome", choices=[("completed", "Completed"), ("cancelled", "Cancelled")])
    actual_end_date = DateField("Actual end date", validators=[Optional()])
    summary = TextAreaField("Closure summary", validators=[DataRequired(), Length(min=10, max=5000)],
                            description="What was delivered, and whether objectives were met.")
    lessons = TextAreaField("Lessons learned", validators=[Optional(), Length(max=5000)])


class ProjectMemberForm(FlaskForm):
    user_id = SelectField("Person", coerce=int)
    project_role = StringField("Role on this project", default="Team member", validators=[Optional(), Length(max=80)])


class CategoryForm(FlaskForm):
    name = StringField("Category name", validators=[DataRequired(), Length(min=2, max=80)])
    color = StringField("Colour", default="#6d5bf6", validators=[DataRequired(), Length(min=7, max=7)])

    def validate_color(self, field):
        import re

        from wtforms.validators import ValidationError

        if not re.fullmatch(r"#[0-9a-fA-F]{6}", field.data or ""):
            raise ValidationError("Use a colour like #6d5bf6.")


# ---------------------------------------------------------------- Stage 5: tasks
from app.models import TASK_PRIORITIES, TASK_STATUSES  # noqa: E402


class TaskForm(FlaskForm):
    title = StringField("Task", validators=[DataRequired(), Length(min=2, max=200)])
    description = TextAreaField("Details", validators=[Optional(), Length(max=10000)])
    parent_id = SelectField("Part of", coerce=int, default=0)
    assignee_id = SelectField("Assigned to", coerce=int, default=0)
    priority = SelectField("Priority", choices=TASK_PRIORITIES, default="medium")
    start_date = DateField("Start", validators=[Optional()])
    due_date = DateField("Due", validators=[Optional()])
    estimate_hours = DecimalField("Estimated hours", places=1, validators=[Optional(), NumberRange(min=0, max=100000)])
    is_milestone = BooleanField("This is a milestone (a key date, not work)")
    requires_approval = BooleanField("Needs approval when finished")


class TaskStatusForm(FlaskForm):
    status = SelectField("Status", choices=TASK_STATUSES)
    progress = IntegerField("Progress (%)", validators=[Optional(), NumberRange(min=0, max=100)])
    note = TextAreaField("Note (optional)", validators=[Optional(), Length(max=2000)])


class TaskCommentForm(FlaskForm):
    body = TextAreaField("Comment", validators=[DataRequired(), Length(min=1, max=5000)])


class DependencyForm(FlaskForm):
    predecessor_id = SelectField("Waits for", coerce=int)


class ReviewForm(FlaskForm):
    note = TextAreaField("Note", validators=[Optional(), Length(max=2000)])


# ---------------------------------------------------------------- Stage 6: finance
from app.models import BUDGET_CATEGORIES, PAYMENT_METHODS  # noqa: E402


class BudgetLineForm(FlaskForm):
    category = SelectField("Category", choices=BUDGET_CATEGORIES)
    description = StringField("Description", validators=[DataRequired(), Length(min=2, max=255)])
    quantity = DecimalField("Quantity", places=2, default=1, validators=[DataRequired(), NumberRange(min=0.01)])
    unit = StringField("Unit", validators=[Optional(), Length(max=30)], description="e.g. days, trips, pieces")
    unit_cost = DecimalField("Unit cost", places=2, validators=[DataRequired(message="Enter a unit cost."), NumberRange(min=0)])
    task_id = SelectField("Linked task (optional)", coerce=int, default=0)
    notes = TextAreaField("Notes", validators=[Optional(), Length(max=2000)])


class ExpenseForm(FlaskForm):
    budget_line_id = SelectField("Budget line", coerce=int)
    spent_on = DateField("Date", validators=[DataRequired()])
    description = StringField("What was it for?", validators=[DataRequired(), Length(min=2, max=255)])
    payee = StringField("Paid to", validators=[Optional(), Length(max=160)])
    invoice_ref = StringField("Invoice / receipt no.", validators=[Optional(), Length(max=60)])
    amount = DecimalField("Amount", places=2, validators=[DataRequired(), NumberRange(min=0.01)])
    currency = SelectField("Currency", choices=CURRENCIES)
    exchange_rate = DecimalField("Exchange rate", places=6, validators=[Optional(), NumberRange(min=0.000001)],
                                 description="Units of the project currency per 1 unit of this currency. Leave empty to use the rate set by finance.")


class DecisionForm(FlaskForm):
    note = TextAreaField("Note", validators=[Optional(), Length(max=2000)])
    allow_over = BooleanField("I approve this even though it goes over the budget line")


class PaymentForm(FlaskForm):
    paid_at = DateField("Paid on", validators=[DataRequired()])
    method = SelectField("Method", choices=PAYMENT_METHODS)
    ref = StringField("Payment reference", validators=[Optional(), Length(max=60)])


class RateForm(FlaskForm):
    currency = SelectField("Currency", choices=CURRENCIES)
    rate = DecimalField("Value in base currency", places=6, validators=[DataRequired(), NumberRange(min=0.000001)])
    effective_on = DateField("Effective from", validators=[DataRequired()])


# ---------------------------------------------------------------- Stage 8: collaboration
from flask_wtf.file import FileField, FileRequired  # noqa: E402
from wtforms import TimeField  # noqa: E402

from app.models import DOCUMENT_CATEGORIES  # noqa: E402


class DocumentForm(FlaskForm):
    file = FileField("File", validators=[FileRequired("Choose a file to upload.")])
    title = StringField("Title", validators=[Optional(), Length(max=200)], description="Leave empty to use the file name.")
    category = SelectField("Category", choices=DOCUMENT_CATEGORIES, default="other")
    project_id = SelectField("Project", coerce=int, default=0)
    description = TextAreaField("Description", validators=[Optional(), Length(max=2000)])


class VersionForm(FlaskForm):
    file = FileField("New version", validators=[FileRequired("Choose a file to upload.")])
    note = StringField("What changed?", validators=[Optional(), Length(max=255)])


class MessageForm(FlaskForm):
    body = TextAreaField("Message", validators=[DataRequired(), Length(max=5000)])


class NewConversationForm(FlaskForm):
    people = MultiCheckboxField("People", coerce=int)
    title = StringField("Group name", validators=[Optional(), Length(max=120)],
                        description="Needed when you choose two or more people.")


class AnnouncementForm(FlaskForm):
    title = StringField("Title", validators=[DataRequired(), Length(min=3, max=160)])
    body = TextAreaField("Message", validators=[DataRequired(), Length(min=3, max=5000)])
    pinned = BooleanField("Pin to the top of everyone's dashboard")


class MeetingForm(FlaskForm):
    title = StringField("Title", validators=[DataRequired(), Length(min=2, max=160)])
    day = DateField("Date", validators=[DataRequired()])
    time = TimeField("Start time", validators=[DataRequired()])
    duration_min = IntegerField("Length (minutes)", default=60, validators=[DataRequired(), NumberRange(min=5, max=720)])
    location = StringField("Where", validators=[Optional(), Length(max=255)], description="A room, address or video-call link")
    project_id = SelectField("Project", coerce=int, default=0)
    attendees = MultiCheckboxField("Invite", coerce=int)
    agenda = TextAreaField("Agenda", validators=[Optional(), Length(max=5000)])


class MinutesForm(FlaskForm):
    minutes = TextAreaField("Minutes", validators=[Optional(), Length(max=20000)])


# ---------------------------------------------------------------- Stage 9: monitoring and evaluation
from wtforms.validators import InputRequired  # noqa: E402

from app.models import (DAC_CRITERIA, EVALUATION_KINDS, EVALUATION_STATUSES, FREQUENCIES,  # noqa: E402
                        INDICATOR_DIRECTIONS, INDICATOR_MEASURES, RATING_SCALE, RESULT_LEVELS, VISIT_RATINGS)

_RATING_CHOICES = [(0, "Not rated")] + [(v, f"{v} · {label}") for v, label in RATING_SCALE]


class ResultForm(FlaskForm):
    level = SelectField("Level", choices=RESULT_LEVELS, default="outcome")
    parent_id = SelectField("Contributes to", coerce=int, default=0)
    statement = TextAreaField("Result statement", validators=[DataRequired(), Length(min=5, max=5000)],
                              description="Describe the change, e.g. “Households in the district have year-round access to safe water”.")
    assumptions = TextAreaField("Assumptions and risks", validators=[Optional(), Length(max=5000)],
                                description="What must hold true outside the project for this result to happen?")


class IndicatorForm(FlaskForm):
    name = StringField("Indicator", validators=[DataRequired(), Length(min=3, max=255)],
                       description="What is measured, e.g. “Households using a protected water source”.")
    result_id = SelectField("Result it measures", coerce=int, default=0)
    definition = TextAreaField("Definition and how it's calculated", validators=[Optional(), Length(max=5000)])
    measure = SelectField("Measured as", choices=INDICATOR_MEASURES, default="number")
    unit = StringField("Unit", validators=[Optional(), Length(max=40)], description="e.g. households, people, km")
    direction = SelectField("Direction", choices=INDICATOR_DIRECTIONS, default="increase")
    baseline_value = DecimalField("Baseline value", places=2, default=0,
                                  validators=[InputRequired("Enter the baseline (0 if starting from nothing).")])
    baseline_date = DateField("Baseline date", validators=[DataRequired()])
    target_value = DecimalField("Target value", places=2, validators=[InputRequired("Enter the target.")])
    target_date = DateField("Target date", validators=[DataRequired()])
    frequency = SelectField("Data collected", choices=FREQUENCIES, default="quarterly")
    responsible_id = SelectField("Responsible for reporting", coerce=int, default=0)
    data_source = TextAreaField("Means of verification", validators=[Optional(), Length(max=2000)],
                                description="Where the data comes from, e.g. household survey, attendance registers.")
    disaggregation = StringField("Disaggregate by", validators=[Optional(), Length(max=255)],
                                 description="e.g. sex, age group, district")
    endline_value = DecimalField("Endline value", places=2, validators=[Optional()],
                                 description="Fill in once the endline study is done.")
    endline_date = DateField("Endline date", validators=[Optional()])


class IndicatorValueForm(FlaskForm):
    period_end = DateField("Period ending", validators=[DataRequired()])
    value = DecimalField("Actual value", places=2, validators=[InputRequired("Enter the value.")])
    source = StringField("Source", validators=[Optional(), Length(max=255)], description="e.g. Q3 field survey")
    notes = TextAreaField("Notes", validators=[Optional(), Length(max=5000)])


class ValueReviewForm(FlaskForm):
    note = StringField("Note", validators=[Optional(), Length(max=500)])


class VisitForm(FlaskForm):
    project_id = SelectField("Project", coerce=int)
    visit_date = DateField("Visit date", validators=[DataRequired()])
    location = StringField("Location", validators=[DataRequired(), Length(min=2, max=255)],
                           description="Community, site or facility visited")
    latitude = StringField("Latitude", validators=[Optional(), Length(max=20)])
    longitude = StringField("Longitude", validators=[Optional(), Length(max=20)])
    purpose = TextAreaField("Purpose of the visit", validators=[Optional(), Length(max=10000)])
    people_met = TextAreaField("People met", validators=[Optional(), Length(max=10000)],
                               description="Who you spoke with (roles or groups, e.g. “water committee, 14 women”)")
    findings = TextAreaField("Findings", validators=[Optional(), Length(max=10000)],
                             description="What you observed: progress, quality, problems")
    recommendations = TextAreaField("Recommendations and follow-up", validators=[Optional(), Length(max=10000)])
    rating = SelectField("Overall, the work is", choices=VISIT_RATINGS, default="good")


class VisitReviewForm(FlaskForm):
    note = TextAreaField("Reviewer's note", validators=[Optional(), Length(max=5000)])


class EvaluationForm(FlaskForm):
    title = StringField("Title", validators=[DataRequired(), Length(min=3, max=200)])
    kind = SelectField("Type", choices=EVALUATION_KINDS, default="midterm")
    status = SelectField("Status", choices=EVALUATION_STATUSES, default="planned")
    evaluator = StringField("Evaluator", validators=[Optional(), Length(max=200)],
                            description="Person, team or firm carrying it out")
    is_external = BooleanField("External (independent) evaluator")
    start_date = DateField("Start", validators=[Optional()])
    end_date = DateField("End", validators=[Optional()])
    scope = TextAreaField("Scope and questions", validators=[Optional(), Length(max=20000)])
    findings = TextAreaField("Key findings", validators=[Optional(), Length(max=20000)])
    recommendations = TextAreaField("Recommendations", validators=[Optional(), Length(max=20000)])
    management_response = TextAreaField("Management response", validators=[Optional(), Length(max=20000)],
                                        description="What the organization will do about each recommendation")
    report_document_id = SelectField("Evaluation report (from project documents)", coerce=int, default=0)


for _key, _label in DAC_CRITERIA:
    setattr(EvaluationForm, _key, SelectField(_label, coerce=int, choices=_RATING_CHOICES, default=0))


# ---------------------------------------------------------------- Stage 10: risk and procurement
from app.models import (IMPACT, ISSUE_PRIORITIES, ISSUE_STATUSES, LIKELIHOOD, RISK_CATEGORIES,  # noqa: E402
                        RISK_RESPONSES, VENDOR_CATEGORIES)

_L = [(v, f"{v} · {label}") for v, label in LIKELIHOOD]
_I = [(v, f"{v} · {label}") for v, label in IMPACT]


class RiskForm(FlaskForm):
    title = StringField("Risk", validators=[DataRequired(), Length(min=5, max=200)],
                        description="What could happen, e.g. “Rains delay borehole drilling”")
    description = TextAreaField("Cause and consequence", validators=[Optional(), Length(max=5000)],
                                description="Because of …, … may happen, which would lead to …")
    category = SelectField("Category", choices=RISK_CATEGORIES, default="operational")
    likelihood = SelectField("Likelihood", coerce=int, choices=_L, default=3)
    impact = SelectField("Impact", coerce=int, choices=_I, default=3)
    response = SelectField("Response", choices=RISK_RESPONSES, default="reduce")
    mitigation = TextAreaField("Mitigation plan", validators=[Optional(), Length(max=5000)],
                               description="What we do now to make it less likely or less severe")
    contingency = TextAreaField("Contingency plan", validators=[Optional(), Length(max=5000)],
                                description="What we'll do if it happens")
    owner_id = SelectField("Risk owner", coerce=int, default=0)
    review_date = DateField("Next review", validators=[Optional()])


class RiskReviewForm(FlaskForm):
    likelihood = SelectField("Likelihood now", coerce=int, choices=_L)
    impact = SelectField("Impact now", coerce=int, choices=_I)
    note = TextAreaField("What has changed?", validators=[Optional(), Length(max=5000)])
    next_review = DateField("Next review", validators=[Optional()])


class EscalateForm(FlaskForm):
    priority = SelectField("Issue priority", choices=ISSUE_PRIORITIES, default="high")
    assignee_id = SelectField("Assign to", coerce=int, default=0)
    due_date = DateField("Resolve by", validators=[Optional()])
    description = TextAreaField("What happened?", validators=[Optional(), Length(max=5000)])


class IssueForm(FlaskForm):
    title = StringField("Issue", validators=[DataRequired(), Length(min=5, max=200)])
    description = TextAreaField("Details", validators=[Optional(), Length(max=5000)])
    priority = SelectField("Priority", choices=ISSUE_PRIORITIES, default="medium")
    assignee_id = SelectField("Assign to", coerce=int, default=0)
    due_date = DateField("Resolve by", validators=[Optional()])


class IssueStatusForm(FlaskForm):
    status = SelectField("Status", choices=ISSUE_STATUSES)
    resolution = TextAreaField("Resolution", validators=[Optional(), Length(max=5000)],
                               description="Needed when resolving or closing")


class VendorForm(FlaskForm):
    name = StringField("Vendor name", validators=[DataRequired(), Length(min=2, max=200)])
    category = SelectField("Category", choices=VENDOR_CATEGORIES)
    contact_name = StringField("Contact person", validators=[Optional(), Length(max=120)])
    email = StringField("Email", validators=[Optional(), Email(), Length(max=255)])
    phone = StringField("Phone", validators=[Optional(), Length(max=40)])
    address = TextAreaField("Address", validators=[Optional(), Length(max=2000)])
    tax_id = StringField("Tax ID (TIN)", validators=[Optional(), Length(max=40)])
    registration_no = StringField("Business registration no.", validators=[Optional(), Length(max=60)])
    notes = TextAreaField("Notes", validators=[Optional(), Length(max=5000)])


class ProcurementForm(FlaskForm):
    project_id = SelectField("Project", coerce=int)
    title = StringField("What do you need?", validators=[DataRequired(), Length(min=3, max=200)],
                        description="e.g. “Hand pumps for 12 boreholes”")
    budget_line_id = SelectField("Budget line", coerce=int, default=0)
    needed_by = DateField("Needed by", validators=[Optional()])
    delivery_location = StringField("Deliver to", validators=[Optional(), Length(max=255)])
    justification = TextAreaField("Why is it needed?", validators=[Optional(), Length(max=5000)],
                                  description="Required before you submit")


class QuotationForm(FlaskForm):
    vendor_id = SelectField("Vendor", coerce=int)
    amount = DecimalField("Quoted total", places=2, validators=[DataRequired(), NumberRange(min=0.01)])
    currency = SelectField("Currency", choices=CURRENCIES)
    exchange_rate = DecimalField("Exchange rate", places=6, validators=[Optional(), NumberRange(min=0.000001)],
                                 description="Only for other currencies. Leave empty to use finance's rate.")
    delivery_days = IntegerField("Delivery (days)", validators=[Optional(), NumberRange(min=0, max=3650)])
    valid_until = DateField("Valid until", validators=[Optional()])
    reference = StringField("Quotation no.", validators=[Optional(), Length(max=60)])
    document_id = SelectField("Quotation document", coerce=int, default=0)
    notes = TextAreaField("Notes", validators=[Optional(), Length(max=2000)])


class AwardForm(FlaskForm):
    quotation_id = SelectField("Winning quotation", coerce=int)
    justification = TextAreaField("Justification", validators=[Optional(), Length(max=5000)],
                                  description="Needed if you don't choose the cheapest valid quotation, or have fewer quotations than required")


class ReceiveForm(FlaskForm):
    received_on = DateField("Received on", validators=[DataRequired()])
    rating = SelectField("Vendor performance", coerce=int, default=4,
                         choices=[(5, "5 · Excellent"), (4, "4 · Good"), (3, "3 · Acceptable"), (2, "2 · Poor"),
                                  (1, "1 · Unacceptable")])
    invoice_ref = StringField("Invoice no.", validators=[Optional(), Length(max=60)])
    note = TextAreaField("Condition, quantities, any problems", validators=[Optional(), Length(max=5000)])


# ---------------------------------------------------------------- Stage 11: billing and integrations
from app.models import API_SCOPES  # noqa: E402


class ApiKeyForm(FlaskForm):
    name = StringField("Name", validators=[DataRequired(), Length(min=2, max=80)],
                       description="What will use it, e.g. “Power BI dashboard” or “KoboToolbox sync”")
    scopes = MultiCheckboxField("Access", choices=API_SCOPES)
    expires = SelectField("Expires", coerce=int, default=365,
                          choices=[(90, "In 90 days"), (365, "In one year"), (0, "Never (not recommended)")])


class NotificationPrefsForm(FlaskForm):
    email_notify = BooleanField("Email me about assignments, approvals and other notifications")
