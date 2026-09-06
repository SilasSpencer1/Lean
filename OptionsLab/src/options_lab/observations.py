"""Observation metadata normalization and time-suitability evidence."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

FeedClass = Literal["realtime", "indicative", "delayed", "unknown"]
Fidelity = Literal["genuine", "synthetic", "unknown"]
ObservationKind = Literal["quote", "interval"]
AvailabilityBasis = Literal["measured", "assumed"]

_FEED_CLASSES = ("realtime", "indicative", "delayed", "unknown")
_FIDELITIES = ("genuine", "synthetic", "unknown")
_KINDS = ("quote", "interval")
_AVAILABILITY_BASES = ("measured", "assumed")
_MAX_QUOTE_AGE = timedelta(seconds=5)
_REQUIRED_FIELDS = (
    "source",
    "provider_record_id",
    "feed_class",
    "fidelity",
    "kind",
    "event_at",
    "available_at",
    "availability_basis",
    "availability_evidence_ref",
    "is_fill_forward",
    "quality_flags",
)
_OPTIONAL_FIELDS = ("interval_start", "interval_end")
_STRING_FIELDS = ("source", "provider_record_id", "availability_evidence_ref")
_TOKEN_FIELDS = ("feed_class", "fidelity", "kind", "availability_basis")
_TIMESTAMP_FIELDS = ("event_at", "available_at", "interval_start", "interval_end")
_PARSE_FIELDS = _STRING_FIELDS + _TOKEN_FIELDS + _TIMESTAMP_FIELDS + ("is_fill_forward", "quality_flags")
_AVAILABILITY_REASONS = (
    "available_after_decision", "event_after_decision", "event_after_available",
    "interval_bounds_missing", "interval_order_invalid", "interval_after_decision",
    "interval_published_before_end",
)
_LIVE_REASONS = _AVAILABILITY_REASONS + (
    "not_quote", "fidelity_not_genuine", "feed_not_realtime",
    "availability_not_measured", "event_time_missing", "fill_forward",
    "quality_flags_present", "quote_too_old",
)


@dataclass(frozen=True)
class FieldDiagnostic:
    """This class represents a safe field-level normalization diagnostic."""

    field: str
    code: str

    def __post_init__(self) -> None:
        """
        Validate safe diagnostic fields.

        :returns:             None.
        :raises   TypeError:  If a field is not a string.
        :raises   ValueError: If a field is empty or incompatible with its code.
        """
        _require_nonempty_string("field", self.field)
        _require_nonempty_string("code", self.code)
        allowed: tuple[str, ...] = ()
        if self.field == "$":
            allowed = ("expected_exact_dict", "unknown_fields")
        elif self.field in _REQUIRED_FIELDS:
            allowed = ("missing",)
        if self.field in _STRING_FIELDS + _TOKEN_FIELDS:
            allowed += ("invalid_type", "invalid_value")
        elif self.field in _TIMESTAMP_FIELDS:
            allowed += ("invalid_timestamp",)
        elif self.field in ("is_fill_forward", "quality_flags"):
            allowed += ("invalid_type",)
        if self.code not in allowed:
            raise ValueError("diagnostic field and code are incompatible")


@dataclass(frozen=True)
class ObservationMeta:
    """This class represents normalized, self-reported observation metadata."""

    source: str
    provider_record_id: str
    raw_ref: str
    feed_class: FeedClass
    fidelity: Fidelity
    kind: ObservationKind
    event_at: datetime | None
    available_at: datetime
    received_at: datetime
    availability_basis: AvailabilityBasis
    availability_evidence_ref: str
    interval_start: datetime | None = None
    interval_end: datetime | None = None
    is_fill_forward: bool = False
    quality_flags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """
        Validate and normalize a metadata record.

        :returns:             None.
        :raises   TypeError:  If a field has the wrong exact scalar type.
        :raises   ValueError: If a string, vocabulary value, or timestamp is invalid.
        """
        for name in ("source", "provider_record_id", "raw_ref", "availability_evidence_ref"):
            _require_nonempty_string(name, getattr(self, name))
        _require_token("feed_class", self.feed_class, _FEED_CLASSES)
        _require_token("fidelity", self.fidelity, _FIDELITIES)
        _require_token("kind", self.kind, _KINDS)
        _require_token("availability_basis", self.availability_basis, _AVAILABILITY_BASES)
        for name in ("event_at", "interval_start", "interval_end"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _trusted_datetime(name, value))
        object.__setattr__(self, "available_at", _trusted_datetime("available_at", self.available_at))
        object.__setattr__(self, "received_at", _trusted_datetime("received_at", self.received_at))
        if type(self.is_fill_forward) is not bool:
            raise TypeError("is_fill_forward must be a boolean")
        if type(self.quality_flags) is not tuple or any(type(flag) is not str or not flag for flag in self.quality_flags):
            raise TypeError("quality_flags must be a tuple of non-empty strings")


@dataclass(frozen=True)
class InputRejection:
    """This class represents safe evidence that external metadata was malformed."""

    event_id: str
    received_at: datetime
    raw_ref: str
    stage: Literal["observation_normalization"]
    reasons: tuple[str, ...]
    diagnostics: tuple[FieldDiagnostic, ...]

    def __post_init__(self) -> None:
        """
        Validate rejection identity and immutable evidence.

        :returns:             None.
        :raises   TypeError:  If a field has the wrong type.
        :raises   ValueError: If a field is empty or outside its fixed vocabulary.
        """
        _require_nonempty_string("event_id", self.event_id)
        _require_nonempty_string("raw_ref", self.raw_ref)
        object.__setattr__(self, "received_at", _trusted_datetime("received_at", self.received_at))
        _require_token("stage", self.stage, ("observation_normalization",))
        _require_string_tuple("reasons", self.reasons)
        if type(self.diagnostics) is not tuple or any(type(item) is not FieldDiagnostic for item in self.diagnostics):
            raise TypeError("diagnostics must be a tuple of FieldDiagnostic values")
        _validate_diagnostics(self.diagnostics)
        expected_reasons = tuple(dict.fromkeys(item.code for item in self.diagnostics))
        if not self.reasons or self.reasons != expected_reasons:
            raise ValueError("reasons must match diagnostic codes in first-occurrence order")


@dataclass(frozen=True)
class ObservationValidation:
    """This class represents exactly one normalized value or input rejection."""

    value: ObservationMeta | None = None
    rejection: InputRejection | None = None

    def __post_init__(self) -> None:
        """
        Enforce result exclusivity.

        :returns:             None.
        :raises   TypeError:  If an outcome has the wrong concrete record type.
        :raises   ValueError: If neither or both outcomes are supplied.
        """
        if (self.value is None) == (self.rejection is None):
            raise ValueError("validation result must contain exactly one outcome")
        if self.value is not None and type(self.value) is not ObservationMeta:
            raise TypeError("validation result value must be an ObservationMeta")
        if self.rejection is not None and type(self.rejection) is not InputRejection:
            raise TypeError("validation result rejection must be an InputRejection")


@dataclass(frozen=True)
class ObservationAssessment:
    """This class represents observation availability and quote-time evidence."""

    meta: ObservationMeta
    availability_reasons: tuple[str, ...]
    live_quote_reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        """
        Validate immutable assessment evidence.

        :returns:            None.
        :raises   TypeError: If metadata or reason collections have the wrong type.
        :raises   ValueError: If reasons violate vocabulary, order, or prefix rules.
        """
        if type(self.meta) is not ObservationMeta:
            raise TypeError("meta must be an ObservationMeta")
        _require_string_tuple("availability_reasons", self.availability_reasons)
        _require_string_tuple("live_quote_reasons", self.live_quote_reasons)
        canonical_availability = tuple(reason for reason in _AVAILABILITY_REASONS if reason in self.availability_reasons)
        canonical_live = tuple(reason for reason in _LIVE_REASONS if reason in self.live_quote_reasons)
        live_availability = tuple(reason for reason in self.live_quote_reasons if reason in _AVAILABILITY_REASONS)
        if self.availability_reasons != canonical_availability or self.live_quote_reasons != canonical_live:
            raise ValueError("assessment reasons must be unique canonical vocabulary subsets")
        if live_availability != self.availability_reasons:
            raise ValueError("live_quote_reasons must include the exact availability reason prefix")

    @property
    def available(self) -> bool:
        """
        Return whether the observation was available at the decision time.

        :returns: True when no availability reason was found.
        """
        return not self.availability_reasons

    @property
    def live_quote_time_suitable(self) -> bool:
        """
        Return whether metadata can support a live quote freshness check.

        This evidence does not validate external claims or authorize entry.

        :returns: True when no availability or live quote reason was found.
        """
        return not self.availability_reasons and not self.live_quote_reasons


def normalize_observation_meta(
    raw: object,
    *,
    raw_ref: str,
    event_id: str,
    received_at: datetime,
) -> ObservationValidation:
    """
    Normalize one exact-dictionary JSON-style metadata record.

    Ordinary malformed external data produces a safe rejection. The trusted
    envelope identifies where that data was received and raises on misuse.

    :param    raw:         Exact dictionary containing external metadata.
    :param    raw_ref:     Trusted reference to the unmodified source record.
    :param    event_id:    Trusted identifier for this ingestion event.
    :param    received_at: Trusted current-ingestion timestamp.
    :returns:              Exactly one normalized value or input rejection.
    :raises   TypeError:   If a trusted envelope argument has the wrong type.
    :raises   ValueError:  If a trusted envelope string or timestamp is invalid.
    """
    _require_nonempty_string("raw_ref", raw_ref)
    _require_nonempty_string("event_id", event_id)
    received_at = _trusted_datetime("received_at", received_at)

    diagnostics: list[FieldDiagnostic] = []
    if type(raw) is not dict:
        diagnostics.append(FieldDiagnostic("$", "expected_exact_dict"))
    else:
        allowed = set(_REQUIRED_FIELDS + _OPTIONAL_FIELDS)
        exact_string_fields = {key for key in raw if type(key) is str}
        if len(exact_string_fields) != len(raw) or not exact_string_fields <= allowed:
            diagnostics.append(FieldDiagnostic("$", "unknown_fields"))
        for field in _REQUIRED_FIELDS:
            if field not in exact_string_fields:
                diagnostics.append(FieldDiagnostic(field, "missing"))
        if not diagnostics:
            raw = dict(raw)
            if type(raw["quality_flags"]) is list:
                raw["quality_flags"] = list(raw["quality_flags"])
            parsed = _parse_external_fields(raw, diagnostics)
            if not diagnostics:
                return ObservationValidation(
                    value=ObservationMeta(
                        raw_ref=raw_ref,
                        received_at=received_at,
                        **parsed,
                    )
                )

    reasons = tuple(dict.fromkeys(item.code for item in diagnostics))
    return ObservationValidation(
        rejection=InputRejection(
            event_id=event_id,
            received_at=received_at,
            raw_ref=raw_ref,
            stage="observation_normalization",
            reasons=reasons,
            diagnostics=tuple(diagnostics),
        )
    )


def assess_observation(
    meta: ObservationMeta,
    *,
    decision_at: datetime,
    max_quote_age: timedelta = _MAX_QUOTE_AGE,
) -> ObservationAssessment:
    """
    Assess declared point-in-time availability and live quote time suitability.

    The result retains input facts and is insufficient for entry authorization.

    :param    meta:          Validated observation metadata.
    :param    decision_at:   Decision timestamp used without reading a clock.
    :param    max_quote_age: Positive allowed quote age, no more than five seconds.
    :returns:                Immutable availability and quote-time evidence.
    :raises   TypeError:     If a trusted argument has the wrong type.
    :raises   ValueError:    If a trusted timestamp or quote-age limit is invalid.
    """
    if type(meta) is not ObservationMeta:
        raise TypeError("meta must be an ObservationMeta")
    decision_at = _trusted_datetime("decision_at", decision_at)
    if type(max_quote_age) is not timedelta:
        raise TypeError("max_quote_age must be a timedelta")
    if not timedelta(0) < max_quote_age <= _MAX_QUOTE_AGE:
        raise ValueError("max_quote_age must be positive and at most five seconds")

    availability: list[str] = []
    if meta.available_at > decision_at:
        availability.append("available_after_decision")
    if meta.event_at is not None:
        if meta.event_at > decision_at:
            availability.append("event_after_decision")
        if meta.event_at > meta.available_at:
            availability.append("event_after_available")
    if meta.kind == "interval":
        if meta.interval_start is None or meta.interval_end is None:
            availability.append("interval_bounds_missing")
        else:
            if meta.interval_start >= meta.interval_end:
                availability.append("interval_order_invalid")
            if meta.interval_end > decision_at:
                availability.append("interval_after_decision")
            if meta.available_at < meta.interval_end:
                availability.append("interval_published_before_end")

    live = list(availability)
    checks = (
        (meta.kind != "quote", "not_quote"),
        (meta.fidelity != "genuine", "fidelity_not_genuine"),
        (meta.feed_class != "realtime", "feed_not_realtime"),
        (meta.availability_basis != "measured", "availability_not_measured"),
        (meta.event_at is None, "event_time_missing"),
        (meta.is_fill_forward, "fill_forward"),
        (bool(meta.quality_flags), "quality_flags_present"),
    )
    live.extend(reason for failed, reason in checks if failed)
    if meta.event_at is not None and decision_at - meta.event_at > max_quote_age:
        live.append("quote_too_old")

    return ObservationAssessment(meta, tuple(availability), tuple(live))


def _parse_external_fields(
    raw: dict[object, object], diagnostics: list[FieldDiagnostic]
) -> dict[str, object]:
    """Parse external fields while collecting only safe diagnostics."""
    parsed: dict[str, object] = {}
    for field in _STRING_FIELDS:
        value = raw[field]
        if type(value) is not str:
            diagnostics.append(FieldDiagnostic(field, "invalid_type"))
        elif not value:
            diagnostics.append(FieldDiagnostic(field, "invalid_value"))
        else:
            parsed[field] = value
    for field, allowed in (
        ("feed_class", _FEED_CLASSES),
        ("fidelity", _FIDELITIES),
        ("kind", _KINDS),
        ("availability_basis", _AVAILABILITY_BASES),
    ):
        value = raw[field]
        if type(value) is not str:
            diagnostics.append(FieldDiagnostic(field, "invalid_type"))
        elif value not in allowed:
            diagnostics.append(FieldDiagnostic(field, "invalid_value"))
        else:
            parsed[field] = value
    for field in _TIMESTAMP_FIELDS:
        value = raw.get(field)
        if value is None and field != "available_at":
            parsed[field] = None
            continue
        try:
            parsed[field] = _external_datetime(value)
        except (TypeError, ValueError):
            diagnostics.append(FieldDiagnostic(field, "invalid_timestamp"))
    fill_forward = raw["is_fill_forward"]
    if type(fill_forward) is not bool:
        diagnostics.append(FieldDiagnostic("is_fill_forward", "invalid_type"))
    else:
        parsed["is_fill_forward"] = fill_forward
    flags = raw["quality_flags"]
    if type(flags) is not list or any(type(flag) is not str or not flag for flag in flags):
        diagnostics.append(FieldDiagnostic("quality_flags", "invalid_type"))
    else:
        parsed["quality_flags"] = tuple(flags)
    return parsed


def _external_datetime(value: object) -> datetime:
    """Parse an aware datetime object or ISO string and normalize it to UTC."""
    if type(value) is str:
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if type(value) is not datetime:
        raise TypeError("timestamp must be a datetime or ISO string")
    return _trusted_datetime("timestamp", value)


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


def _require_string_tuple(name: str, value: object) -> None:
    """Validate an immutable collection of non-empty exact strings."""
    if type(value) is not tuple or any(type(item) is not str or not item for item in value):
        raise TypeError(f"{name} must be a tuple of non-empty strings")


def _validate_diagnostics(diagnostics: tuple[FieldDiagnostic, ...]) -> None:
    """Require one valid normalization phase in its canonical emission order."""
    if not diagnostics:
        raise ValueError("diagnostics must not be empty")
    if diagnostics[0].code == "expected_exact_dict":
        if diagnostics != (FieldDiagnostic("$", "expected_exact_dict"),):
            raise ValueError("expected_exact_dict must be the only diagnostic")
        return
    schema_phase = any(item.code in ("unknown_fields", "missing") for item in diagnostics)
    if schema_phase:
        expected = (() if FieldDiagnostic("$", "unknown_fields") not in diagnostics else (FieldDiagnostic("$", "unknown_fields"),))
        expected += tuple(FieldDiagnostic(field, "missing") for field in _REQUIRED_FIELDS if FieldDiagnostic(field, "missing") in diagnostics)
    else:
        if len({item.field for item in diagnostics}) != len(diagnostics):
            raise ValueError("parsed diagnostics must not repeat fields")
        expected = tuple(item for field in _PARSE_FIELDS for item in diagnostics if item.field == field)
    if diagnostics != expected:
        raise ValueError("diagnostics must follow one canonical normalization phase")
