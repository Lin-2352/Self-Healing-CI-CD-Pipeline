"""Constant-time comparisons for tokens and webhook signatures."""

import hashlib
import hmac


def secrets_equal(left: str, right: str) -> bool:
    """Return whether two secrets match.

    A different length is a mismatch. `hmac.compare_digest` is only called when the
    lengths agree, so a short guess cannot raise.

    Args:
        left: The value supplied by the caller.
        right: The value this process expected.

    Returns:
        True when both are non-empty and equal.
    """
    if not left or not right or len(left) != len(right):
        return False
    return hmac.compare_digest(left, right)


def github_signature(secret: str, body: bytes) -> str:
    """Build the `X-Hub-Signature-256` value for the raw request body.

    Args:
        secret: `GITHUB_WEBHOOK_SECRET`.
        body: The exact bytes GitHub signed.

    Returns:
        The header value, including the `sha256=` prefix.
    """
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"
