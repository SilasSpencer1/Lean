"""Inspect bounded model JSON data without granting bundle or execution authority."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
import hashlib

from ._input_parsing import _InvalidInput, _fail, _parse_decimal, _parse_string, _require_shape
from ._validation import _require_nonempty_string, _trusted_datetime
from .admission import _decode_json
from .bar_inputs import _identity_decimal, _UnsupportedIdentity


_CASH_FORMAT = "options_lab.cash_json.v1"
_FIXED_FORMAT = "options_lab.fixed_by_right_json.v1"
_MODEL_FIELDS = ("schema_version", "format_id")
_ROW_FIELDS = ("mean_attempt_return", "calibration_bucket")


@dataclass(frozen=True, init=False)
class ModelDataRow:
    """This class represents one inspected fixed-right mean and declared bucket."""

    right: str
    mean_attempt_return: Decimal
    calibration_bucket: str

    def __init__(self) -> None:
        """Block standalone row construction.

        :returns:          None.
        :raises TypeError: Always; use normalize_model_bytes.
        """
        raise TypeError("ModelDataRow values come from normalize_model_bytes")


@dataclass(frozen=True, init=False)
class ParsedModelData:
    """This class represents immutable inspected data and its exact original bytes."""

    model_kind: str
    format_id: str
    original_model_bytes: bytes
    model_hash: str
    rows: tuple[ModelDataRow, ...]

    def __init__(self) -> None:
        """Block caller-selected model data or hashes.

        :returns:          None.
        :raises TypeError: Always; use normalize_model_bytes.
        """
        raise TypeError("ParsedModelData values come from normalize_model_bytes")


@dataclass(frozen=True, init=False)
class BundleInputRejection:
    """This class represents a bounded inspection failure with actual receipt facts."""

    stage: str
    event_id: str
    raw_ref: str
    received_at: datetime
    field: str
    code: str

    def __init__(self) -> None:
        """Block caller-selected failure evidence.

        :returns:          None.
        :raises TypeError: Always; use model input normalization.
        """
        raise TypeError("BundleInputRejection values come from input normalization")


@dataclass(frozen=True, init=False)
class ModelDataValidation:
    """This class represents one inspected model or rejection and its receipt."""

    event_id: str
    raw_ref: str
    received_at: datetime
    value: ParsedModelData | None
    rejection: BundleInputRejection | None

    def __init__(self) -> None:
        """Block caller-selected inspection outcomes.

        :returns:          None.
        :raises TypeError: Always; use normalize_model_bytes.
        """
        raise TypeError("ModelDataValidation values come from normalize_model_bytes")


def _make(cls, **fields):
    """Construct private factory records from already inspected concrete fields."""
    value = object.__new__(cls)
    for name, item in fields.items():
        object.__setattr__(value, name, item)
    return value


def _shape(raw: object, path: str, fields: tuple[str, ...]) -> None:
    """Bound exact dictionary keys before the existing closed-shape inspection."""
    if type(raw) is not dict:
        _fail(path, "expected_exact_dict")
    if len(raw) > len(fields):
        _fail(path, "unknown_fields")
    if any(type(key) is not str or len(key) > max(map(len, fields)) for key in raw):
        _fail(path, "unknown_fields")
    _require_shape(raw, path, fields)


def _identifier(value: object, field: str) -> str:
    """Inspect one bounded exact nonempty string without surrogate code points."""
    parsed = _parse_string(value, field)
    if len(parsed) > 1024:
        _fail(field, "resource_limit")
    if any(0xD800 <= ord(char) <= 0xDFFF for char in parsed):
        _fail(field, "invalid_value")
    return parsed


def _decimal(value: object, field: str) -> Decimal:
    """Inspect bounded signed fixed-point data without ambient-context rounding."""
    if type(value) is str and len(value) > 1002:
        _fail(field, "resource_limit")
    try:
        parsed = _parse_decimal(value, field)
        _identity_decimal(parsed)
    except _UnsupportedIdentity:
        _fail(field, "invalid_decimal")
    except MemoryError:
        _fail(field, "resource_limit")
    return parsed


def normalize_model_bytes(
    model_bytes: object, *, event_id: str, raw_ref: str, received_at: datetime,
) -> ModelDataValidation:
    """Inspect exact cash or fixed-right JSON bytes and retain their original SHA.

    Means and bucket names are data; inspection grants no calibration support,
    artifact admission, runtime compatibility, prediction or readiness claim.

    :param model_bytes:  Untrusted exact bytes, bounded to 64 KiB before decoding.
    :param event_id:     Trusted nonempty inspection-event identifier.
    :param raw_ref:      Trusted nonempty receipt locator.
    :param received_at:  Trusted aware receipt timestamp, normalized to UTC.
    :returns:            Exclusive immutable data or a safe bounded rejection.
    :raises TypeError:   If a trusted receipt argument has the wrong exact type.
    :raises ValueError:  If trusted receipt identity or timestamp is invalid.
    """
    _require_nonempty_string("event_id", event_id)
    _require_nonempty_string("raw_ref", raw_ref)
    received_at = _trusted_datetime("received_at", received_at)
    receipt = dict(event_id=event_id, raw_ref=raw_ref, received_at=received_at)
    try:
        if type(model_bytes) is not bytes:
            _fail("model_bytes", "invalid_type")
        if len(model_bytes) > 65536:
            _fail("model_bytes", "resource_limit")
        raw, decode_code = _decode_json(model_bytes)
        if decode_code is not None:
            _fail("model_bytes", decode_code)
        if type(raw) is not dict:
            _fail("$", "expected_exact_dict")
        format_value = raw.get("format_id")
        cash = type(format_value) is str and format_value == _CASH_FORMAT
        _shape(raw, "$", _MODEL_FIELDS if cash else _MODEL_FIELDS + ("call", "put"))
        if type(raw["schema_version"]) is not int:
            _fail("schema_version", "invalid_type")
        if raw["schema_version"] != 1:
            _fail("schema_version", "invalid_value")
        format_id = _identifier(format_value, "format_id")
        if format_id not in (_CASH_FORMAT, _FIXED_FORMAT):
            _fail("format_id", "invalid_value")
        rows = []
        for right in (() if cash else ("call", "put")):
            row = raw[right]
            _shape(row, right, _ROW_FIELDS)
            rows.append(_make(ModelDataRow, right=right,
                mean_attempt_return=_decimal(row["mean_attempt_return"], f"{right}.mean_attempt_return"),
                calibration_bucket=_identifier(row["calibration_bucket"], f"{right}.calibration_bucket")))
        value = _make(ParsedModelData, model_kind="cash" if cash else "fixture_fixed_by_right",
                      format_id=format_id, original_model_bytes=model_bytes,
                      model_hash=hashlib.sha256(model_bytes).hexdigest(), rows=tuple(rows))
    except _InvalidInput as failure:
        field, code = failure.args
        rejection = _make(BundleInputRejection, stage="model_data", **receipt, field=field, code=code)
        return _make(ModelDataValidation, **receipt, value=None, rejection=rejection)
    return _make(ModelDataValidation, **receipt, value=value, rejection=None)
