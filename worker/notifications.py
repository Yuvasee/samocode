"""Notifications for Samocode orchestrator.

Simple HTTP-based notifications using Telegram Bot API.
Failures are logged but don't stop execution.
"""

import logging

import requests

logger = logging.getLogger("samocode.notifications")


def send_telegram_message(
    message: str,
    bot_token: str,
    chat_id: str,
    timeout: int = 5,
) -> bool:
    """Send message via Telegram Bot API. Single retry on timeout/connection error."""
    if not bot_token or not chat_id:
        logger.debug("Telegram not configured, skipping notification")
        return False

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "Markdown",
    }

    for attempt in range(2):
        try:
            response = requests.post(url, json=payload, timeout=timeout)
            response.raise_for_status()
            logger.debug("Telegram notification sent successfully")
            return True
        except requests.Timeout:
            if attempt == 0:
                logger.warning("Telegram notification timed out, retrying...")
                continue
            logger.warning("Telegram notification timed out after retry")
            return False
        except requests.ConnectionError:
            if attempt == 0:
                logger.warning("Telegram connection error, retrying...")
                continue
            logger.warning("Telegram connection error after retry")
            return False
        except requests.RequestException as e:
            logger.warning(f"Telegram notification failed: {e}")
            return False
        except Exception as e:
            logger.warning(f"Unexpected error sending Telegram: {e}")
            return False

    return False


def notify_blocked(
    reason: str,
    session_name: str,
    needs: str | None,
    bot_token: str,
    chat_id: str,
) -> None:
    """Notify that workflow is blocked."""
    needs_text = f"\n*Needs:* `{needs}`" if needs else ""
    message = (
        f"*Samocode Blocked*\n\n"
        f"*Session:* `{session_name}`\n"
        f"*Reason:* `{reason}`{needs_text}\n\n"
        f"Check session files."
    )
    send_telegram_message(message, bot_token, chat_id)


def notify_waiting(
    waiting_for: str,
    session_name: str,
    bot_token: str,
    chat_id: str,
) -> None:
    """Notify that workflow is waiting for input."""
    message = (
        f"*Samocode Waiting*\n\n"
        f"*Session:* `{session_name}`\n"
        f"*Waiting for:* `{waiting_for}`\n\n"
        f"Check session files."
    )
    send_telegram_message(message, bot_token, chat_id)


def notify_complete(
    summary: str,
    session_name: str,
    iterations: int,
    bot_token: str,
    chat_id: str,
) -> None:
    """Notify that workflow completed successfully."""
    message = (
        f"*Samocode Complete*\n\n"
        f"*Session:* `{session_name}`\n"
        f"*Iterations:* {iterations}\n"
        f"*Summary:* `{summary}`"
    )
    send_telegram_message(message, bot_token, chat_id)


def notify_error(
    error_message: str,
    session_name: str,
    iteration: int,
    bot_token: str,
    chat_id: str,
) -> None:
    """Notify that orchestrator encountered an error."""
    truncated = (
        error_message[:500] + "..." if len(error_message) > 500 else error_message
    )
    message = (
        f"*Samocode Error*\n\n"
        f"*Session:* `{session_name}`\n"
        f"*Iteration:* {iteration}\n"
        f"*Error:* `{truncated}`\n\n"
        f"Check logs for full details."
    )
    send_telegram_message(message, bot_token, chat_id)
