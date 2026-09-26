"""Admit finite calendar and fit membership content from actual fixture members."""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
import hashlib

from ._input_parsing import _InvalidInput, _fail, _parse_contract_id, _parse_date
from ._validation import _require_nonempty_string
from .account_inputs import _contract_snapshot
from .admission import VerifiedFixtureManifest, VerifiedFixtureMember, _canonical_bytes, verify_fixture_bundle
from .bar_inputs import _UnsupportedIdentity, _timestamp_string
from .bundle_inputs import ParsedModelData, ModelDataRow, _identifier, _make, _shape, normalize_model_bytes
from .bundle_manifest_inputs import ExternalReference, _list, _one, _reference, _timestamp
from .config import _snapshot_hash
from .features import _session_snapshot
from .session_inputs import SessionInputRejection, normalize_exchange_session
from .sessions import ExchangeSession, _hours_reasons


_GENERATOR = "optionslab-calibration-inputs-fixture-builder"
_SAMPLE_FIELDS = ("schema_version", "sample_id", "decision_id", "contract", "session_date",
    "feature_available_at", "information_start", "information_end", "label_available_at", "available_at",
    "outcome_status", "bucket_id")
_CALENDAR_FIELDS = ("schema_version", "calendar_id", "calendar", "coverage_start_date",
    "coverage_end_date", "session_refs", "closed_dates", "available_at")
_MEMBERSHIP_FIELDS = ("schema_version", "membership_id", "sample_refs")
_TIME_FIELDS = ("feature_available_at", "information_start", "information_end", "label_available_at", "available_at")
_MAX_BODY = 8 * 1024 * 1024
_MAX_ROOTS = 32 * 1024 * 1024


@dataclass(frozen=True, init=False)
class CalibrationMember:
    """This class represents one complete admitted fit sample and its source."""

    sample_id: str
    decision_id: str
    contract: object
    session_date: date
    feature_available_at: datetime | None
    information_start: datetime | None
    information_end: datetime | None
    label_available_at: datetime | None
    available_at: datetime | None
    outcome_status: str
    bucket_id: str
    manifest: VerifiedFixtureManifest
    member: VerifiedFixtureMember
    source_record_hash: str
    content_hash: str

    def __init__(self) -> None:
        """Block unadmitted sample construction.

        :returns: None.
        :raises TypeError: Always; use normalize_fit_membership.
        """
        raise TypeError("CalibrationMember values come from normalization")

    def snapshot(self) -> dict[str, object]:
        """Return all normalized sample facts and actual source identity.

        :returns: A fresh explicit semantic snapshot.
        """
        return dict(record_kind="options_lab.fit_sample", schema_version=1, sample_id=self.sample_id,
            decision_id=self.decision_id, contract=_contract_snapshot(self.contract),
            session_date=self.session_date.isoformat(),
            **{name: _timestamp_string(getattr(self, name)) for name in _TIME_FIELDS},
            outcome_status=self.outcome_status, bucket_id=self.bucket_id,
            source_fixture_id=self.manifest.fixture_id, source_payload_sha256=self.manifest.payload_sha256,
            source_record_id=self.member.record_id, source_record_hash=self.source_record_hash)


@dataclass(frozen=True, init=False)
class CalendarSessionEvidence:
    """This class represents a resolved open session and its complete source facts."""

    reference: ExternalReference
    manifest: VerifiedFixtureManifest
    member: VerifiedFixtureMember
    profile_bytes: bytes
    session: ExchangeSession
    structural_reasons: tuple[str, ...]

    def __init__(self) -> None:
        """Block unadmitted session evidence construction.

        :returns: None.
        :raises TypeError: Always; use normalize_calendar_descriptor.
        """
        raise TypeError("CalendarSessionEvidence values come from normalization")

    def snapshot(self) -> dict[str, object]:
        """Return a fresh source bound session snapshot.

        :returns: Complete session, reference, profile and structural facts.
        """
        return dict(reference=self.reference.snapshot(), profile_bytes=self.profile_bytes.decode("ascii"),
            session=_session_snapshot(self.session), structural_reasons=list(self.structural_reasons))


@dataclass(frozen=True, init=False)
class CalendarDescriptor:
    """This class represents an exact covered calendar with retained open sessions."""

    calendar_id: str
    calendar: str
    coverage_start_date: date
    coverage_end_date: date
    session_refs: tuple[ExternalReference, ...]
    sessions: tuple[CalendarSessionEvidence, ...]
    closed_dates: tuple[date, ...]
    available_at: datetime | None
    fixture: VerifiedFixtureManifest
    member: VerifiedFixtureMember
    upstream_fixtures: tuple[VerifiedFixtureManifest, ...]
    content_hash: str

    def __init__(self) -> None:
        """Block caller selected calendar authority.

        :returns: None.
        :raises TypeError: Always; use normalize_calendar_descriptor.
        """
        raise TypeError("CalendarDescriptor values come from normalization")

    def snapshot(self) -> dict[str, object]:
        """Return all explicit normalized calendar facts.

        :returns: Fresh semantic snapshot without its containing root.
        """
        return dict(record_kind="options_lab.calendar_descriptor", schema_version=1,
            calendar_id=self.calendar_id, calendar=self.calendar,
            coverage_start_date=self.coverage_start_date.isoformat(),
            coverage_end_date=self.coverage_end_date.isoformat(),
            session_refs=[ref.snapshot() for ref in self.session_refs],
            sessions=[item.snapshot() for item in self.sessions],
            closed_dates=[day.isoformat() for day in self.closed_dates],
            available_at=_timestamp_string(self.available_at))


@dataclass(frozen=True, init=False)
class FitMembership:
    """This class represents one ordered, source bound model or tuning population."""

    membership_id: str
    role: str
    sample_refs: tuple[ExternalReference, ...]
    rows: tuple[CalibrationMember, ...]
    model: ParsedModelData
    fixture: VerifiedFixtureManifest
    member: VerifiedFixtureMember
    upstream_fixtures: tuple[VerifiedFixtureManifest, ...]
    content_hash: str

    def __init__(self) -> None:
        """Block caller selected fit population authority.

        :returns: None.
        :raises TypeError: Always; use normalize_fit_membership.
        """
        raise TypeError("FitMembership values come from normalization")

    def snapshot(self) -> dict[str, object]:
        """Return every ordered reference, row and rechecked model identity.

        :returns: Fresh explicit semantic snapshot.
        """
        return dict(record_kind="options_lab.fit_membership", schema_version=1,
            membership_id=self.membership_id, role=self.role,
            sample_refs=[ref.snapshot() for ref in self.sample_refs],
            rows=[row.snapshot() for row in self.rows], model_hash=self.model.model_hash)


@dataclass(frozen=True, init=False)
class CalibrationInputRejection:
    """This class represents one safe reached calendar or membership failure."""

    stage: str
    fixture: VerifiedFixtureManifest
    member: VerifiedFixtureMember | None
    event_id: str
    raw_ref: str
    received_at: datetime
    field: str
    code: str
    cause: object | None

    def __init__(self) -> None:
        """Block caller selected failure evidence.

        :returns: None.
        :raises TypeError: Always; use a calibration input normalizer.
        """
        raise TypeError("CalibrationInputRejection values come from normalization")


@dataclass(frozen=True, init=False)
class CalendarDescriptorValidation:
    """This class represents one calendar value or a safe rejection."""

    value: CalendarDescriptor | None
    rejection: CalibrationInputRejection | None

    def __init__(self) -> None:
        """Block caller selected outcomes.

        :returns: None.
        :raises TypeError: Always; use normalize_calendar_descriptor.
        """
        raise TypeError("CalendarDescriptorValidation values come from normalization")


@dataclass(frozen=True, init=False)
class FitMembershipValidation:
    """This class represents one fit population or a safe rejection."""

    value: FitMembership | None
    rejection: CalibrationInputRejection | None

    def __init__(self) -> None:
        """Block caller selected outcomes.

        :returns: None.
        :raises TypeError: Always; use normalize_fit_membership.
        """
        raise TypeError("FitMembershipValidation values come from normalization")


class _Failure(Exception):
    """This class represents bounded private failure with an optional reached cause."""


def _check(condition, field, code="counterpart_mismatch", cause=None):
    """Raise one safe private failure for a failed concrete comparison."""
    if not condition:
        raise _Failure(field, code, cause)


def _receipt(member, fixture):
    """Return the actual member or containing fixture receipt."""
    if member is None:
        return dict(event_id=fixture.event_id, raw_ref=fixture.raw_ref, received_at=fixture.received_at)
    env = member.decode_envelope()
    return dict(event_id=env["event_id"], raw_ref=env["raw_ref"],
        received_at=_timestamp(env["simulated_received_at"], "envelope.simulated_received_at"))


def _roots(fixture, explicit):
    """Bound distinct actual bytes and re-admit all supplied roots once."""
    _check(len(explicit) <= 64, "upstream_fixtures", "resource_limit")
    ids, shas, total = {}, {}, 0
    for index, old in enumerate((fixture, *explicit)):
        _check(type(old.payload_bytes) is bytes and type(old.payload_sha256) is str,
               "fixtures", "retained_content_mismatch")
        _check(hashlib.sha256(old.payload_bytes).hexdigest() == old.payload_sha256,
               "fixtures", "payload_mismatch")
        _check(index == 0 or old.fixture_id != fixture.fixture_id and old.payload_sha256 != fixture.payload_sha256,
               "upstream_fixtures", "containing_reference")
        _check(old.fixture_id not in ids and old.payload_sha256 not in shas,
               "upstream_fixtures", "duplicate_reference")
        ids[old.fixture_id] = old
        shas[old.payload_sha256] = old
        total += len(old.payload_bytes)
        _check(total <= _MAX_ROOTS, "fixtures", "resource_limit")
    fresh = {}
    for old in ids.values():
        admitted = verify_fixture_bundle(old.fixture_id, old.payload_bytes,
            event_id=old.event_id, raw_ref=old.raw_ref, received_at=old.received_at)
        _check(admitted.value is not None, "fixtures", "admission_failed", admitted.rejection)
        value = admitted.value
        for name in ("fixture_id", "payload_sha256", "payload_bytes", "members", "modeled_source_profiles_bytes",
                     "generator_id", "generator_version", "assembled_at", "generator_source_ref", "received_at"):
            _check(getattr(value, name) == getattr(old, name), "fixtures", "retained_content_mismatch")
        fresh[value.fixture_id] = value
    return fresh


def _own_member(fixture, record_id, kinds):
    """Resolve one containing member with actual raw/profile/envelope authority."""
    member = next((item for item in fixture.members if item.record_id == record_id), None)
    _check(member is not None, "record_id", "unknown_member")
    _check(member.kind in kinds, "record_id", "member_kind_mismatch")
    _check(hashlib.sha256(member.raw_body_bytes).hexdigest() == member.raw_hash,
           "record_id", "raw_hash_mismatch")
    profile = next((p for p in fixture.decode_modeled_source_profiles() if p["profile_id"] == member.profile_id), None)
    _check(profile is not None and fixture.generator_id == profile["source"] == _GENERATOR
           and fixture.generator_version == "1", "record_id", "profile_mismatch")
    _check(member.decode_envelope()["supersedes_record_id"] is None,
           "record_id", "artifact_not_immutable")
    return member


def _body(raw, member):
    """Compare a preflighted exact body to retained canonical member bytes."""
    _check(len(member.raw_body_bytes) <= _MAX_BODY, "$", "resource_limit")
    _check(_canonical_bytes(raw) == member.raw_body_bytes, "$", "member_content_mismatch")


def _body_size(value):
    """Count exact canonical JSON bytes from preflighted primitive leaves."""
    if type(value) is str:
        size = 2
        for char in value:
            code = ord(char)
            size += (2 if char in '"\\\b\f\n\r\t' else
                     6 if code < 32 or 127 <= code <= 65535 else
                     12 if code > 65535 else 1)
        return size
    if type(value) is dict:
        return 2 + max(0, len(value) - 1) + sum(_body_size(key) + 1 + _body_size(item)
                                                   for key, item in value.items())
    if type(value) is list:
        return 2 + max(0, len(value) - 1) + sum(_body_size(item) for item in value)
    if value is None:
        return 4
    if type(value) is bool:
        return 4 if value else 5
    if type(value) is int:
        return len(str(value))
    _fail("$", "invalid_type")


def _refs(raw, field, limit):
    """Parse an ordered finite unique list of exact external references."""
    refs, seen = [], set()
    for index, value in enumerate(_list(raw, field, limit)):
        ref = _reference(value, f"{field}.{index}", False)
        identity = (ref.fixture_id, ref.payload_sha256, ref.record_id, ref.raw_hash)
        if identity in seen:
            _fail(f"{field}.{index}", "duplicate_reference")
        seen.add(identity)
        refs.append(ref)
    return tuple(refs)


def _resolve(ref, roots, kind):
    """Resolve all four reference facts against an actual re-admitted member."""
    root = roots.get(ref.fixture_id)
    _check(root is not None, "reference.fixture_id", "unknown_fixture")
    _check(root.payload_sha256 == ref.payload_sha256, "reference.payload_sha256", "payload_mismatch")
    member = next((item for item in root.members if item.record_id == ref.record_id), None)
    _check(member is not None, "reference.record_id", "unknown_member")
    _check(member.kind == kind, "reference.record_id", "member_kind_mismatch")
    _check(member.raw_hash == ref.raw_hash and hashlib.sha256(member.raw_body_bytes).hexdigest() == ref.raw_hash,
           "reference.raw_hash", "raw_hash_mismatch")
    _check(len(member.raw_body_bytes) <= _MAX_BODY, "reference.raw_body", "resource_limit")
    return root, member


def _used(refs, roots, fixture):
    """Reject supplied roots that no successful reference reached."""
    reached = {ref.fixture_id for ref in refs}
    _check(all(root.fixture_id in reached for root in roots.values() if root.fixture_id != fixture.fixture_id),
           "upstream_fixtures", "unused_reference")
    return tuple(root for root in roots.values() if root.fixture_id in reached and root.fixture_id != fixture.fixture_id)


def _sample(root, member):
    """Normalize one actual sample without repairing adverse time/status facts."""
    _check(root.generator_id == _GENERATOR and root.generator_version == "1", "fit_sample", "profile_mismatch")
    profile = next((p for p in root.decode_modeled_source_profiles() if p["profile_id"] == member.profile_id), None)
    _check(profile is not None and profile["kind"] == "fit_sample" and profile["source"] == _GENERATOR
           and member.decode_envelope()["supersedes_record_id"] is None,
           "fit_sample", "profile_mismatch")
    raw = member.decode_raw_body()
    _shape(raw, "fit_sample", _SAMPLE_FIELDS)
    _one(raw["schema_version"], "fit_sample.schema_version")
    sample_id = _identifier(raw["sample_id"], "fit_sample.sample_id")
    decision_id = _identifier(raw["decision_id"], "fit_sample.decision_id")
    _shape(raw["contract"], "contract", ("underlying", "expiry", "right", "strike", "multiplier", "deliverable_id"))
    for name in ("underlying", "deliverable_id"):
        _identifier(raw["contract"][name], "contract." + name)
    _check(type(raw["contract"]["strike"]) is str and len(raw["contract"]["strike"]) <= 1002,
           "contract.strike", "resource_limit")
    contract = _parse_contract_id(raw["contract"])
    _contract_snapshot(contract)
    times = {name: _timestamp(raw[name], "fit_sample." + name, True) for name in _TIME_FIELDS}
    row = _make(CalibrationMember, sample_id=sample_id, decision_id=decision_id, contract=contract,
        session_date=_parse_date(raw["session_date"], "fit_sample.session_date"), **times,
        outcome_status=_identifier(raw["outcome_status"], "fit_sample.outcome_status"),
        bucket_id=_identifier(raw["bucket_id"], "fit_sample.bucket_id"),
        manifest=root, member=member, source_record_hash=member.raw_hash, content_hash="")
    _check(row.outcome_status in ("observed_fill", "observed_no_fill", "censored", "invalid"),
           "fit_sample.outcome_status", "invalid_value")
    object.__setattr__(row, "content_hash", _snapshot_hash(row.snapshot()))
    return row


def normalize_calendar_descriptor(raw: object, *, fixture: VerifiedFixtureManifest, record_id: str,
                                  upstream_fixtures: tuple[VerifiedFixtureManifest, ...] = ()) -> CalendarDescriptorValidation:
    """Admit a complete explicit XNYS date coverage and its actual open sessions.

    :param raw: Exact untrusted calendar descriptor body.
    :param fixture: Actual containing P08 fixture.
    :param record_id: Trusted containing member identifier.
    :param upstream_fixtures: Exact tuple of referenced P08 fixtures.
    :returns: One immutable calendar or bounded reached rejection.
    :raises TypeError: If an owner or trusted tuple has an unexpected exact type.
    :raises ValueError: If the trusted record identifier is empty.
    """
    _trusted(fixture, record_id, upstream_fixtures)
    reached = None
    try:
        _shape(raw, "$", _CALENDAR_FIELDS)
        _one(raw["schema_version"], "schema_version")
        calendar_id = _identifier(raw["calendar_id"], "calendar_id")
        _check(raw["calendar"] == "XNYS" if type(raw["calendar"]) is str else False,
               "calendar", "invalid_value")
        start = _parse_date(raw["coverage_start_date"], "coverage_start_date")
        end = _parse_date(raw["coverage_end_date"], "coverage_end_date")
        _check(start <= end and (end - start).days < 4096, "coverage_end_date", "resource_limit")
        refs = _refs(raw["session_refs"], "session_refs", 4096)
        closed_raw = _list(raw["closed_dates"], "closed_dates", 4096)
        _check(len(refs) + len(closed_raw) <= 4096, "session_refs", "resource_limit")
        closed = tuple(_parse_date(item, f"closed_dates.{i}") for i, item in enumerate(closed_raw))
        _check(len(set(closed)) == len(closed), "closed_dates", "duplicate_reference")
        _check(closed == tuple(sorted(closed)), "closed_dates", "invalid_value")
        available = _timestamp(raw["available_at"], "available_at", True)
        _check(_body_size(raw) <= _MAX_BODY, "$", "resource_limit")
        roots = _roots(fixture, upstream_fixtures)
        own = _own_member(roots[fixture.fixture_id], record_id, ("calendar_descriptor",))
        reached = own
        _body(raw, own)
        sessions = []
        for ref in refs:
            source, source_member = _resolve(ref, roots, "exchange_session")
            reached = source_member
            env = _receipt(source_member, source)
            normalized = normalize_exchange_session(source_member.decode_raw_body(), **env)
            _check(normalized.value is not None, "session_refs", "normalization_failed", normalized.rejection)
            session = normalized.value
            profile = next(p for p in source.decode_modeled_source_profiles() if p["profile_id"] == source_member.profile_id)
            _check(profile["source"] == session.source and profile["availability_basis"] == session.availability_basis
                   and profile["fidelity"] == session.fidelity and profile["kind"] == "exchange_session"
                   and source_member.decode_envelope()["supersedes_record_id"] is None,
                   "session_refs", "profile_mismatch")
            reasons = []
            _hours_reasons(session, "session", reasons)
            _check(session.calendar == "XNYS" and session.kind in ("regular", "early_close") and not reasons,
                   "session_refs", "invalid_session")
            sessions.append(_make(CalendarSessionEvidence, reference=ref, manifest=source,
                member=source_member, profile_bytes=_canonical_bytes(profile), session=session,
                structural_reasons=tuple(reasons)))
        dates = tuple(item.session.session_date for item in sessions)
        _check(dates == tuple(sorted(set(dates))), "session_refs", "invalid_value")
        covered = set(dates) | set(closed)
        _check(not set(dates) & set(closed) and len(covered) == (end - start).days + 1
               and all(start <= day <= end for day in covered), "coverage", "incomplete_coverage")
        upstream = _used(refs, roots, fixture)
        value = _make(CalendarDescriptor, calendar_id=calendar_id, calendar="XNYS",
            coverage_start_date=start, coverage_end_date=end, session_refs=refs, sessions=tuple(sessions),
            closed_dates=closed, available_at=available, fixture=roots[fixture.fixture_id], member=own,
            upstream_fixtures=upstream, content_hash="")
        object.__setattr__(value, "content_hash", _snapshot_hash(value.snapshot()))
    except (_InvalidInput, _Failure, _UnsupportedIdentity, MemoryError, RecursionError) as failure:
        result = _rejected(CalendarDescriptorValidation, "calendar_descriptor", fixture, reached, failure)
        return result
    return _make(CalendarDescriptorValidation, value=value, rejection=None)


def normalize_fit_membership(raw: object, *, fixture: VerifiedFixtureManifest, record_id: str,
                             model: ParsedModelData, upstream_fixtures: tuple[VerifiedFixtureManifest, ...] = ()) -> FitMembershipValidation:
    """Admit all ordered model or tuning samples against actual fixed-right model rows.

    :param raw: Exact untrusted membership body.
    :param fixture: Actual containing P08 fixture.
    :param record_id: Trusted containing member identifier.
    :param model: Exact inspected A1a model evidence.
    :param upstream_fixtures: Exact tuple of referenced P08 fixtures.
    :returns: One immutable membership or bounded reached rejection.
    :raises TypeError: If an owner, model or trusted tuple has an unexpected exact type.
    :raises ValueError: If the trusted record identifier is empty.
    """
    _trusted(fixture, record_id, upstream_fixtures)
    if type(model) is not ParsedModelData:
        raise TypeError("model must be an exact ParsedModelData")
    reached = None
    try:
        _shape(raw, "$", _MEMBERSHIP_FIELDS)
        _one(raw["schema_version"], "schema_version")
        membership_id = _identifier(raw["membership_id"], "membership_id")
        refs = _refs(raw["sample_refs"], "sample_refs", 4096)
        _check(_body_size(raw) <= _MAX_BODY, "$", "resource_limit")
        roots = _roots(fixture, upstream_fixtures)
        own = _own_member(roots[fixture.fixture_id], record_id, ("model_membership", "tuning_membership"))
        reached = own
        _body(raw, own)
        role = "model" if own.kind == "model_membership" else "tuning"
        _check(type(model.original_model_bytes) is bytes and type(model.model_hash) is str
               and type(model.model_kind) is str and type(model.format_id) is str
               and type(model.rows) is tuple and all(type(row) is ModelDataRow
                   and type(row.right) is str and type(row.calibration_bucket) is str
                   and type(row.mean_attempt_return) is Decimal for row in model.rows),
               "model", "retained_content_mismatch")
        rechecked = normalize_model_bytes(model.original_model_bytes, **_receipt(own, fixture))
        _check(rechecked.value is not None, "model", "normalization_failed", rechecked.rejection)
        actual_model = rechecked.value
        _check(actual_model == model and actual_model.model_kind == "fixture_fixed_by_right"
               and len(actual_model.rows) == 2 and actual_model.rows[0].calibration_bucket != actual_model.rows[1].calibration_bucket,
               "model", "model_mismatch")
        buckets = {row.right: row.calibration_bucket for row in actual_model.rows}
        rows = []
        for ref in refs:
            source, source_member = _resolve(ref, roots, "fit_sample")
            reached = source_member
            row = _sample(source, source_member)
            _check(row.bucket_id == buckets[row.contract.right], "sample_refs", "bucket_mismatch")
            rows.append(row)
        upstream = _used(refs, roots, fixture)
        value = _make(FitMembership, membership_id=membership_id, role=role,
            sample_refs=refs, rows=tuple(rows), model=actual_model, fixture=roots[fixture.fixture_id],
            member=own, upstream_fixtures=upstream, content_hash="")
        object.__setattr__(value, "content_hash", _snapshot_hash(value.snapshot()))
    except (_InvalidInput, _Failure, _UnsupportedIdentity, MemoryError, RecursionError) as failure:
        return _rejected(FitMembershipValidation, "fit_membership", fixture, reached, failure)
    return _make(FitMembershipValidation, value=value, rejection=None)


def _trusted(fixture, record_id, upstream):
    """Require exact trusted owner and tuple types before touching untrusted raw input."""
    if type(fixture) is not VerifiedFixtureManifest or type(upstream) is not tuple:
        raise TypeError("fixture and upstream_fixtures require exact owner and tuple types")
    if any(type(item) is not VerifiedFixtureManifest for item in upstream):
        raise TypeError("upstream_fixtures must contain exact VerifiedFixtureManifest values")
    _require_nonempty_string("record_id", record_id)


def _rejected(cls, stage, fixture, member, failure):
    """Return one safe rejection with the actual reached receipt and cause."""
    if type(failure) in (_InvalidInput, _Failure):
        field, code, *rest = failure.args
        cause = rest[0] if rest else None
    else:
        field, code, cause = "$", "resource_limit", None
    try:
        receipt = _receipt(member, fixture)
    except (RuntimeError, _InvalidInput, TypeError, ValueError):
        receipt = _receipt(None, fixture)
    rejection = _make(CalibrationInputRejection, stage=stage, fixture=fixture, member=member,
        **receipt, field=field, code=code, cause=cause)
    return _make(cls, value=None, rejection=rejection)
