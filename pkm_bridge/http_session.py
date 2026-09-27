"""HTTP session for third-party APIs: a default timeout and one retry.

requests waits forever by default, so a stalled TickTick or OAuth endpoint
would hang the request thread (or the scheduler) indefinitely.
"""

import time

import requests

DEFAULT_TIMEOUT = 15.0
DEFAULT_RETRY_DELAY = 1.0
MAX_RETRY_DELAY = 10.0

# A 429 means the request was refused unprocessed, so any method may retry it.
# A 5xx may have been partly processed, so only methods safe to repeat retry.
_RETRY_ANY_METHOD = {429}
_RETRY_IDEMPOTENT = {500, 502, 503, 504}
_IDEMPOTENT_METHODS = {"GET", "HEAD", "OPTIONS", "PUT", "DELETE"}


def _should_retry(method: str, status: int) -> bool:
    if status in _RETRY_ANY_METHOD:
        return True
    return status in _RETRY_IDEMPOTENT and method.upper() in _IDEMPOTENT_METHODS


def _retry_delay(response: requests.Response) -> float:
    """Seconds to wait, from Retry-After when it gives seconds, capped."""
    try:
        delay = float(response.headers.get("Retry-After", DEFAULT_RETRY_DELAY))
    except ValueError:
        delay = DEFAULT_RETRY_DELAY
    return max(0.0, min(delay, MAX_RETRY_DELAY))


class HttpSession(requests.Session):
    """requests.Session that times out and retries once on 429 and 5xx."""

    def __init__(self, timeout: float = DEFAULT_TIMEOUT):
        super().__init__()
        self.timeout = timeout

    def request(self, method: str, url: str, **kwargs) -> requests.Response:
        kwargs.setdefault("timeout", self.timeout)
        response = super().request(method, url, **kwargs)
        if _should_retry(method, response.status_code):
            time.sleep(_retry_delay(response))
            response = super().request(method, url, **kwargs)
        return response
