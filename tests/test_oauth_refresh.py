"""Tests for OAuth refresh bookkeeping and connection status."""

from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest
import requests
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from pkm_bridge.database import Base, OAuthToken
from pkm_bridge.db_repository import OAuthRepository


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[OAuthToken.__table__])
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _add_token(db, expires_in: timedelta, refresh_token: str | None = "r1") -> OAuthToken:
    token = OAuthToken(
        service="google_calendar",
        access_token="a1",
        refresh_token=refresh_token,
        expires_at=datetime.utcnow() + expires_in,
    )
    db.add(token)
    db.commit()
    return token


def _http_error(status: int, body: str) -> requests.HTTPError:
    response = requests.Response()
    response.status_code = status
    response._content = body.encode()
    return requests.HTTPError(response=response)


def test_unexpired_token_is_returned_without_refresh(db):
    _add_token(db, timedelta(hours=1))
    handler = MagicMock()
    token = OAuthRepository.refresh_if_expired(db, "google_calendar", handler)
    assert token.access_token == "a1"
    handler.refresh_token.assert_not_called()


def test_successful_refresh_saves_and_clears_old_error(db):
    token = _add_token(db, timedelta(hours=-1))
    token.refresh_error = "HTTP 400: old"
    db.commit()
    handler = MagicMock()
    handler.refresh_token.return_value = {
        "access_token": "a2",
        "expires_at": datetime.utcnow() + timedelta(hours=1),
    }

    token = OAuthRepository.refresh_if_expired(db, "google_calendar", handler)

    assert token.access_token == "a2"
    assert token.refresh_token == "r1"
    assert token.refresh_error is None


def test_rejected_refresh_is_recorded_and_status_disconnects(db):
    _add_token(db, timedelta(hours=-1))
    handler = MagicMock()
    handler.refresh_token.side_effect = _http_error(400, '{"error": "invalid_grant"}')

    assert OAuthRepository.refresh_if_expired(db, "google_calendar", handler) is None

    token = OAuthRepository.get_token(db, "google_calendar")
    assert "invalid_grant" in token.refresh_error
    assert token.refresh_failed_at is not None
    status = OAuthRepository.connection_status(token)
    assert status["connected"] is False
    assert status["auto_refreshable"] is False


def test_transient_refresh_failure_is_not_recorded(db):
    _add_token(db, timedelta(hours=-1))
    handler = MagicMock()
    handler.refresh_token.side_effect = _http_error(503, "unavailable")

    assert OAuthRepository.refresh_if_expired(db, "google_calendar", handler) is None

    token = OAuthRepository.get_token(db, "google_calendar")
    assert token.refresh_error is None
    assert OAuthRepository.connection_status(token)["connected"] is True


def test_expired_token_without_refresh_token_is_not_refreshed(db):
    _add_token(db, timedelta(hours=-1), refresh_token=None)
    handler = MagicMock()
    assert OAuthRepository.refresh_if_expired(db, "google_calendar", handler) is None
    handler.refresh_token.assert_not_called()
    token = OAuthRepository.get_token(db, "google_calendar")
    assert OAuthRepository.connection_status(token)["connected"] is False


def test_reauthorizing_clears_refresh_error(db):
    token = _add_token(db, timedelta(hours=-1))
    token.refresh_error = "HTTP 400: invalid_grant"
    db.commit()
    token = OAuthRepository.save_token(
        db, "google_calendar", "a3", "r2", datetime.utcnow() + timedelta(hours=1)
    )
    assert token.refresh_error is None
    assert OAuthRepository.connection_status(token)["connected"] is True
