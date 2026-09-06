"""Normalize partial decision-context requests without discarding usable fields."""

from dataclasses import dataclass
from datetime import datetime
import re
from typing import Literal

from ._input_parsing import RejectionCode, _InvalidInput, _parse_string, _parse_timestamp
from ._validation import _require_nonempty_string, _trusted_datetime


_ROOT_FIELDS = ("decision_id", "decision_at", "member_record_ids")


@dataclass(frozen=True)
class ContextRequest:
    """This class represents an exact decision identity and UTC decision instant."""

    decision_id: str
    decision_at: datetime

    def __post_init__(self) -> None:
        """
        Validate the exact header and normalize its aware decision time.

        :returns:             None.
        :raises   TypeError:  If the header or either scalar has a wrong exact type.
        :raises   ValueError: If the identity is empty or the time is invalid.
        """
        if type(self) is not ContextRequest:
            raise TypeError("header must be an exact ContextRequest")
        _require_nonempty_string("decision_id", self.decision_id)
        object.__setattr__(self, "decision_at", _trusted_datetime("decision_at", self.decision_at))


@dataclass(frozen=True)
class ContextInputRejection:
    """This class represents one safe request failure with its trusted envelope."""

    event_id: str
    received_at: datetime
    raw_ref: str
    field: str
    code: RejectionCode

    def __post_init__(self) -> None:
        """
        Validate exact trusted facts and a closed request field/code pair.

        :returns:             None.
        :raises   TypeError:  If the rejection or any scalar has a wrong exact type.
        :raises   ValueError: If identity, time, or the diagnostic pair is invalid.
        """
        if type(self) is not ContextInputRejection:
            raise TypeError("rejection must be an exact ContextInputRejection")
        _require_nonempty_string("event_id", self.event_id)
        _require_nonempty_string("raw_ref", self.raw_ref)
        object.__setattr__(self, "received_at", _trusted_datetime("received_at", self.received_at))
        _require_nonempty_string("field", self.field)
        _require_nonempty_string("code", self.code)
        if self.code not in _allowed_codes(self.field):
            raise ValueError("rejection field and code are incompatible")

    @property
    def stage(self) -> Literal["context_request_normalization"]:
        """Return the fixed request normalization stage.

        :returns: The context request normalization stage.
        """
        return "context_request_normalization"

    @property
    def reasons(self) -> tuple[RejectionCode]:
        """Return the single safe code as immutable evidence.

        :returns: A one-item tuple containing the rejection code.
        """
        return (self.code,)


@dataclass(frozen=True, init=False)
class ContextRequestValidation:
    """This class represents derived partial request data and all parse failures."""

    value: ContextRequest | None
    member_record_ids: tuple[str, ...]
    event_id: str
    raw_ref: str
    received_at: datetime
    rejections: tuple[ContextInputRejection, ...]

    def __init__(self) -> None:
        """
        Block standalone construction of contradictory partial request evidence.

        :returns:             None.
        :raises   TypeError:  Always; values come only from request normalization.
        """
        raise TypeError("ContextRequestValidation values come from normalize_context_request")


def normalize_context_request(
    raw: object, *, event_id: str, raw_ref: str, received_at: datetime,
) -> ContextRequestValidation:
    """
    Parse an exact request dictionary and retain every independently safe field.

    All three root keys are required. A valid identity and aware ISO decision
    timestamp produce a header even when member or root-shape errors exist.
    Exact nonempty string member IDs survive independently in first-occurrence
    order. Missing fields precede present-field failures; invalid list entries
    follow by numeric index. Receipt time never substitutes for decision time.

    :param    raw:          Untrusted request with decision_id, decision_at, and member_record_ids.
    :param    event_id:     Trusted exact nonempty ingestion event identifier.
    :param    raw_ref:      Trusted exact nonempty raw request locator.
    :param    received_at:  Trusted exact aware ingestion receipt datetime.
    :returns:               Frozen partial header, unique safe IDs, envelope, and rejections.
    :raises   TypeError:    If a trusted argument has a wrong exact type.
    :raises   ValueError:   If a trusted identity or receipt time is invalid.
    """
    _require_nonempty_string("event_id", event_id)
    _require_nonempty_string("raw_ref", raw_ref)
    received_at = _trusted_datetime("received_at", received_at)

    root: dict[str, object] = {}
    errors: list[tuple[str, RejectionCode]] = []
    if type(raw) is not dict:
        errors.append(("$", "expected_exact_dict"))
    else:
        unknown_keys = False
        # Lookup in the original dict could invoke a hostile colliding key.
        for key, value in raw.items():
            if type(key) is str and key in _ROOT_FIELDS:
                root[key] = value
            else:
                unknown_keys = True
        if unknown_keys:
            errors.append(("$", "unknown_fields"))
        for name in _ROOT_FIELDS:
            if name not in root:
                errors.append((name, "missing"))

    decision_id = None
    decision_at = None
    if "decision_id" in root:
        try:
            decision_id = _parse_string(root["decision_id"], "decision_id")
        except _InvalidInput as failure:
            errors.append(failure.args)
    if "decision_at" in root:
        try:
            decision_at = _parse_timestamp(root["decision_at"], "decision_at")
        except _InvalidInput as failure:
            errors.append(failure.args)

    member_ids: list[str] = []
    seen: set[str] = set()
    if "member_record_ids" in root:
        members = root["member_record_ids"]
        if type(members) is not list:
            errors.append(("member_record_ids", "invalid_type"))
        else:
            for index, item in enumerate(members):
                try:
                    member_id = _parse_string(item, f"member_record_ids[{index}]")
                except _InvalidInput as failure:
                    errors.append(failure.args)
                    continue
                if member_id not in seen:
                    seen.add(member_id)
                    member_ids.append(member_id)

    header = None
    if decision_id is not None and decision_at is not None:
        header = ContextRequest(decision_id, decision_at)
    result = object.__new__(ContextRequestValidation)
    object.__setattr__(result, "value", header)
    object.__setattr__(result, "member_record_ids", tuple(member_ids))
    object.__setattr__(result, "event_id", event_id)
    object.__setattr__(result, "raw_ref", raw_ref)
    object.__setattr__(result, "received_at", received_at)
    object.__setattr__(result, "rejections", tuple(
        ContextInputRejection(event_id, received_at, raw_ref, name, code)
        for name, code in errors
    ))
    return result


def _allowed_codes(field: str) -> tuple[RejectionCode, ...]:
    """
    Return only codes emitted for the exact field or canonical member index.

    :param    field:  Exact string diagnostic path.
    :returns:         Closed compatible code tuple, or empty for an invalid path.
    """
    if field == "$":
        return ("expected_exact_dict", "unknown_fields")
    if field == "decision_id":
        return ("missing", "invalid_type", "invalid_value")
    if field == "decision_at":
        return ("missing", "invalid_type", "invalid_timestamp")
    if field == "member_record_ids":
        return ("missing", "invalid_type")
    if re.fullmatch(r"member_record_ids\[(?:0|[1-9][0-9]*)\]", field):
        return ("invalid_type", "invalid_value")
    return ()
