"""Tests for the ntfy push-notification client and the notify tool."""

import base64
import json
import logging
from types import SimpleNamespace

import pytest
import requests

from pkm_bridge import ntfy
from pkm_bridge.tools.notify import NotifyTool

NTFY_ENV = ("NTFY_TOPIC", "NTFY_SERVER", "NTFY_USER", "NTFY_PASS", "NTFY_TOKEN")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Start every test from an unconfigured environment."""
    for var in NTFY_ENV:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def captured(monkeypatch):
    """Capture the request ntfy.send() would make, without touching the network."""
    calls = []

    def fake_post(url, data=None, headers=None, timeout=None):
        calls.append(
            {
                "url": url,
                "payload": json.loads(data.decode("utf-8")),
                "raw": data,
                "headers": headers,
                "timeout": timeout,
            }
        )
        return SimpleNamespace(
            ok=True,
            status_code=200,
            text="",
            json=lambda: {"id": "abc123"},
        )

    monkeypatch.setattr(ntfy.requests, "post", fake_post)
    return calls


# --- Validation ---------------------------------------------------------


def test_missing_topic_is_rejected():
    with pytest.raises(ntfy.NtfyError, match="No ntfy topic"):
        ntfy.send("hello")


def test_topic_must_be_a_single_path_segment():
    for bad in ("has/slash", "has space", "", "x" * 65):
        with pytest.raises(ntfy.NtfyError):
            ntfy.send("hello", topic=bad)


def test_invalid_priority_is_rejected():
    with pytest.raises(ntfy.NtfyError, match="Invalid priority"):
        ntfy.send("hello", topic="t", priority="loud")


def test_blank_message_is_rejected():
    with pytest.raises(ntfy.NtfyError, match="empty notification"):
        ntfy.send("   \n  ", topic="t")


# --- Request construction -----------------------------------------------


def test_publishes_json_to_server_root(captured):
    ntfy.send("body", topic="mytopic")

    (call,) = captured
    assert call["url"] == "https://ntfy.sh/"
    assert call["payload"] == {"topic": "mytopic", "message": "body", "priority": 3}
    assert call["headers"]["Content-Type"] == "application/json; charset=utf-8"
    assert call["timeout"] == ntfy.DEFAULT_TIMEOUT


def test_env_supplies_server_and_default_topic(monkeypatch, captured):
    monkeypatch.setenv("NTFY_SERVER", "https://ntfy.example.com/")
    monkeypatch.setenv("NTFY_TOPIC", "from-env")

    ntfy.send("body")

    assert captured[0]["url"] == "https://ntfy.example.com/"
    assert captured[0]["payload"]["topic"] == "from-env"


def test_explicit_topic_overrides_env(monkeypatch, captured):
    monkeypatch.setenv("NTFY_TOPIC", "from-env")

    ntfy.send("body", topic="explicit")

    assert captured[0]["payload"]["topic"] == "explicit"


def test_optional_fields_are_mapped(captured):
    ntfy.send(
        "body",
        topic="t",
        title="Heads up",
        priority="urgent",
        tags=["warning", "bell"],
        click_url="https://example.com/x?a=1&b=2",
        markdown=True,
    )

    assert captured[0]["payload"] == {
        "topic": "t",
        "message": "body",
        "priority": 5,
        "title": "Heads up",
        "tags": ["warning", "bell"],
        "click": "https://example.com/x?a=1&b=2",
        "markdown": True,
    }


def test_omitted_fields_are_absent(captured):
    ntfy.send("body", topic="t", title="", tags=[], click_url=None, markdown=False)

    assert set(captured[0]["payload"]) == {"topic", "message", "priority"}


def test_title_is_collapsed_to_one_line_and_capped(captured):
    ntfy.send("body", topic="t", title="  two\nlines   here  ")
    assert captured[0]["payload"]["title"] == "two lines here"

    ntfy.send("body", topic="t", title="x" * 400)
    assert len(captured[1]["payload"]["title"]) == ntfy.MAX_TITLE_LEN


# --- UTF-8 --------------------------------------------------------------


def test_utf8_survives_in_body_title_and_tags(captured):
    ntfy.send(
        "Réunion à 15h — café ☕ 完了",
        topic="t",
        title="Rappel ✅ naïve",
        tags=["café", "🎉"],
    )

    payload = captured[0]["payload"]
    assert payload["message"] == "Réunion à 15h — café ☕ 完了"
    assert payload["title"] == "Rappel ✅ naïve"
    assert payload["tags"] == ["café", "🎉"]
    # Sent as real UTF-8 bytes, not \u-escapes.
    assert "café ☕ 完了".encode("utf-8") in captured[0]["raw"]


def test_headers_carry_no_user_content(captured):
    """Everything user-supplied rides in the JSON body, so headers can't be injected."""
    ntfy.send("body\ninjected: yes", topic="t", title="a\r\nX-Evil: 1")

    assert set(captured[0]["headers"]) == {"Content-Type"}


# --- Auth ---------------------------------------------------------------


def test_no_auth_header_when_unconfigured(captured):
    ntfy.send("body", topic="t")
    assert "Authorization" not in captured[0]["headers"]


def test_basic_auth_from_user_and_pass(monkeypatch, captured):
    monkeypatch.setenv("NTFY_USER", "gary")
    monkeypatch.setenv("NTFY_PASS", "s3cret")

    ntfy.send("body", topic="t")

    scheme, value = captured[0]["headers"]["Authorization"].split()
    assert scheme == "Basic"
    assert base64.b64decode(value).decode() == "gary:s3cret"


def test_basic_auth_wins_over_token(monkeypatch, captured):
    monkeypatch.setenv("NTFY_USER", "gary")
    monkeypatch.setenv("NTFY_PASS", "s3cret")
    monkeypatch.setenv("NTFY_TOKEN", "tk_123")

    ntfy.send("body", topic="t")

    assert captured[0]["headers"]["Authorization"].startswith("Basic ")


def test_bearer_token_when_no_user(monkeypatch, captured):
    monkeypatch.setenv("NTFY_TOKEN", "tk_123")

    ntfy.send("body", topic="t")

    assert captured[0]["headers"]["Authorization"] == "Bearer tk_123"


# --- Responses ----------------------------------------------------------


def test_returns_parsed_response(captured):
    assert ntfy.send("body", topic="t") == {"id": "abc123"}


def test_non_json_response_falls_back_to_status(monkeypatch):
    def fake_post(*args, **kwargs):
        def raise_value_error():
            raise ValueError("not json")

        return SimpleNamespace(ok=True, status_code=200, text="ok", json=raise_value_error)

    monkeypatch.setattr(ntfy.requests, "post", fake_post)
    assert ntfy.send("body", topic="t") == {"status": 200}


def test_http_error_raises_without_leaking_credentials(monkeypatch):
    monkeypatch.setenv("NTFY_USER", "gary")
    monkeypatch.setenv("NTFY_PASS", "s3cret")

    def fake_post(*args, **kwargs):
        return SimpleNamespace(ok=False, status_code=403, text="forbidden", json=dict)

    monkeypatch.setattr(ntfy.requests, "post", fake_post)

    with pytest.raises(ntfy.NtfyError) as excinfo:
        ntfy.send("body", topic="t")

    assert "403" in str(excinfo.value)
    assert "s3cret" not in str(excinfo.value)
    assert "Basic" not in str(excinfo.value)


def test_network_failure_raises_ntfy_error(monkeypatch):
    def fake_post(*args, **kwargs):
        raise requests.ConnectionError("no route to host")

    monkeypatch.setattr(ntfy.requests, "post", fake_post)

    with pytest.raises(ntfy.NtfyError, match="Could not reach ntfy"):
        ntfy.send("body", topic="t")


# --- Tool wrapper -------------------------------------------------------


@pytest.fixture
def tool():
    return NotifyTool(logging.getLogger("test.notify"))


def test_tool_passes_params_through(tool, captured):
    result = tool.execute(
        {
            "message": "body",
            "title": "Title",
            "priority": "high",
            "tags": ["bell"],
            "topic": "overnight",
            "markdown": True,
        }
    )

    assert captured[0]["payload"] == {
        "topic": "overnight",
        "message": "body",
        "priority": 4,
        "title": "Title",
        "tags": ["bell"],
        "markdown": True,
    }
    assert result.startswith("✅")
    assert "abc123" in result


def test_tool_defaults_priority_when_omitted(tool, captured):
    tool.execute({"message": "body", "topic": "t"})
    assert captured[0]["payload"]["priority"] == 3


def test_tool_reports_failure_instead_of_raising(tool):
    result = tool.execute({"message": "body"})  # no topic configured
    assert result.startswith("❌")
    assert "No ntfy topic" in result


def test_tool_schema_matches_supported_priorities(tool):
    assert tool.input_schema["properties"]["priority"]["enum"] == list(ntfy.PRIORITIES)
    assert tool.input_schema["required"] == ["message"]
