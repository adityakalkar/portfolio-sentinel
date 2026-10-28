"""Tests for Slack delivery (task 5.5). No network: the HTTP call is replaced by a fake."""

import json

import pytest

from pm_agent import delivery

BRIEF = ("# 1 limit breach: NVDA at 15.2% vs a 15.0% limit\n\n"
         "1. **NVDA is over its limit.** It rose 10.0% overnight.\n"
         "2. **UNH fell 8.0%.**\n")


def test_markdown_becomes_slack_formatting():
    assert delivery.to_slack_markdown(BRIEF) == (
        "*1 limit breach: NVDA at 15.2% vs a 15.0% limit*\n\n"
        "1. *NVDA is over its limit.* It rose 10.0% overnight.\n"
        "2. *UNH fell 8.0%.*"
    )


def test_message_names_the_portfolio_and_date():
    message = delivery.slack_message("sample_fund", "2026-10-22", BRIEF)
    assert message.startswith("_Morning brief: sample_fund, 2026-10-22_\n")
    assert "*1 limit breach" in message


class FakeResponse:
    def __init__(self, status):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_post_sends_json_to_the_webhook():
    sent = []

    def fake_open(request, timeout):
        sent.append(request)
        return FakeResponse(200)

    delivery.post_to_slack("hello", "https://hooks.slack.com/services/T/B/x", opener=fake_open)

    request = sent[0]
    assert request.full_url == "https://hooks.slack.com/services/T/B/x"
    assert request.get_method() == "POST"
    assert request.get_header("Content-type") == "application/json"
    assert json.loads(request.data) == {"text": "hello"}


def test_post_raises_on_a_failed_delivery():
    with pytest.raises(RuntimeError, match="Slack returned HTTP 404"):
        delivery.post_to_slack("hi", "https://hooks.slack.com/x",
                               opener=lambda request, timeout: FakeResponse(404))


def test_webhook_url_must_be_set(monkeypatch):
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    with pytest.raises(ValueError, match="Set SLACK_WEBHOOK_URL"):
        delivery.webhook_url()
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/x")
    assert delivery.webhook_url() == "https://hooks.slack.com/x"
