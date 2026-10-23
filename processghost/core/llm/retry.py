"""One retry policy for every provider call, replacing the blanket `@retry(stop_after_attempt(3))`
that used to sit on each client method.

What was wrong with the blanket policy (docs/18 entry 4, docs/23): it retried EVERYTHING, including
a response that failed schema validation and auth errors, and it stacked on top of ChatOpenAI's own
internal retries, so one rate-limited call could be billed several times; its 10 s backoff cap was
too short for the rate limits the Checker actually hit.

The policy here:
- retry only errors that can succeed on a second attempt: rate limits, timeouts, connection errors,
  5xx responses -- up to RETRY_MAX_ATTEMPTS with exponential backoff + jitter (cap RETRY_MAX_WAIT_S);
- a response that does not match its schema (StructuredOutputError) is re-asked ONCE, not three times;
- everything else (auth, bad request, programming errors) fails immediately;
- every retry is written to the run's trace (Metrics.record_failure, stage "retry") so a call that
  was retried is visible, not silently absorbed.
"""
from __future__ import annotations

from typing import Callable

import anthropic
import httpx
import openai
from tenacity import RetryCallState, retry, retry_if_exception, wait_exponential_jitter

from core import config


class StructuredOutputError(RuntimeError):
    """The model answered, but not in the schema's shape (or not at all)."""


_TRANSIENT = (
    openai.RateLimitError,
    openai.APITimeoutError,
    openai.APIConnectionError,
    openai.InternalServerError,
    anthropic.RateLimitError,
    anthropic.APITimeoutError,
    anthropic.APIConnectionError,
    anthropic.InternalServerError,
    httpx.TimeoutException,
    httpx.TransportError,
)


# A 429 that no amount of waiting fixes: the account is out of credit or at its spending cap. The SDK
# raises these as RateLimitError, so they must be told apart by error code or they get retried for minutes.
_NOT_TRANSIENT_CODES = {"insufficient_quota", "credit_balance_exhausted", "billing_hard_limit_reached"}


def is_transient(exc: BaseException) -> bool:
    if getattr(exc, "code", None) in _NOT_TRANSIENT_CODES:
        return False
    if isinstance(exc, _TRANSIENT):
        return True
    status = getattr(exc, "status_code", None)  # SDKs that do not subclass the openai errors
    return isinstance(status, int) and (status == 429 or status >= 500)


def _retryable(exc: BaseException) -> bool:
    return is_transient(exc) or isinstance(exc, StructuredOutputError)


def _stop(state: RetryCallState) -> bool:
    exc = state.outcome.exception() if state.outcome else None
    limit = config.RETRY_MAX_PARSE_ATTEMPTS if isinstance(exc, StructuredOutputError) else config.RETRY_MAX_ATTEMPTS
    return state.attempt_number >= limit


def _log_retry(state: RetryCallState) -> None:
    """Record the failed attempt on the client's Metrics (the decorated callables are methods, so the
    first positional arg is the client). Never raises: logging must not turn a retry into a failure."""
    try:
        client = state.args[0]
        exc = state.outcome.exception()
        client.metrics.record_failure(
            "retry",
            f"{type(exc).__name__}: {exc}"[:300],
            meta={"attempt": state.attempt_number, "fn": state.fn.__name__ if state.fn else None},
        )
    except Exception:  # noqa: BLE001
        pass


def llm_retry() -> Callable:
    return retry(
        retry=retry_if_exception(_retryable),
        stop=_stop,
        wait=wait_exponential_jitter(initial=config.RETRY_INITIAL_WAIT_S, max=config.RETRY_MAX_WAIT_S),
        before_sleep=_log_retry,
        reraise=True,
    )
