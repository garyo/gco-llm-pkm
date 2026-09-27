"""Tests for HttpSession: default timeout and a single retry on 429/5xx."""

import pytest
import requests

from pkm_bridge import http_session
from pkm_bridge.http_session import DEFAULT_TIMEOUT, MAX_RETRY_DELAY, HttpSession


def _response(status: int, headers: dict | None = None) -> requests.Response:
    r = requests.Response()
    r.status_code = status
    r.headers.update(headers or {})
    return r


@pytest.fixture
def calls(monkeypatch):
    """Queue responses for requests.Session.request; record each call's kwargs."""
    queued: list[requests.Response] = []
    made: list[tuple[str, dict]] = []
    sleeps: list[float] = []

    def fake_request(self, method, url, **kwargs):
        made.append((method, kwargs))
        return queued.pop(0)

    monkeypatch.setattr(requests.Session, "request", fake_request)
    monkeypatch.setattr(http_session.time, "sleep", sleeps.append)
    return queued, made, sleeps


def test_applies_default_timeout(calls):
    queued, made, _ = calls
    queued.append(_response(200))
    HttpSession().get("https://example.test")
    assert made[0][1]["timeout"] == DEFAULT_TIMEOUT


def test_explicit_timeout_wins(calls):
    queued, made, _ = calls
    queued.append(_response(200))
    HttpSession().get("https://example.test", timeout=3)
    assert made[0][1]["timeout"] == 3


def test_retries_rate_limit_once_honouring_retry_after(calls):
    queued, made, sleeps = calls
    queued.extend([_response(429, {"Retry-After": "2"}), _response(200)])
    assert HttpSession().post("https://example.test").status_code == 200
    assert len(made) == 2
    assert sleeps == [2.0]


def test_retry_after_is_capped(calls):
    queued, _, sleeps = calls
    queued.extend([_response(429, {"Retry-After": "3600"}), _response(200)])
    HttpSession().get("https://example.test")
    assert sleeps == [MAX_RETRY_DELAY]


def test_gives_up_after_one_retry(calls):
    queued, made, _ = calls
    queued.extend([_response(503), _response(503)])
    assert HttpSession().get("https://example.test").status_code == 503
    assert len(made) == 2


def test_server_error_on_post_is_not_retried(calls):
    """A POST that failed with 5xx may have taken effect; repeating could duplicate it."""
    queued, made, _ = calls
    queued.append(_response(500))
    assert HttpSession().post("https://example.test").status_code == 500
    assert len(made) == 1


def test_client_error_is_not_retried(calls):
    queued, made, _ = calls
    queued.append(_response(404))
    HttpSession().get("https://example.test")
    assert len(made) == 1
