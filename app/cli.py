import os

import click
from flask import Flask


def register_cli(app: Flask) -> None:
    @app.cli.command("seed")
    def seed_cmd():
        """Load permissions and subscription plans (safe to run repeatedly)."""
        from app.services.seed import seed_reference_data

        click.echo(f"Seeded: {seed_reference_data()}")

    @app.cli.command("create-superadmin")
    @click.option("--email", required=True)
    @click.option("--name", default="Platform Administrator")
    def create_superadmin(email, name):
        """Create or reset a Platform Super Administrator.

        Password is read from SUPERADMIN_PASSWORD or prompted (never passed as an argument).
        """
        from app.services.onboarding import create_platform_admin

        password = os.environ.get("SUPERADMIN_PASSWORD") or click.prompt(
            "Password", hide_input=True, confirmation_prompt=True
        )
        if len(password) < app.config["PASSWORD_MIN_LENGTH"]:
            raise click.ClickException("Password too short")
        create_platform_admin(email, name, password)
        click.echo(f"Super Admin ready: {email.lower()}")

    @app.cli.command("expire-trials")
    def expire_trials_cmd():
        """Mark ended free trials as past due."""
        from app.services.subscriptions import expire_trials

        click.echo(f"Trials expired: {expire_trials()}")

    @app.cli.command("me-reminders")
    def me_reminders_cmd():
        """Remind people whose indicator data is overdue (also runs daily on the worker)."""
        from app.services.me import send_due_reminders

        click.echo(f"Reminders sent: {send_due_reminders()}")

    @app.cli.command("risk-reminders")
    def risk_reminders_cmd():
        """Remind risk owners whose review date has passed (also runs daily on the worker)."""
        from app.services.risks import send_review_reminders

        click.echo(f"Reminders sent: {send_review_reminders()}")

    @app.cli.command("send-mail")
    def send_mail_cmd():
        """Send queued emails now (the worker does this every minute)."""
        from app.services.email_outbox import flush

        click.echo(flush())

    @app.cli.command("run-renewals")
    def run_renewals_cmd():
        """Issue renewal invoices and mark lapsed subscriptions past due (the worker runs this daily)."""
        from app.services.billing import run_renewals

        click.echo(run_renewals())
