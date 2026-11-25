"""Celery worker / beat entry point.

    celery -A celery_worker.celery worker --loglevel=info
    celery -A celery_worker.celery beat --loglevel=info
"""
import os

from celery import Celery
from celery.schedules import crontab

from app import create_app

flask_app = create_app(os.environ.get("APP_ENV", "production"))

celery = Celery(
    "projectsphere",
    broker=os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
    backend=os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
)
celery.conf.update(timezone="UTC", task_acks_late=True, worker_prefetch_multiplier=1)


class FlaskTask(celery.Task):
    def __call__(self, *args, **kwargs):
        with flask_app.app_context():
            return self.run(*args, **kwargs)


celery.Task = FlaskTask


@celery.task(name="subscriptions.expire_trials")
def expire_trials_task():
    from app.services.subscriptions import expire_trials

    return expire_trials()


@celery.task(name="me.due_reminders")
def me_due_reminders_task():
    from app.services.me import send_due_reminders

    return send_due_reminders()


@celery.task(name="risks.review_reminders")
def risk_review_reminders_task():
    from app.services.risks import send_review_reminders

    return send_review_reminders()


@celery.task(name="mail.flush_outbox")
def flush_outbox_task():
    from app.services.email_outbox import flush

    return flush()


@celery.task(name="billing.renewals")
def billing_renewals_task():
    from app.services.billing import run_renewals

    return run_renewals()


celery.conf.beat_schedule = {
    "send-queued-email-every-minute": {"task": "mail.flush_outbox", "schedule": 60.0},
    "billing-renewals-daily": {"task": "billing.renewals", "schedule": crontab(hour=5, minute=17)},
    "risk-review-reminders-daily": {"task": "risks.review_reminders", "schedule": crontab(hour=6, minute=41)},
    "expire-trials-hourly": {"task": "subscriptions.expire_trials", "schedule": crontab(minute=7)},
    "me-data-reminders-daily": {"task": "me.due_reminders", "schedule": crontab(hour=6, minute=23)},
}
