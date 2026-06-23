"""
Scheduled reminders.

Water reminders
---------------
Uses python-telegram-bot's built-in JobQueue (backed by APScheduler) to send
water reminders five times a day at fixed local times: 9 AM, 12 PM, 3 PM,
6 PM, and 9 PM, in the timezone configured via APP_TIMEZONE.

Reminders are (re)scheduled once a chat is authenticated. Because sessions
are in-memory only, reminders need to be re-armed on every bot restart - this
happens automatically the next time the user authenticates with /start.
"""

from __future__ import annotations

import logging
from datetime import time

from telegram.ext import Application, ContextTypes

from bot.config import Config
from bot.sheets_client import SheetsClient

logger = logging.getLogger(__name__)

WATER_REMINDER_TEXT = "💧 <b>Water Reminder</b>\n\nHave you had enough water today?"

_JOB_NAME_PREFIX = "water_reminder_"
_SCHEDULED_FLAG = "reminders_scheduled_for_chat"


# --------------------------------------------------------------------------- #
# Water reminders
# --------------------------------------------------------------------------- #

async def _send_water_reminder(context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = context.job.chat_id
    try:
        await context.bot.send_message(chat_id=chat_id, text=WATER_REMINDER_TEXT, parse_mode="HTML")
        logger.info("Sent water reminder to chat_id=%s", chat_id)
    except Exception as exc:  # noqa: BLE001 - reminders must never crash the scheduler
        logger.error("Failed to send water reminder to chat_id=%s: %s", chat_id, exc, exc_info=True)


# --------------------------------------------------------------------------- #
# Public scheduling helpers
# --------------------------------------------------------------------------- #

def schedule_reminders_for_chat(application: Application, chat_id: int, config: Config) -> None:
    """Schedule (or re-schedule) the daily water reminders for a given chat."""
    if application.job_queue is None:
        logger.warning(
            "JobQueue is not available (install python-telegram-bot[job-queue]); "
            "scheduled reminders are disabled."
        )
        return

    already_scheduled = application.bot_data.get(_SCHEDULED_FLAG)
    if already_scheduled == chat_id:
        logger.debug("Reminders already scheduled for chat_id=%s; skipping.", chat_id)
        return

    # Remove any previously scheduled reminder jobs (e.g. for a different chat_id).
    for job in application.job_queue.jobs():
        if job.name and job.name.startswith(_JOB_NAME_PREFIX):
            job.schedule_removal()

    tz = config.tzinfo
    for hour in config.reminder_hours:
        job_name = f"{_JOB_NAME_PREFIX}{hour}"
        application.job_queue.run_daily(
            _send_water_reminder,
            time=time(hour=hour, minute=0, tzinfo=tz),
            chat_id=chat_id,
            name=job_name,
        )
        logger.info("Scheduled water reminder at %02d:00 (%s) for chat_id=%s", hour, config.app_timezone, chat_id)

    application.bot_data[_SCHEDULED_FLAG] = chat_id
