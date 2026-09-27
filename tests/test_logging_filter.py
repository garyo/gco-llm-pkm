"""Tests for filtering health-check probes out of access logs."""

import logging

import pytest
import uvicorn

from pkm_bridge.logging_config import HealthCheckFilter, quiet_health_checks


def _record(msg: str, *args) -> logging.LogRecord:
    return logging.LogRecord("access", logging.INFO, __file__, 1, msg, args, None)


WERKZEUG = '%s - - [%s] "%s" %s %s'
UVICORN = '%s - "%s %s HTTP/%s" %d'


@pytest.mark.parametrize(
    "record, dropped",
    [
        (_record(WERKZEUG, "127.0.0.1", "27/Sep/2026", "GET /health HTTP/1.1", "200", "-"), True),
        (
            _record(WERKZEUG, "127.0.0.1", "d", "GET /health HTTP/1.1", "\x1b[32m200\x1b[0m", "-"),
            True,
        ),
        (_record(WERKZEUG, "127.0.0.1", "d", "GET /health HTTP/1.1", "503", "-"), False),
        (_record(WERKZEUG, "127.0.0.1", "d", "GET /healthz HTTP/1.1", "200", "-"), False),
        (_record(WERKZEUG, "10.0.0.1", "d", "GET /api/file/x HTTP/1.1", "200", "-"), False),
        (_record(UVICORN, "127.0.0.1:48974", "GET", "/health", "1.1", 200), True),
        (_record(UVICORN, "127.0.0.1:48974", "GET", "/health", "1.1", 500), False),
        (_record(UVICORN, "1.2.3.4:1", "POST", "/", "1.1", 200), False),
    ],
)
def test_health_filter(record, dropped):
    assert HealthCheckFilter().filter(record) is not dropped


def test_filter_survives_uvicorn_logging_setup():
    uvicorn.Config(app=None)
    quiet_health_checks("uvicorn.access")
    access = logging.getLogger("uvicorn.access")
    assert any(isinstance(f, HealthCheckFilter) for f in access.filters)
    assert not access.filter(_record(UVICORN, "127.0.0.1:1", "GET", "/health", "1.1", 200))
