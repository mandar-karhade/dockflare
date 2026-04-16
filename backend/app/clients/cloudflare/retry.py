"""Retry and rate-limit handling for CF API calls."""

from __future__ import annotations

import structlog
from tenacity import (
    RetryCallState,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.core.errors import CFAPIError

logger = structlog.get_logger()


class RateLimitError(CFAPIError):
    """429 Too Many Requests from CF API."""

    def __init__(self, retry_after: float = 1.0) -> None:
        self.retry_after = retry_after
        super().__init__(cf_code=429, message=f"Rate limited, retry after {retry_after}s")


def _log_retry(retry_state: RetryCallState) -> None:
    logger.warning(
        "cf_api.retry",
        attempt=retry_state.attempt_number,
        wait=retry_state.next_action.sleep if retry_state.next_action else 0,  # type: ignore[union-attr]
    )


cf_retry = retry(
    retry=retry_if_exception_type(RateLimitError),
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, min=1, max=60),
    before_sleep=_log_retry,
    reraise=True,
)
