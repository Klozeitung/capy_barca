"""
Alerts: personal reminders on database entries and the system alerts of
calendar dates. Importing the package registers the session hook that keeps
the system alerts in line with the date values (see ``system_alerts``).
"""
from app.alerts import system_alerts  # noqa: F401
