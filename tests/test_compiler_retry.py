"""Tests for LLM retry logic in compiler.py."""

from openkb.agent.compiler import TruncatedResponseError, _should_retry_exception


# Custom exception classes for testing (so we can control the type name)
class TimeoutError(Exception):
    """Simulates litellm.Timeout."""

    pass


class APIError(Exception):
    """Simulates litellm.APIError (5xx)."""

    pass


class InvalidAPIError(APIError):
    """Simulates InvalidAPIError (4xx)."""

    pass


class BadRequestError(Exception):
    """Simulates BadRequestError."""

    pass


class RateLimitError(Exception):
    """Simulates litellm.RateLimitError."""

    pass


class AuthenticationError(Exception):
    """Simulates AuthenticationError."""

    pass


class PermissionError(Exception):
    """Simulates PermissionError."""

    pass


class ServiceUnavailableError(Exception):
    """Simulates ServiceUnavailableError."""

    pass


class TestShouldRetryException:
    """Test the exception filtering logic for retry decisions."""

    def test_retryable_timeout(self):
        """Timeout should be retryable."""
        exc = TimeoutError("Gateway Timeout")
        assert _should_retry_exception(exc) is True

    def test_retryable_api_error_5xx(self):
        """5xx API errors should be retryable."""
        exc = APIError("503 Service Unavailable")
        assert _should_retry_exception(exc) is True

    def test_not_retryable_invalid_api_error(self):
        """InvalidAPIError (4xx) should NOT be retryable."""
        exc = InvalidAPIError("400 Bad Request")
        assert _should_retry_exception(exc) is False

    def test_retryable_rate_limit(self):
        """Rate limit errors should be retryable."""
        exc = RateLimitError("429 Too Many Requests")
        assert _should_retry_exception(exc) is True

    def test_retryable_connection_error(self):
        """Connection errors should be retryable."""
        exc = ConnectionError("Connection refused")
        assert _should_retry_exception(exc) is True

    def test_retryable_service_unavailable(self):
        """Service unavailable errors should be retryable."""
        exc = ServiceUnavailableError("Service down")
        assert _should_retry_exception(exc) is True

    def test_not_retryable_truncation(self):
        """Truncated output should NOT be retryable."""
        exc = TruncatedResponseError("hit length limit")
        assert _should_retry_exception(exc) is False

    def test_not_retryable_value_error(self):
        """ValueError should NOT be retryable."""
        exc = ValueError("empty content")
        assert _should_retry_exception(exc) is False

    def test_not_retryable_type_error(self):
        """TypeError should NOT be retryable."""
        exc = TypeError("malformed")
        assert _should_retry_exception(exc) is False

    def test_not_retryable_auth_error(self):
        """Authentication errors should NOT be retryable."""
        exc = AuthenticationError("invalid API key")
        assert _should_retry_exception(exc) is False

    def test_not_retryable_permission_error(self):
        """Permission errors should NOT be retryable."""
        exc = PermissionError("forbidden")
        assert _should_retry_exception(exc) is False

    def test_not_retryable_bad_request(self):
        """BadRequest errors should NOT be retryable."""
        exc = BadRequestError("invalid params")
        assert _should_retry_exception(exc) is False

    def test_not_retryable_unknown(self):
        """Unknown errors should NOT be retried (conservative)."""

        class WeirdCustomError(Exception):
            pass

        exc = WeirdCustomError("something weird")
        assert _should_retry_exception(exc) is False

    def test_not_retryable_generic_exception(self):
        """Generic Exception without special name should NOT be retried."""
        exc = Exception("generic error")
        assert _should_retry_exception(exc) is False
