import re

from flask_wtf import FlaskForm
from wtforms import (BooleanField, DateField, DecimalField, EmailField, IntegerField, SelectField, StringField,
                     TextAreaField)
from wtforms.validators import DataRequired, Email, Length, NumberRange, Optional, ValidationError

from app.models import ORG_TYPES, TICKET_PRIORITIES, TICKET_STATUSES

SUB_STATUSES = [("trialing", "Trial"), ("active", "Active (paying)"), ("past_due", "Past due"), ("cancelled", "Cancelled")]
CURRENCIES = [("GHS", "GHS"), ("USD", "USD"), ("EUR", "EUR"), ("GBP", "GBP"), ("NGN", "NGN"), ("KES", "KES"), ("XOF", "XOF")]


def plan_code(form, field):
    if not re.fullmatch(r"[a-z][a-z0-9-]{1,38}", field.data or ""):
        raise ValidationError("Use lowercase letters, numbers and dashes, starting with a letter.")


class PlanForm(FlaskForm):
    code = StringField("Code", validators=[DataRequired(), plan_code],
                       description="Permanent identifier, e.g. 'starter'. Can't be changed later.")
    name = StringField("Name", validators=[DataRequired(), Length(max=80)])
    audience = StringField("Who it's for", validators=[Optional(), Length(max=80)])
    description = StringField("Short description", validators=[Optional(), Length(max=255)])
    currency = SelectField("Currency", choices=CURRENCIES)
    price_monthly = DecimalField("Monthly price", places=2, validators=[Optional(), NumberRange(min=0)],
                                 description="Leave empty for custom (Enterprise) pricing.")
    price_annual = DecimalField("Annual price", places=2, validators=[Optional(), NumberRange(min=0)])
    max_users = IntegerField("Team members", validators=[Optional(), NumberRange(min=1)], description="Empty = unlimited")
    max_projects = IntegerField("Projects", validators=[Optional(), NumberRange(min=1)], description="Empty = unlimited")
    max_storage_mb = IntegerField("Storage (MB)", validators=[Optional(), NumberRange(min=1)], description="Empty = custom")
    features = TextAreaField("Features (one per line)", validators=[Optional(), Length(max=2000)])
    sort_order = IntegerField("Display order", default=0, validators=[Optional()])
    is_public = BooleanField("Show on the pricing page and sign-up form", default=True)
    is_active = BooleanField("Available for new subscriptions", default=True)

    def validate_price_annual(self, field):
        if field.data is not None and self.price_monthly.data is None:
            raise ValidationError("Set a monthly price too, or leave both empty for custom pricing.")


class CreateOrganizationForm(FlaskForm):
    org_name = StringField("Organization name", validators=[DataRequired(), Length(min=2, max=160)])
    org_type = SelectField("Type", choices=ORG_TYPES)
    country = StringField("Country", default="Ghana", validators=[DataRequired(), Length(max=80)])
    plan = SelectField("Plan", validators=[DataRequired()])
    status = SelectField("Start as", choices=[("trialing", "Free trial"), ("active", "Active (paying)")])
    admin_name = StringField("Admin's full name", validators=[DataRequired(), Length(min=2, max=120)])
    admin_email = EmailField("Admin's email", validators=[DataRequired(), Email(), Length(max=255)])


class OrganizationEditForm(FlaskForm):
    name = StringField("Organization name", validators=[DataRequired(), Length(min=2, max=160)])
    org_type = SelectField("Type", choices=ORG_TYPES)
    country = StringField("Country", validators=[DataRequired(), Length(max=80)])
    email = EmailField("Contact email", validators=[Optional(), Email(), Length(max=255)])
    phone = StringField("Phone", validators=[Optional(), Length(max=40)])


class SubscriptionForm(FlaskForm):
    plan_id = SelectField("Plan", coerce=int)
    status = SelectField("Status", choices=SUB_STATUSES)
    billing_cycle = SelectField("Billing", choices=[("monthly", "Monthly"), ("annual", "Annual")])
    trial_ends_at = DateField("Trial ends", validators=[Optional()])
    current_period_end = DateField("Paid until", validators=[Optional()])


class SuperAdminForm(FlaskForm):
    full_name = StringField("Full name", validators=[DataRequired(), Length(min=2, max=120)])
    email = EmailField("Email", validators=[DataRequired(), Email(), Length(max=255)])


class StaffReplyForm(FlaskForm):
    body = TextAreaField("Reply", validators=[DataRequired(), Length(min=2, max=5000)])
    internal = BooleanField("Internal note (not visible to the organization)")
    status = SelectField("Set status", choices=TICKET_STATUSES)


class TicketTriageForm(FlaskForm):
    status = SelectField("Status", choices=TICKET_STATUSES)
    priority = SelectField("Priority", choices=TICKET_PRIORITIES)
    assigned_to_id = SelectField("Assigned to", coerce=int)


# ---------------------------------------------------------------- Stage 11: billing


class ManualInvoiceForm(FlaskForm):
    description = StringField("What it's for", validators=[DataRequired(), Length(min=3, max=255)])
    amount = DecimalField("Amount", places=2, validators=[DataRequired()])
    currency = SelectField("Currency", choices=[("GHS", "GHS"), ("USD", "USD"), ("EUR", "EUR"), ("GBP", "GBP")])
    due_date = DateField("Due date", validators=[DataRequired()])
    plan_id = SelectField("Activates plan (optional)", coerce=int, default=0)
    cycle = SelectField("Period", choices=[("monthly", "One month"), ("annual", "One year")])
    period_start = DateField("Period starts", validators=[Optional()])


class OfflinePaymentForm(FlaskForm):
    method = SelectField("Paid by", choices=[("bank", "Bank transfer"), ("momo", "Mobile money"), ("cheque", "Cheque"),
                                             ("cash", "Cash"), ("other", "Other")])
    reference = StringField("Reference", validators=[DataRequired(), Length(min=3, max=100)])
    paid_on = DateField("Paid on", validators=[DataRequired()])
