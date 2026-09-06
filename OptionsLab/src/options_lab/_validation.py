"""Private scalar validation shared by observation and reference records."""

from datetime import datetime, timezone


def _trusted_datetime(name: str, value: object) -> datetime:
    """Validate a trusted aware datetime and normalize it to UTC."""
    if type(value) is not datetime:
        raise TypeError(f"{name} must be a datetime")
    if value.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware")
    try:
        offset = value.utcoffset()
    except Exception:
        raise ValueError(f"{name} timezone evaluation failed") from None
    if offset is None:
        raise ValueError(f"{name} must be timezone-aware")
    try:
        return value.astimezone(timezone.utc)
    except OverflowError:
        raise ValueError(f"{name} cannot be represented in UTC") from None
    except Exception:
        raise ValueError(f"{name} timezone conversion failed") from None


def _require_nonempty_string(name: str, value: object) -> None:
    """Validate an exact, non-empty trusted string."""
    if type(value) is not str:
        raise TypeError(f"{name} must be a string")
    if not value:
        raise ValueError(f"{name} must not be empty")


def _require_token(name: str, value: object, allowed: tuple[str, ...]) -> None:
    """Validate an exact string from one fixed vocabulary."""
    if type(value) is not str:
        raise TypeError(f"{name} must be a string")
    if value not in allowed:
        raise ValueError(f"{name} is not supported")
