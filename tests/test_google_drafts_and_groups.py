"""Tests for Gmail draft creation and Google Groups membership."""

import base64
import email
import email.policy
import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from googleapiclient.errors import HttpError

from pkm_bridge.google_gmail_client import GoogleGmailClient
from pkm_bridge.google_oauth import GMAIL_SCOPE_READ, GMAIL_SCOPES, missing_scopes
from pkm_bridge.tools import google_gmail, google_groups
from pkm_bridge.tools.google_gmail import GoogleGmailTool, as_address_list
from pkm_bridge.tools.google_groups import GoogleGroupsTool

LOGGER = logging.getLogger("test")
GUIDE = "https://pelorus-nav.com/doc/userguide"


def _token(scope: str) -> SimpleNamespace:
    return SimpleNamespace(access_token="a", refresh_token="r", scope=scope)


def _http_error(status: int) -> HttpError:
    return HttpError(SimpleNamespace(status=status, reason="x"), b"{}")


class TestDraftMessage:
    def test_html_draft_has_text_alternative_and_untouched_links(self):
        html = f'<div>See <a href="{GUIDE}">{GUIDE}</a> &amp; enjoy</div>'
        msg = GoogleGmailClient.build_draft_message(["a@example.com"], "Hi", html_body=html)

        assert msg["To"] == "a@example.com"
        assert msg.get_content_type() == "multipart/alternative"
        text, rich = msg.get_payload()
        assert text.get_content().strip() == f"See {GUIDE} & enjoy"
        assert f'href="{GUIDE}"' in rich.get_content()

    def test_raw_round_trip_keeps_the_href(self):
        html = f'<a href="{GUIDE}">guide</a>'
        msg = GoogleGmailClient.build_draft_message(["a@example.com"], "Hi", html_body=html)
        raw = base64.urlsafe_b64encode(msg.as_bytes())
        parsed = email.message_from_bytes(
            base64.urlsafe_b64decode(raw), policy=email.policy.default
        )
        assert f'href="{GUIDE}"' in parsed.get_body(("html",)).get_content()

    def test_text_only_draft_is_single_part(self):
        msg = GoogleGmailClient.build_draft_message(
            ["a@example.com", "b@example.com"], "Hi", text_body="plain", cc=["c@example.com"]
        )
        assert msg.get_content_type() == "text/plain"
        assert msg["To"] == "a@example.com, b@example.com"
        assert msg["Cc"] == "c@example.com"

    def test_requires_recipient_and_body(self):
        with pytest.raises(ValueError):
            GoogleGmailClient.build_draft_message([], "Hi", text_body="x")
        with pytest.raises(ValueError):
            GoogleGmailClient.build_draft_message(["a@example.com"], "Hi")

    def test_html_to_text_shows_targets_of_labelled_links(self):
        text = GoogleGmailClient.html_to_text(
            f'<div>Read the <a href="{GUIDE}">User Guide</a>.</div><div>Bye</div>'
        )
        assert text == f"Read the User Guide ({GUIDE}).\nBye"


def test_missing_scopes():
    assert missing_scopes(GMAIL_SCOPE_READ, GMAIL_SCOPES) == GMAIL_SCOPES[1:]
    assert missing_scopes(" ".join(GMAIL_SCOPES), GMAIL_SCOPES) == []
    assert missing_scopes(None, [GMAIL_SCOPE_READ]) == [GMAIL_SCOPE_READ]


def test_as_address_list():
    assert as_address_list("a@x.com, b@x.com") == ["a@x.com", "b@x.com"]
    assert as_address_list(["a@x.com", " "]) == ["a@x.com"]
    assert as_address_list(None) == []


class TestGmailToolDrafts:
    def test_read_only_grant_asks_for_reconnect(self, monkeypatch):
        monkeypatch.setattr(google_gmail, "load_gmail_token", lambda *a: _token(GMAIL_SCOPE_READ))
        result = GoogleGmailTool(LOGGER, MagicMock()).execute(
            {"action": "create_draft", "to": ["a@x.com"], "html_body": "<p>x</p>"}
        )
        assert "Reconnect" in result

    def test_creates_draft_and_reports_link(self, monkeypatch):
        client = MagicMock()
        client.create_draft.return_value = {"id": "r1", "message": {"id": "m1"}}
        tool = GoogleGmailTool(LOGGER, MagicMock())
        monkeypatch.setattr(tool, "get_client", lambda scopes=None: client)

        result = tool.execute(
            {"action": "create_draft", "to": "a@x.com", "subject": "S", "html_body": "<p>x</p>"}
        )

        assert client.create_draft.call_args.kwargs["to"] == ["a@x.com"]
        assert "r1" in result and "compose=m1" in result and "Not sent" in result

    def test_rejects_draft_without_body(self, monkeypatch):
        tool = GoogleGmailTool(LOGGER, MagicMock())
        monkeypatch.setattr(tool, "get_client", lambda scopes=None: MagicMock())
        assert "required" in tool.execute({"action": "create_draft", "to": ["a@x.com"]})


class TestGroupsTool:
    @pytest.fixture
    def tool(self):
        return GoogleGroupsTool(LOGGER, MagicMock())

    def _service(self, error: HttpError | None = None) -> MagicMock:
        service = MagicMock()
        if error:
            service.members.return_value.insert.return_value.execute.side_effect = error
        return service

    def test_adds_member(self, tool):
        service = self._service()
        result = tool.add_member(service, "g@x.com", "a@x.com", "MEMBER")
        service.members.return_value.insert.assert_called_once_with(
            groupKey="g@x.com", body={"email": "a@x.com", "role": "MEMBER"}
        )
        assert result.startswith("Added")

    def test_existing_member_is_not_an_error(self, tool):
        assert "already a member" in tool.add_member(
            self._service(_http_error(409)), "g@x.com", "a@x.com", "MEMBER"
        )

    def test_consumer_group_failure_explains_the_domain_rule(self, tool):
        result = tool.add_member(self._service(_http_error(403)), "g@x.com", "a@x.com", "MEMBER")
        assert "googlegroups.com" in result

    def test_grant_without_group_scope_asks_for_reconnect(self, tool, monkeypatch):
        monkeypatch.setattr(google_groups, "load_gmail_token", lambda *a: _token(GMAIL_SCOPE_READ))
        result = tool.execute({"action": "add_member", "group": "g@x.com", "email": "a@x.com"})
        assert "Reconnect" in result

    def test_validates_role(self, tool):
        result = tool.execute(
            {"action": "add_member", "group": "g@x.com", "email": "a@x.com", "role": "KING"}
        )
        assert result.startswith("Error")
