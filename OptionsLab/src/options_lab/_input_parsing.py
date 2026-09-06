"""Private concrete parsing primitives for strict JSON-style inputs."""

from datetime import date, datetime
from decimal import Decimal, localcontext
import re
from typing import Literal, Never

from ._validation import _trusted_datetime

RejectionCode = Literal[
    "expected_exact_dict", "unknown_fields", "missing", "invalid_type",
    "invalid_value", "invalid_date", "invalid_timestamp", "invalid_decimal",
]

_MONEY_PATTERN = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)")


class _InvalidInput(Exception):
    """This class represents private first-failure parser control flow."""


def _require_shape(raw: object, path: str, fields: tuple[str, ...]) -> None:
    """Require one exact dictionary with only and all declared string keys."""
    if type(raw) is not dict:
        _fail(path, "expected_exact_dict")
    keys = tuple(raw)
    if any(type(key) is not str for key in keys):
        _fail(path, "unknown_fields")
    if any(key not in fields for key in keys):
        _fail(path, "unknown_fields")
    for field in fields:
        if field not in raw:
            _fail(field if path == "$" else f"{path}.{field}", "missing")


def _parse_string(value: object, field: str) -> str:
    """Parse one exact nonempty external string."""
    if type(value) is not str:
        _fail(field, "invalid_type")
    if not value:
        _fail(field, "invalid_value")
    return value


def _parse_token(value: object, field: str, allowed: tuple[str, ...]) -> str:
    """Parse one exact string from a fixed local vocabulary."""
    parsed = _parse_string(value, field)
    if parsed not in allowed:
        _fail(field, "invalid_value")
    return parsed


def _parse_date(value: object, field: str) -> date:
    """Parse one exact ASCII calendar date string."""
    if type(value) is not str:
        _fail(field, "invalid_type")
    if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) is None:
        _fail(field, "invalid_date")
    try:
        return date.fromisoformat(value)
    except ValueError:
        _fail(field, "invalid_date")


def _parse_decimal(value: object, field: str) -> Decimal:
    """Parse one exact ASCII fixed-point string without changing caller context."""
    if type(value) is not str:
        _fail(field, "invalid_type")
    if _MONEY_PATTERN.fullmatch(value) is None:
        _fail(field, "invalid_decimal")
    with localcontext():
        parsed = Decimal(value)
    if not parsed.is_finite():
        _fail(field, "invalid_decimal")
    return parsed


def _parse_timestamp(value: object, field: str, *, nullable: bool = False) -> datetime | None:
    """Parse one exact aware ISO datetime string and normalize it to UTC."""
    if value is None and nullable:
        return None
    if type(value) is not str:
        _fail(field, "invalid_type")
    try:
        return _trusted_datetime("timestamp", datetime.fromisoformat(value))
    except ValueError:
        _fail(field, "invalid_timestamp")


def _fail(field: str, code: RejectionCode) -> Never:
    """Stop parsing at one safe field and code."""
    raise _InvalidInput(field, code)
