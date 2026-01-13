"""Tests for worker/notifications.py - Telegram notifications.

This module tests:
- send_telegram_message HTTP behavior
- Retry logic for timeouts and connection errors
- Message formatting for each notification type
"""

from unittest.mock import MagicMock, patch

import requests

from worker.notifications import (
    notify_blocked,
    notify_complete,
    notify_error,
    notify_waiting,
    send_telegram_message,
)


class TestSendTelegramMessage:
    """Tests for send_telegram_message - HTTP posting to Telegram."""

    def test_not_configured_skips_request(self) -> None:
        """Returns False immediately when bot_token or chat_id empty."""
        with patch("worker.notifications.requests.post") as mock_post:
            result = send_telegram_message("test", "", "123")

            assert result is False
            mock_post.assert_not_called()

    def test_not_configured_empty_chat_id(self) -> None:
        """Returns False when chat_id is empty."""
        with patch("worker.notifications.requests.post") as mock_post:
            result = send_telegram_message("test", "token", "")

            assert result is False
            mock_post.assert_not_called()

    def test_successful_send(self) -> None:
        """Returns True on successful HTTP post."""
        with patch("worker.notifications.requests.post") as mock_post:
            mock_post.return_value.raise_for_status = MagicMock()

            result = send_telegram_message("Hello", "bot_token", "chat_id")

            assert result is True
            mock_post.assert_called_once()
            call_args = mock_post.call_args
            assert "bot_token" in call_args[0][0]
            assert call_args[1]["json"]["text"] == "Hello"
            assert call_args[1]["json"]["chat_id"] == "chat_id"

    def test_timeout_retries_once(self) -> None:
        """Retries once on timeout, then returns False."""
        with patch("worker.notifications.requests.post") as mock_post:
            mock_post.side_effect = requests.Timeout()

            result = send_telegram_message("test", "token", "chat")

            assert result is False
            assert mock_post.call_count == 2

    def test_connection_error_retries_once(self) -> None:
        """Retries once on connection error, then returns False."""
        with patch("worker.notifications.requests.post") as mock_post:
            mock_post.side_effect = requests.ConnectionError()

            result = send_telegram_message("test", "token", "chat")

            assert result is False
            assert mock_post.call_count == 2

    def test_request_exception_no_retry(self) -> None:
        """Other request exceptions don't retry."""
        with patch("worker.notifications.requests.post") as mock_post:
            mock_post.side_effect = requests.RequestException("Bad request")

            result = send_telegram_message("test", "token", "chat")

            assert result is False
            assert mock_post.call_count == 1

    def test_success_after_retry(self) -> None:
        """Returns True if second attempt succeeds."""
        with patch("worker.notifications.requests.post") as mock_post:
            mock_response = MagicMock()
            mock_response.raise_for_status = MagicMock()
            mock_post.side_effect = [requests.Timeout(), mock_response]

            result = send_telegram_message("test", "token", "chat")

            assert result is True
            assert mock_post.call_count == 2


class TestNotifyBlocked:
    """Tests for notify_blocked - blocked workflow notification."""

    def test_formats_message_with_needs(self) -> None:
        """Message includes reason and needs."""
        with patch("worker.notifications.send_telegram_message") as mock_send:
            notify_blocked("Test failure", "my-session", "help", "token", "chat")

            mock_send.assert_called_once()
            message = mock_send.call_args[0][0]
            assert "Blocked" in message
            assert "my-session" in message
            assert "Test failure" in message
            assert "help" in message

    def test_formats_message_without_needs(self) -> None:
        """Message works without needs field."""
        with patch("worker.notifications.send_telegram_message") as mock_send:
            notify_blocked("Test failure", "my-session", None, "token", "chat")

            message = mock_send.call_args[0][0]
            assert "Blocked" in message
            assert "Test failure" in message


class TestNotifyWaiting:
    """Tests for notify_waiting - waiting for input notification."""

    def test_formats_message(self) -> None:
        """Message includes waiting_for info."""
        with patch("worker.notifications.send_telegram_message") as mock_send:
            notify_waiting("qa_answers", "my-session", "token", "chat")

            message = mock_send.call_args[0][0]
            assert "Waiting" in message
            assert "my-session" in message
            assert "qa_answers" in message


class TestNotifyComplete:
    """Tests for notify_complete - workflow completed notification."""

    def test_formats_message(self) -> None:
        """Message includes summary and iterations."""
        with patch("worker.notifications.send_telegram_message") as mock_send:
            notify_complete("All done!", "my-session", 5, "token", "chat")

            message = mock_send.call_args[0][0]
            assert "Complete" in message
            assert "my-session" in message
            assert "All done!" in message
            assert "5" in message


class TestNotifyError:
    """Tests for notify_error - error notification."""

    def test_formats_message(self) -> None:
        """Message includes error and iteration."""
        with patch("worker.notifications.send_telegram_message") as mock_send:
            notify_error("Something broke", "my-session", 3, "token", "chat")

            message = mock_send.call_args[0][0]
            assert "Error" in message
            assert "my-session" in message
            assert "Something broke" in message
            assert "3" in message

    def test_truncates_long_error(self) -> None:
        """Long error messages are truncated."""
        with patch("worker.notifications.send_telegram_message") as mock_send:
            long_error = "x" * 600
            notify_error(long_error, "my-session", 1, "token", "chat")

            message = mock_send.call_args[0][0]
            assert len(message) < 700
            assert "..." in message
