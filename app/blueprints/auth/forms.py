from flask import current_app
from flask_wtf import FlaskForm
from wtforms import BooleanField, EmailField, PasswordField, SelectField, StringField
from wtforms.validators import DataRequired, Email, EqualTo, Length, ValidationError

from app.models import ORG_TYPES


def strong_password(form, field):
    value = field.data or ""
    min_len = current_app.config["PASSWORD_MIN_LENGTH"]
    if len(value) < min_len:
        raise ValidationError(f"Use at least {min_len} characters.")
    if value.lower() == value or value.upper() == value or not any(c.isdigit() for c in value):
        raise ValidationError("Mix upper and lower case letters and at least one number.")


class LoginForm(FlaskForm):
    email = EmailField("Work email", validators=[DataRequired(), Email(), Length(max=255)])
    password = PasswordField("Password", validators=[DataRequired(), Length(max=200)])
    remember = BooleanField("Keep me signed in on this device")


class RegisterForm(FlaskForm):
    org_name = StringField("Organization name", validators=[DataRequired(), Length(min=2, max=160)])
    org_type = SelectField("Organization type", choices=ORG_TYPES, validators=[DataRequired()])
    country = StringField("Country", default="Ghana", validators=[DataRequired(), Length(max=80)])
    full_name = StringField("Your full name", validators=[DataRequired(), Length(min=2, max=120)])
    email = EmailField("Work email", validators=[DataRequired(), Email(), Length(max=255)])
    password = PasswordField("Password", validators=[DataRequired(), strong_password, Length(max=200)])
    confirm = PasswordField("Confirm password", validators=[DataRequired(), EqualTo("password", "Passwords don't match.")])
    plan = SelectField("Plan", validators=[DataRequired()])
    accept = BooleanField("I agree to the terms of service and privacy policy", validators=[DataRequired("Please accept the terms to continue.")])
