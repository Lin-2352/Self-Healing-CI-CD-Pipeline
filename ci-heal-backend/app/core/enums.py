"""Labels shared by the database, the API, and (later) the decision engine.

Stored as plain strings, not database-native enums, so SQLite and PostgreSQL behave the same
and adding a label later is a code change rather than a migration.
"""

from enum import StrEnum


class FailureCategory(StrEnum):
    """What kind of CI failure the classifier decided this was."""

    DEPENDENCY_ERROR = "dependency_error"
    CONFIGURATION_ERROR = "configuration_error"
    TEST_FAILURE = "test_failure"
    FLAKY_OR_NETWORK = "flaky_or_network"
    UNKNOWN = "unknown"


class DecisionAction(StrEnum):
    """What the pipeline chose to do about a failure."""

    RETRY = "retry"
    FIX = "fix"
    ESCALATE = "escalate"


class DecisionOutcome(StrEnum):
    """How a decision ended, filled in when a later workflow run reports back."""

    PENDING = "pending"
    VERIFIED_SUCCESS = "verified_success"
    VERIFIED_FAILURE = "verified_failure"
    RETRY_SUCCESS = "retry_success"
    RETRY_FAILURE = "retry_failure"
    ESCALATED = "escalated"
    ERROR = "error"


class ProcessingStatus(StrEnum):
    """Where a failure is in the background pipeline, so a stuck job can be requeued."""

    QUEUED = "queued"
    PROCESSING = "processing"
    DONE = "done"
    ERROR = "error"


class FailureSource(StrEnum):
    """Which entry point recorded the failure."""

    GITHUB = "github"
    N8N = "n8n"
