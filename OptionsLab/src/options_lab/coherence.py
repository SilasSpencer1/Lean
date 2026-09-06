"""Normalize and assess actual fixture-bound quote coherence evidence."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import re
from typing import Literal

from ._input_parsing import (
    RejectionCode,
    _InvalidInput,
    _parse_contract_id,
    _parse_string,
    _parse_timestamp,
    _parse_token,
    _require_shape,
)
from ._validation import _require_nonempty_string, _require_token, _trusted_datetime
from .admission import (
    _COHERENCE_PROTOCOLS,
    VerifiedFixtureManifest,
    VerifiedFixtureMember,
)
from .contracts import ContractId
from .observations import normalize_observation_meta
from .quote_content import QuoteContentIdentity, identify_quote_content
from .quote_inputs import normalize_quote_observation
from .quotes import QuoteObservation, _assess_quote_only
from .underlying import UnderlyingQuote, assess_underlying_quote
from .underlying_inputs import normalize_underlying_quote


SideRole = Literal[
    "option_bid", "option_ask", "underlying_bid", "underlying_ask",
]
CoherenceMethod = Literal["joint_snapshot", "side_validity_overlap"]

_ROLES = ("option_bid", "option_ask", "underlying_bid", "underlying_ask")
_METHODS = ("joint_snapshot", "side_validity_overlap")
_HASH_DIGITS = frozenset("0123456789abcdef")
_ROOT_FIELDS = (
    "evidence_id", "source", "protocol_id", "contract", "option_quote_hash",
    "underlying_quote_hash", "method", "coherent_at", "available_at",
    "snapshot_id", "sides",
)
_SIDE_FIELDS = (
    "role", "quote_content_hash", "valid_from", "invalidated_at",
    "observed_through", "evidence_record_id",
)


@dataclass(frozen=True)
class SideValidity:
    """This class represents retained validity facts for one quoted book side."""

    role: SideRole
    quote_content_hash: str
    valid_from: datetime | None
    invalidated_at: datetime | None
    observed_through: datetime | None
    evidence_record_id: str

    def __post_init__(self) -> None:
        """
        Validate exact side identity and normalize supplied times to UTC.

        Missing and contradictory interval facts remain representable so the
        assessment can retain and explain them.

        :returns:             None.
        :raises TypeError:    If a retained field has the wrong exact type.
        :raises ValueError:   If an identity or timestamp is malformed.
        """
        _require_token("role", self.role, _ROLES)
        _trusted_hash("quote_content_hash", self.quote_content_hash)
        for name in ("valid_from", "invalidated_at", "observed_through"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _trusted_datetime(name, value))
        _require_nonempty_string("evidence_record_id", self.evidence_record_id)


@dataclass(frozen=True)
class QuoteCoherenceEvidence:
    """This class represents one retained point or interval coherence claim."""

    evidence_id: str
    source: str
    protocol_id: str
    raw_ref: str
    contract: ContractId
    option_quote_hash: str
    underlying_quote_hash: str
    method: CoherenceMethod
    coherent_at: datetime | None
    available_at: datetime | None
    snapshot_id: str | None
    sides: tuple[SideValidity, ...]

    def __post_init__(self) -> None:
        """
        Validate the exact retained evidence shape and normalize times to UTC.

        Semantic defects such as missing instants, wrong proof shape, duplicate
        roles, and reversed intervals remain available to the assessor.

        :returns:             None.
        :raises TypeError:    If a retained field has the wrong exact type.
        :raises ValueError:   If a non-null identity or timestamp is malformed.
        """
        for name in ("evidence_id", "source", "protocol_id", "raw_ref"):
            _require_nonempty_string(name, getattr(self, name))
        if type(self.contract) is not ContractId:
            raise TypeError("contract must be a ContractId")
        _trusted_hash("option_quote_hash", self.option_quote_hash)
        _trusted_hash("underlying_quote_hash", self.underlying_quote_hash)
        _require_token("method", self.method, _METHODS)
        for name in ("coherent_at", "available_at"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _trusted_datetime(name, value))
        if self.snapshot_id is not None:
            _require_nonempty_string("snapshot_id", self.snapshot_id)
        if type(self.sides) is not tuple or any(
            type(side) is not SideValidity for side in self.sides
        ):
            raise TypeError("sides must be a tuple of SideValidity values")


@dataclass(frozen=True)
class CoherenceInputRejection:
    """This class represents one safe quote-coherence normalization failure."""

    event_id: str
    received_at: datetime
    raw_ref: str
    field: str
    code: RejectionCode

    def __post_init__(self) -> None:
        """
        Validate trusted rejection identity and the closed diagnostic pair.

        :returns:             None.
        :raises TypeError:    If identity, time, field, or code has a wrong type.
        :raises ValueError:   If identity is empty or field/code is unsupported.
        """
        for name in ("event_id", "raw_ref", "field"):
            _require_nonempty_string(name, getattr(self, name))
        object.__setattr__(
            self, "received_at", _trusted_datetime("received_at", self.received_at)
        )
        if type(self.code) is not str:
            raise TypeError("code must be a string")
        if self.code not in _allowed_codes(self.field):
            raise ValueError("rejection field and code are incompatible")

    @property
    def stage(self) -> Literal["quote_coherence_normalization"]:
        """
        Return the fixed quote-coherence normalization stage.

        :returns: The quote-coherence normalization stage.
        """
        return "quote_coherence_normalization"

    @property
    def reasons(self) -> tuple[RejectionCode]:
        """
        Return the one bounded normalization code.

        :returns: A one-item tuple containing the rejection code.
        """
        return (self.code,)


@dataclass(frozen=True)
class CoherenceValidation:
    """This class represents exactly one normalized coherence value or rejection."""

    value: QuoteCoherenceEvidence | None = None
    rejection: CoherenceInputRejection | None = None

    def __post_init__(self) -> None:
        """
        Enforce one exact concrete normalization outcome.

        :returns:             None.
        :raises TypeError:    If an outcome has the wrong exact record type.
        :raises ValueError:   If neither or both outcomes are supplied.
        """
        if (self.value is None) == (self.rejection is None):
            raise ValueError("validation result must contain exactly one outcome")
        if self.value is not None and type(self.value) is not QuoteCoherenceEvidence:
            raise TypeError("validation value must be QuoteCoherenceEvidence")
        if self.rejection is not None and type(self.rejection) is not CoherenceInputRejection:
            raise TypeError("validation rejection must be CoherenceInputRejection")


@dataclass(frozen=True)
class CoherenceAssessment:
    """This class represents derived same-root quote coherence evidence."""

    evidence: QuoteCoherenceEvidence | None
    option_quote: QuoteObservation
    underlying_quote: UnderlyingQuote
    manifest: VerifiedFixtureManifest
    decision_at: datetime
    max_quote_age: timedelta
    reasons: tuple[str, ...] = field(init=False)
    coherent_at: datetime | None = field(init=False)
    proof_basis: CoherenceMethod | None = field(init=False)
    option_identity: QuoteContentIdentity = field(init=False)
    underlying_identity: QuoteContentIdentity = field(init=False)
    matched_members: tuple[VerifiedFixtureMember, ...] = field(init=False)

    def __post_init__(self) -> None:
        """
        Validate retained owner inputs and derive coherence without caller flags.

        :returns:             None.
        :raises TypeError:    If a retained owner input has a wrong exact type.
        :raises ValueError:   If the decision time or quote-age cap is invalid.
        """
        if self.evidence is not None and type(self.evidence) is not QuoteCoherenceEvidence:
            raise TypeError("evidence must be QuoteCoherenceEvidence or None")
        if type(self.option_quote) is not QuoteObservation:
            raise TypeError("option_quote must be a QuoteObservation")
        if type(self.underlying_quote) is not UnderlyingQuote:
            raise TypeError("underlying_quote must be an UnderlyingQuote")
        if type(self.manifest) is not VerifiedFixtureManifest:
            raise TypeError("manifest must be a VerifiedFixtureManifest")
        decision_at = _trusted_datetime("decision_at", self.decision_at)
        object.__setattr__(self, "decision_at", decision_at)
        if type(self.max_quote_age) is not timedelta:
            raise TypeError("max_quote_age must be a timedelta")
        if not timedelta(0) < self.max_quote_age <= timedelta(seconds=5):
            raise ValueError("max_quote_age must be positive and at most five seconds")

        option_identity = identify_quote_content(self.option_quote)
        underlying_identity = identify_quote_content(self.underlying_quote)
        reasons, members = _assessment_evidence(
            self.evidence, self.option_quote, self.underlying_quote, self.manifest,
            decision_at, self.max_quote_age, option_identity, underlying_identity,
        )
        coherent = not reasons and self.evidence is not None
        object.__setattr__(self, "option_identity", option_identity)
        object.__setattr__(self, "underlying_identity", underlying_identity)
        object.__setattr__(self, "reasons", reasons)
        object.__setattr__(self, "matched_members", members)
        object.__setattr__(
            self, "coherent_at", self.evidence.coherent_at if coherent else None
        )
        object.__setattr__(
            self, "proof_basis", self.evidence.method if coherent else None
        )

    @property
    def coherent(self) -> bool:
        """
        Return whether all actual same-root evidence checks passed.

        :returns: True only for a complete registered point or overlap proof.
        """
        return not self.reasons

    def supports_instant(self, at: datetime) -> bool:
        """
        Recompute whether the retained proof supports one explicit instant.

        :param    at:          Trusted aware instant to test against actual proof.
        :returns:              True only when the successful proof covers ``at``.
        :raises TypeError:     If ``at`` is not an exact datetime.
        :raises ValueError:    If ``at`` is naive or cannot normalize to UTC.
        """
        at = _trusted_datetime("at", at)
        if not self.coherent or at > self.decision_at or self.evidence is None:
            return False
        if self.evidence.method == "joint_snapshot":
            return at == self.coherent_at
        return _sides_support(self.evidence.sides, at)


def assess_quote_coherence(
    evidence: QuoteCoherenceEvidence | None,
    option_quote: QuoteObservation,
    underlying_quote: UnderlyingQuote,
    *,
    manifest: VerifiedFixtureManifest,
    decision_at: datetime,
    max_quote_age: timedelta,
) -> CoherenceAssessment:
    """
    Assess current quotes against actual evidence in one verified fixture root.

    :param    evidence:          Typed coherence evidence or explicit absence.
    :param    option_quote:      Current typed option quote.
    :param    underlying_quote:  Current typed underlying quote.
    :param    manifest:          Actual byte-verified fixture manifest.
    :param    decision_at:       Trusted aware decision instant.
    :param    max_quote_age:     Positive quote-age cap of at most five seconds.
    :returns:                    Immutable recomputed coherence evidence.
    :raises TypeError:           If a trusted input has a wrong exact type.
    :raises ValueError:          If the decision time or age cap is invalid.
    """
    return CoherenceAssessment(
        evidence, option_quote, underlying_quote, manifest, decision_at,
        max_quote_age,
    )


def normalize_quote_coherence(
    raw: object,
    *,
    event_id: str,
    raw_ref: str,
    received_at: datetime,
) -> CoherenceValidation:
    """
    Normalize one exact JSON-style quote-coherence body.

    Required nullable keys remain explicit. Structural and scalar parsing follows
    declaration order, and ordinary malformed source input becomes bounded evidence.

    :param    raw:          Untrusted exact coherence body dictionary.
    :param    event_id:     Trusted nonempty ingestion event identifier.
    :param    raw_ref:      Trusted nonempty locator for the source record.
    :param    received_at:  Trusted aware ingestion timestamp.
    :returns:               Exactly one normalized value or safe rejection.
    :raises TypeError:      If a trusted argument has the wrong exact type.
    :raises ValueError:     If trusted identity or time evidence is invalid.
    """
    _require_nonempty_string("event_id", event_id)
    _require_nonempty_string("raw_ref", raw_ref)
    received_at = _trusted_datetime("received_at", received_at)
    try:
        _require_shape(raw, "$", _ROOT_FIELDS)
        root = dict(raw)
        evidence_id = _parse_string(root["evidence_id"], "evidence_id")
        source = _parse_string(root["source"], "source")
        protocol_id = _parse_string(root["protocol_id"], "protocol_id")
        contract = _parse_contract_id(root["contract"])
        option_hash = _parse_hash(root["option_quote_hash"], "option_quote_hash")
        underlying_hash = _parse_hash(
            root["underlying_quote_hash"], "underlying_quote_hash"
        )
        method = _parse_token(root["method"], "method", _METHODS)
        coherent_at = _parse_timestamp(
            root["coherent_at"], "coherent_at", nullable=True
        )
        available_at = _parse_timestamp(
            root["available_at"], "available_at", nullable=True
        )
        snapshot_id = (
            None if root["snapshot_id"] is None
            else _parse_string(root["snapshot_id"], "snapshot_id")
        )
        sides = _parse_sides(root["sides"])
        value = QuoteCoherenceEvidence(
            evidence_id, source, protocol_id, raw_ref, contract, option_hash,
            underlying_hash, method, coherent_at, available_at, snapshot_id, sides,
        )
    except _InvalidInput as failure:
        field_name, code = failure.args
        return CoherenceValidation(rejection=CoherenceInputRejection(
            event_id, received_at, raw_ref, field_name, code
        ))
    return CoherenceValidation(value=value)


def _parse_sides(raw: object) -> tuple[SideValidity, ...]:
    """Parse one exact list while preserving declaration order and adverse facts."""
    if type(raw) is not list:
        raise _InvalidInput("sides", "invalid_type")
    sides: list[SideValidity] = []
    for index, item in enumerate(raw):
        path = f"sides[{index}]"
        _require_shape(item, path, _SIDE_FIELDS)
        side = dict(item)
        sides.append(SideValidity(
            _parse_token(side["role"], f"{path}.role", _ROLES),
            _parse_hash(
                side["quote_content_hash"], f"{path}.quote_content_hash"
            ),
            _parse_timestamp(side["valid_from"], f"{path}.valid_from", nullable=True),
            _parse_timestamp(
                side["invalidated_at"], f"{path}.invalidated_at", nullable=True
            ),
            _parse_timestamp(
                side["observed_through"], f"{path}.observed_through", nullable=True
            ),
            _parse_string(side["evidence_record_id"], f"{path}.evidence_record_id"),
        ))
    return tuple(sides)


def _parse_hash(value: object, field_name: str) -> str:
    """Parse one canonical lowercase SHA-256 string from raw input."""
    value = _parse_string(value, field_name)
    if len(value) != 64 or any(character not in _HASH_DIGITS for character in value):
        raise _InvalidInput(field_name, "invalid_value")
    return value


def _trusted_hash(name: str, value: object) -> None:
    """Validate one trusted canonical lowercase SHA-256 string."""
    if type(value) is not str:
        raise TypeError(f"{name} must be a string")
    if len(value) != 64 or any(character not in _HASH_DIGITS for character in value):
        raise ValueError(f"{name} must be a canonical SHA-256 string")


def _allowed_codes(field_name: str) -> tuple[str, ...]:
    """Return the fixed rejection codes allowed for one coherence schema path."""
    if field_name == "$":
        return ("expected_exact_dict", "unknown_fields")
    if field_name in ("contract",):
        return ("missing", "expected_exact_dict", "unknown_fields")
    if field_name == "sides":
        return ("missing", "invalid_type")
    if re.fullmatch(r"sides\[[0-9]+\]", field_name):
        return ("expected_exact_dict", "unknown_fields")
    if field_name in ("coherent_at", "available_at") or re.fullmatch(
        r"sides\[[0-9]+\]\.(?:valid_from|invalidated_at|observed_through)",
        field_name,
    ):
        return ("missing", "invalid_type", "invalid_timestamp")
    if field_name == "contract.expiry":
        return ("missing", "invalid_type", "invalid_date")
    if field_name == "contract.strike":
        return ("missing", "invalid_type", "invalid_decimal")
    if field_name == "contract.multiplier":
        return ("missing", "invalid_type")
    if re.fullmatch(
        r"(?:evidence_id|source|protocol_id|option_quote_hash|"
        r"underlying_quote_hash|method|snapshot_id|contract\.(?:underlying|right|"
        r"deliverable_id)|sides\[[0-9]+\]\.(?:role|quote_content_hash|"
        r"evidence_record_id))",
        field_name,
    ):
        return ("missing", "invalid_type", "invalid_value")
    return ()


def _assessment_evidence(
    evidence: QuoteCoherenceEvidence | None,
    option: QuoteObservation,
    underlying: UnderlyingQuote,
    manifest: VerifiedFixtureManifest,
    decision_at: datetime,
    max_quote_age: timedelta,
    option_identity: QuoteContentIdentity,
    underlying_identity: QuoteContentIdentity,
) -> tuple[tuple[str, ...], tuple[VerifiedFixtureMember, ...]]:
    """Derive ordered binding, owner, protocol, and temporal evidence."""
    reasons: list[str] = []
    option_matches = _matching_quote_members(manifest, "option_quote", option)
    underlying_matches = _matching_quote_members(
        manifest, "underlying_quote", underlying
    )
    evidence_matches = (
        () if evidence is None else _matching_coherence_members(manifest, evidence)
    )
    for name, matches in (
        ("option", option_matches), ("underlying", underlying_matches),
        ("evidence", evidence_matches),
    ):
        if name == "evidence" and evidence is None:
            reasons.append("coherence_evidence_missing")
        elif not matches:
            reasons.append(f"{name}_member_missing")
        elif len(matches) > 1:
            reasons.append(f"{name}_member_ambiguous")

    members = tuple(
        matches[0]
        for matches in (option_matches, underlying_matches, evidence_matches)
        if len(matches) == 1
    )
    profiles = {
        profile["profile_id"]: profile
        for profile in manifest.decode_modeled_source_profiles()
    }
    for name, matches, record in (
        ("option", option_matches, option),
        ("underlying", underlying_matches, underlying),
        ("evidence", evidence_matches, evidence),
    ):
        if len(matches) == 1 and not _profile_matches(
            profiles.get(matches[0].profile_id), name, record
        ):
            reasons.append(f"{name}_profile_mismatch")

    reasons.extend(f"option_identity_{reason}" for reason in option_identity.reasons)
    reasons.extend(
        f"underlying_identity_{reason}" for reason in underlying_identity.reasons
    )
    option_observation, option_reasons = _assess_quote_only(
        option, decision_at=decision_at, max_quote_age=max_quote_age
    )
    underlying_assessment = assess_underlying_quote(
        underlying, decision_at=decision_at, max_quote_age=max_quote_age
    )
    reasons.extend(
        f"option_{reason}" for reason in option_observation.live_quote_reasons
    )
    reasons.extend(f"option_{reason}" for reason in option_reasons)
    reasons.extend(
        f"underlying_{reason}"
        for reason in underlying_assessment.observation.live_quote_reasons
    )
    reasons.extend(
        f"underlying_{reason}" for reason in underlying_assessment.quote_reasons
    )
    if evidence is None:
        return tuple(dict.fromkeys(reasons)), members
    if option_identity.content_hash != evidence.option_quote_hash:
        reasons.append("option_hash_mismatch")
    if underlying_identity.content_hash != evidence.underlying_quote_hash:
        reasons.append("underlying_hash_mismatch")
    if evidence.contract != option.contract:
        reasons.append("option_contract_mismatch")
    if underlying.symbol != evidence.contract.underlying:
        reasons.append("underlying_contract_mismatch")

    expected_protocol = dict(_COHERENCE_PROTOCOLS)[evidence.method]
    if evidence.protocol_id != expected_protocol:
        reasons.append("protocol_method_mismatch")
    if evidence.protocol_id not in manifest.coherence_protocol_ids:
        reasons.append("protocol_not_registered")
    if evidence.method == "joint_snapshot":
        if evidence.snapshot_id is None:
            reasons.append("joint_snapshot_id_missing")
        if evidence.sides:
            reasons.append("joint_sides_present")
    else:
        reasons.extend(_overlap_reasons(
            evidence, option, underlying, option_identity, underlying_identity
        ))

    point = evidence.coherent_at
    available = evidence.available_at
    if point is None:
        reasons.append("coherent_at_missing")
    else:
        if point > decision_at:
            reasons.append("coherent_at_after_decision")
        elif decision_at - point > max_quote_age:
            reasons.append("coherence_too_old")
    if available is None:
        reasons.append("available_at_missing")
    else:
        if available > decision_at:
            reasons.append("available_after_decision")
        if point is not None and point > available:
            reasons.append("coherent_at_after_available")
    if point is not None:
        for name, observed_at in (
            ("option_event", option.meta.event_at),
            ("underlying_event", underlying.meta.event_at),
            ("option_bid", option.bid_at), ("option_ask", option.ask_at),
            ("underlying_bid", underlying.bid_at),
            ("underlying_ask", underlying.ask_at),
        ):
            if observed_at is not None and observed_at > point:
                reasons.append(f"{name}_after_coherent_at")
    return tuple(dict.fromkeys(reasons)), members


def _overlap_reasons(
    evidence: QuoteCoherenceEvidence,
    option: QuoteObservation,
    underlying: UnderlyingQuote,
    option_identity: QuoteContentIdentity,
    underlying_identity: QuoteContentIdentity,
) -> tuple[str, ...]:
    """Check four actual side identities, observed chronology and containment."""
    reasons: list[str] = []
    if evidence.snapshot_id is not None:
        reasons.append("overlap_snapshot_id_present")
    if len(evidence.sides) != 4 or set(side.role for side in evidence.sides) != set(_ROLES):
        reasons.append("side_roles_invalid")
    sources = {
        "option_bid": (option_identity.content_hash, option.bid_at),
        "option_ask": (option_identity.content_hash, option.ask_at),
        "underlying_bid": (underlying_identity.content_hash, underlying.bid_at),
        "underlying_ask": (underlying_identity.content_hash, underlying.ask_at),
    }
    for side in evidence.sides:
        expected_hash, source_at = sources[side.role]
        start, end, watermark = side.valid_from, side.invalidated_at, side.observed_through
        if side.quote_content_hash != expected_hash:
            reasons.append(f"{side.role}_hash_mismatch")
        if start is None:
            reasons.append(f"{side.role}_valid_from_missing")
        else:
            if source_at is not None and source_at > start:
                reasons.append(f"{side.role}_source_after_valid_from")
            if end is not None and end <= start:
                reasons.append(f"{side.role}_interval_order_invalid")
            if watermark is not None and watermark < start:
                reasons.append(f"{side.role}_watermark_before_valid_from")
        if watermark is None:
            reasons.append(f"{side.role}_watermark_missing")
        if evidence.available_at is not None:
            if watermark is not None and watermark > evidence.available_at:
                reasons.append(f"{side.role}_watermark_after_available")
            if end is not None and end > evidence.available_at:
                reasons.append(f"{side.role}_invalidation_after_available")
        if evidence.coherent_at is not None and not _sides_support((side,), evidence.coherent_at):
            reasons.append(f"{side.role}_instant_not_supported")
    return tuple(reasons)


def _matching_quote_members(
    manifest: VerifiedFixtureManifest,
    kind: str,
    actual: QuoteObservation | UnderlyingQuote,
) -> tuple[VerifiedFixtureMember, ...]:
    """Return every member that normalizes to the complete supplied quote."""
    matches: list[VerifiedFixtureMember] = []
    for member in manifest.members:
        if member.kind != kind:
            continue
        envelope = member.decode_envelope()
        try:
            received_at = _parse_timestamp(
                envelope["simulated_received_at"], "simulated_received_at"
            )
            metadata = normalize_observation_meta(
                envelope["metadata"], event_id=envelope["event_id"],
                raw_ref=envelope["raw_ref"], received_at=received_at,
            )
            if metadata.value is None:
                continue
            if kind == "option_quote":
                normalized = normalize_quote_observation(
                    member.decode_raw_body(),
                    contract=_parse_contract_id(envelope["contract"]),
                    meta=metadata.value, event_id=envelope["event_id"],
                )
            else:
                normalized = normalize_underlying_quote(
                    member.decode_raw_body(), meta=metadata.value,
                    event_id=envelope["event_id"],
                )
        except _InvalidInput:
            continue
        if normalized.value == actual:
            matches.append(member)
    return tuple(matches)


def _matching_coherence_members(
    manifest: VerifiedFixtureManifest,
    actual: QuoteCoherenceEvidence,
) -> tuple[VerifiedFixtureMember, ...]:
    """Return every member that normalizes to the complete supplied evidence."""
    matches: list[VerifiedFixtureMember] = []
    for member in manifest.members:
        if member.kind != "quote_coherence":
            continue
        envelope = member.decode_envelope()
        try:
            received_at = _parse_timestamp(
                envelope["simulated_received_at"], "simulated_received_at"
            )
        except _InvalidInput:
            continue
        normalized = normalize_quote_coherence(
            member.decode_raw_body(), event_id=envelope["event_id"],
            raw_ref=envelope["raw_ref"], received_at=received_at,
        )
        if normalized.value == actual:
            matches.append(member)
    return tuple(matches)


def _profile_matches(
    profile: dict[str, object] | None,
    role: str,
    record: QuoteObservation | UnderlyingQuote | QuoteCoherenceEvidence | None,
) -> bool:
    """Check a matched member against its admitted modeled source profile."""
    if profile is None or record is None:
        return False
    if role == "evidence":
        return (
            profile["kind"] == "quote_coherence"
            and profile["source"] == record.source
            and profile["feed_class"] is None
            and profile["fidelity"] is None
            and profile["availability_basis"] == "measured"
            and profile["units"] == {}
            and profile["record_identity_rule"] == "new_evidence_id_per_update"
        )
    expected_kind = "option_quote" if role == "option" else "underlying_quote"
    return (
        profile["kind"] == expected_kind
        and profile["source"] == record.meta.source
        and profile["feed_class"] == record.meta.feed_class
        and profile["fidelity"] == record.meta.fidelity
        and profile["availability_basis"] == record.meta.availability_basis
        and profile["record_identity_rule"] == "new_provider_record_id_per_update"
    )


def _sides_support(sides: tuple[SideValidity, ...], at: datetime) -> bool:
    """Return whether all retained side intervals contain one explicit instant."""
    return all(
        side.valid_from is not None
        and side.observed_through is not None
        and side.valid_from <= at <= side.observed_through
        and (side.invalidated_at is None or at < side.invalidated_at)
        for side in sides
    )
