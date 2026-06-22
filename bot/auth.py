"""
Simple in-memory password authentication.

This bot is designed for a single user, but Telegram commands can technically
reach the bot from anyone who finds it, so every command is gated behind a
password check. Authenticated Telegram user IDs are kept in memory only
(`context.application.bot_data`) - restarting the bot clears all sessions,
which is the desired behaviour described in the spec.
"""

from __future__ import annotations

import functools
import logging
from typing import Awaitable, Callable

from telegram import Update
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

ACCESS_DENIED_MESSAGE = "Access denied. Enter correct password."

_AUTH_KEY = "authenticated_users"
_CHAT_ID_KEY = "active_chat_id"


def _authenticated_users(context: ContextTypes.DEFAULT_TYPE) -> set[int]:
    return context.application.bot_data.setdefault(_AUTH_KEY, set())


def is_authenticated(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    return user_id in _authenticated_users(context)


def authenticate(user_id: int, chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Mark a user as authenticated for the lifetime of this process."""
    _authenticated_users(context).add(user_id)
    context.application.bot_data[_CHAT_ID_KEY] = chat_id
    logger.info("User %s authenticated successfully (chat_id=%s).", user_id, chat_id)


def get_active_chat_id(context: ContextTypes.DEFAULT_TYPE) -> int | None:
    return context.application.bot_data.get(_CHAT_ID_KEY)


Handler = Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[None]]


def require_auth(handler: Handler) -> Handler:
    """Decorator that blocks command/message handlers until the user is authenticated."""

    @functools.wraps(handler)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if user is None or not is_authenticated(user.id, context):
            logger.warning("Unauthorized access attempt by user_id=%s", user.id if user else "unknown")
            if update.effective_message is not None:
                await update.effective_message.reply_text(ACCESS_DENIED_MESSAGE)
            return
        await handler(update, context)

    return wrapper
