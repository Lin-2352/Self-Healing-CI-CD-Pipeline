"""Column types that behave the same on SQLite and PostgreSQL."""

from datetime import UTC, datetime

from sqlalchemy import DateTime
from sqlalchemy.types import TypeDecorator


def utcnow() -> datetime:
    """Return the current time as a timezone-aware UTC datetime."""
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator):
    """A datetime column that always comes back timezone-aware and in UTC.

    SQLite has no timezone type: it stores the timestamp and drops the offset. This decorator
    writes UTC and reattaches the UTC offset on read, so application code never sees a naive
    datetime. PostgreSQL already returns aware values; those are normalised to UTC as well.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: object) -> datetime | None:
        """Normalise a value to UTC before it is stored."""
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: object) -> datetime | None:
        """Return a stored value as a timezone-aware UTC datetime."""
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
