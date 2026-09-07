"""Admitted explicit volume-training selections and causal economic identity."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from ._input_parsing import _InvalidInput, _fail, _parse_date, _parse_string, _require_shape
from ._validation import _require_nonempty_string, _trusted_datetime
from .admission import VerifiedFixtureManifest, VerifiedFixtureMember, _canonical_bytes
from .bar_inputs import BarInputRejection, identify_underlying_bar, normalize_underlying_bar
from .bars import BarAssessment, UnderlyingBar, assess_underlying_bar
from .config import _snapshot_hash
from .features import _session_snapshot, _source_identity
from .observations import InputRejection, normalize_observation_meta
from .session_inputs import SessionInputRejection, normalize_exchange_session
from .sessions import ExchangeSession, _calendar_facts
from .vwap_features import ELIGIBLE_VOLUME_DEFINITION_ID


_FIELDS = ("schema_version", "partition_id", "training_sessions", "validation_sessions",
           "test_sessions", "volume_definition_id", "training_inputs")
_GENERATOR = "optionslab-volume-training-fixture-builder"
_MINUTE = timedelta(minutes=1)


@dataclass(frozen=True)
class TrainingSessionBars:
    """This class represents supplied selected bars and their actual calendar session."""

    session: ExchangeSession
    bars: tuple[UnderlyingBar, ...]

    def __post_init__(self) -> None:
        """Validate exact immutable trusted input types, retaining adverse facts.

        :returns: None.
        :raises TypeError: If session or the bar tuple has an unexpected type.
        """
        if type(self.session) is not ExchangeSession:
            raise TypeError("session must be an ExchangeSession")
        if type(self.bars) is not tuple or any(type(bar) is not UnderlyingBar for bar in self.bars):
            raise TypeError("bars must be a tuple of UnderlyingBar values")


@dataclass(frozen=True, init=False)
class VolumePartition:
    """This class represents a selected partition bound to actual admitted bytes."""

    partition_id: str
    training_sessions: tuple[date, ...]
    validation_sessions: tuple[date, ...]
    test_sessions: tuple[date, ...]
    volume_definition_id: str
    training_inputs: tuple[tuple[str, tuple[str, ...]], ...]
    input_manifest_id: str
    input_manifest_hash: str
    record_id: str
    raw_hash: str

    def __init__(self) -> None:
        """Prevent caller declarations from minting admitted partition evidence.

        :returns: None.
        :raises TypeError: Always; use normalize_volume_partition.
        """
        raise TypeError("VolumePartition values come from normalize_volume_partition")


@dataclass(frozen=True, init=False)
class VolumeInputRejection:
    """This class represents safe partition rejection with its real receipt context."""

    manifest: VerifiedFixtureManifest
    record_id: str
    event_id: str
    received_at: datetime
    raw_ref: str
    field: str
    code: str

    def __init__(self) -> None:
        """Prevent contradictory caller-built parsing evidence.

        :returns: None.
        :raises TypeError: Always; rejection is produced by normalization.
        """
        raise TypeError("VolumeInputRejection values come from normalization")


@dataclass(frozen=True, init=False)
class VolumePartitionValidation:
    """This class represents exactly one admitted partition or safe rejection."""

    value: VolumePartition | None
    rejection: VolumeInputRejection | None

    def __init__(self) -> None:
        """Prevent callers from replacing actual normalization outcomes.

        :returns: None.
        :raises TypeError: Always; use normalize_volume_partition.
        """
        raise TypeError("VolumePartitionValidation values come from normalization")


@dataclass(frozen=True, init=False)
class VolumeTrainingInputEvidence:
    """This class represents one declared member, its original assessment and omission."""

    record_id: str
    member: VerifiedFixtureMember | None
    session_record_id: str
    session: ExchangeSession | None
    bar: UnderlyingBar | None
    assessment: BarAssessment | None
    rejection: SessionInputRejection | InputRejection | BarInputRejection | None
    minute_index: int | None
    economic_hash: str | None
    reasons: tuple[str, ...]
    omission_reasons: tuple[str, ...]

    def __init__(self) -> None:
        """Prevent callers from minting a verified member or omission exemption.

        :returns: None.
        :raises TypeError: Always; use bind_volume_training_inputs.
        """
        raise TypeError("VolumeTrainingInputEvidence values come from binding")


@dataclass(frozen=True, init=False)
class VolumeTrainingInputResult:
    """This class represents full receipts, canonical selected inputs and causal failures.

    A supported hash commits to the complete successful selection including
    ordinary typed omissions. It does not establish fitted bucket readiness.
    The retained manifest permanently carries synthetic/core_fixture authority.
    """

    training_bars: tuple[TrainingSessionBars, ...]
    cutoff: datetime
    partition: VolumePartition
    manifest: VerifiedFixtureManifest
    normalized_groups: tuple[TrainingSessionBars, ...]
    member_evidence: tuple[VolumeTrainingInputEvidence, ...]
    selected_inputs: tuple[VolumeTrainingInputEvidence, ...]
    reasons: tuple[str, ...]
    training_input_hash: str | None

    def __init__(self) -> None:
        """Prevent caller-supplied success flags or fitted input hashes.

        :returns: None.
        :raises TypeError: Always; use bind_volume_training_inputs.
        """
        raise TypeError("VolumeTrainingInputResult values come from binding")


def normalize_volume_partition(
    raw: object, *, manifest: VerifiedFixtureManifest, record_id: str,
) -> VolumePartitionValidation:
    """Parse a closed split declaration and compare it to the actual registered member.

    Exact nested types are parsed before equality can touch untrusted leaves.
    Payload identity is derived from the manifest, never a self-referencing body.

    :param raw: Untrusted partition body with explicit training member references.
    :param manifest: Actual factory-derived fixture admission evidence.
    :param record_id: Trusted nonempty registered partition member identifier.
    :returns: One member-bound partition or retained safe parsing rejection.
    :raises TypeError: If a trusted manifest or record identifier has the wrong type.
    :raises ValueError: If the trusted record identifier is empty.
    """
    _require_manifest(manifest)
    _require_nonempty_string("record_id", record_id)
    member = next((m for m in manifest.members if m.record_id == record_id), None)
    try:
        parsed = _parse_partition(raw)
        if member is None:
            _fail("record_id", "unknown_member")
        if member.kind != "volume_partition":
            _fail("record_id", "member_kind_mismatch")
        if _canonical_bytes(raw) != member.raw_body_bytes:
            _fail("$", "member_content_mismatch")
        profile = next(p for p in manifest.decode_modeled_source_profiles() if p["profile_id"] == member.profile_id)
        if manifest.generator_id != _GENERATOR or profile["source"] != _GENERATOR:
            _fail("record_id", "profile_mismatch")
        if member.decode_envelope()["supersedes_record_id"] is not None:
            _fail("record_id", "partition_not_immutable")
    except _InvalidInput as failure:
        envelope = _envelope(member) if member is not None else dict(
            event_id=manifest.event_id, received_at=manifest.received_at, raw_ref=manifest.raw_ref)
        rejection = _freeze(VolumeInputRejection, manifest=manifest, record_id=record_id,
                            **envelope, field=failure.args[0], code=failure.args[1])
        return _freeze(VolumePartitionValidation, value=None, rejection=rejection)
    value = _freeze(VolumePartition, **parsed, input_manifest_id=manifest.fixture_id,
                    input_manifest_hash=manifest.payload_sha256, record_id=record_id, raw_hash=member.raw_hash)
    return _freeze(VolumePartitionValidation, value=value, rejection=None)


def _parse_partition(raw: object) -> dict[str, object]:
    """Parse the exact finite partition schema before using any caller equality."""
    _require_shape(raw, "$", _FIELDS)
    if type(raw["schema_version"]) is not int:
        _fail("schema_version", "invalid_type")
    if raw["schema_version"] != 1:
        _fail("schema_version", "invalid_value")
    parsed = {name: _parse_string(raw[name], name) for name in ("partition_id", "volume_definition_id")}
    for name in ("training_sessions", "validation_sessions", "test_sessions"):
        values = _list(raw[name], name)
        days = tuple(_parse_date(value, name) for value in values)
        if days != tuple(sorted(set(days))):
            _fail(name, "invalid_value")
        parsed[name] = days
    train, validation, test = (parsed[name] for name in ("training_sessions", "validation_sessions", "test_sessions"))
    if not train or any(set(a) & set(b) for a, b in ((train, validation), (train, test), (validation, test))):
        _fail("training_sessions", "invalid_value")
    nonempty = [days for days in (train, validation, test) if days]
    if any(a[-1] >= b[0] for a, b in zip(nonempty, nonempty[1:])):
        _fail("training_sessions", "invalid_value")
    groups = []
    for item in _list(raw["training_inputs"], "training_inputs"):
        _require_shape(item, "training_inputs", ("session_record_id", "bar_record_ids"))
        session_id = _parse_string(item["session_record_id"], "training_inputs.session_record_id")
        bars = tuple(_parse_string(value, "training_inputs.bar_record_ids") for value in _list(item["bar_record_ids"], "training_inputs.bar_record_ids"))
        groups.append((session_id, tuple(sorted(set(bars)))))
    parsed["training_inputs"] = tuple(sorted(set(groups)))
    return parsed


def bind_volume_training_inputs(
    training_bars: tuple[TrainingSessionBars, ...], cutoff: datetime, *,
    partition: VolumePartition, manifest: VerifiedFixtureManifest,
) -> VolumeTrainingInputResult:
    """Reconcile every declared selected member before committing causal volume inputs.

    Unknown volume/eligibility is an omission after independent source and
    temporal checks. Unresolved raw failures or supplied substitutions fail
    the whole binding. No return history or exact VWAP readiness is required.

    :param training_bars: Exact immutable submitted groups, including duplicate receipts.
    :param cutoff: Explicit aware feature-data availability cutoff.
    :param partition: Factory-derived explicit split and registered selected references.
    :param manifest: Actual admitted manifest containing the partition and inputs.
    :returns: Complete receipt evidence, canonical selected inputs and hash or failure.
    :raises TypeError: If a trusted argument has the wrong exact concrete type.
    :raises ValueError: If cutoff is naive or unrepresentable.
    """
    _require_manifest(manifest)
    if type(partition) is not VolumePartition:
        raise TypeError("partition must be a VolumePartition")
    if type(training_bars) is not tuple or any(type(g) is not TrainingSessionBars for g in training_bars):
        raise TypeError("training_bars must be a tuple of TrainingSessionBars")
    cutoff = _trusted_datetime("cutoff", cutoff)
    members = {m.record_id: m for m in manifest.members}
    profiles = {p["profile_id"]: p for p in manifest.decode_modeled_source_profiles()}
    reasons, evidence, groups = [], [], []
    actual = members.get(partition.record_id)
    verified = None if actual is None else normalize_volume_partition(actual.decode_raw_body(), manifest=manifest, record_id=actual.record_id).value
    if verified != partition:
        reasons.append("partition_manifest_mismatch")
    if partition.volume_definition_id != ELIGIBLE_VOLUME_DEFINITION_ID:
        reasons.append("volume_definition_unsupported")
    for session_id, bar_ids in partition.training_inputs:
        session_row = _session_evidence(session_id, members.get(session_id), profiles, partition, cutoff)
        evidence.append(session_row)
        bars = []
        for record_id in bar_ids:
            row = _bar_evidence(record_id, members.get(record_id), profiles, session_row, partition, cutoff)
            evidence.append(row)
            if row.bar is not None:
                bars.append(row.bar)
        if session_row.session is not None:
            groups.append(TrainingSessionBars(session_row.session, tuple(bars)))
    reasons.extend(reason for row in evidence for reason in row.reasons)
    reasons.extend(_supplied_reasons(training_bars, tuple(groups), partition, cutoff))
    selected = {}
    streams = set()
    source_identities, session_identities = {}, {}
    session_source_identities = {}
    for row in evidence:
        if row.bar is None and row.session is not None and row.economic_hash is not None:
            previous = session_identities.setdefault(row.session.session_date, row.economic_hash)
            if previous != row.economic_hash:
                reasons.append("session_identity_conflict")
            source_key = (row.member.kind, row.member.decode_envelope()["stream_id"],
                          row.session.source, row.session.provider_record_id)
            previous = session_source_identities.setdefault(source_key, row.economic_hash)
            if previous != row.economic_hash:
                reasons.append("source_identity_conflict")
        if row.bar is not None and row.economic_hash is not None:
            previous = source_identities.setdefault(_source_identity(row.bar), row.economic_hash)
            if previous != row.economic_hash:
                reasons.append("source_identity_conflict")
        if row.bar is None or row.minute_index is None or row.economic_hash is None:
            continue
        key = (row.session.session_date, row.minute_index)
        old = selected.get(key)
        if old is not None and old.economic_hash != row.economic_hash:
            reasons.append("session_minute_conflict")
        if old is None or row.record_id < old.record_id:
            selected[key] = row
        env = row.member.decode_envelope()
        streams.add((row.member.profile_id, env["stream_id"], row.bar.meta.source,
                     row.bar.meta.feed_class, row.bar.meta.fidelity, row.bar.meta.availability_basis,
                     row.bar.price_basis))
    if len(streams) > 1:
        reasons.append("volume_stream_mismatch")
    canonical = tuple(selected[key] for key in sorted(selected))
    reasons = tuple(sorted(set(reasons)))
    input_hash = None if reasons else _input_hash(partition, cutoff, evidence, canonical)
    return _freeze(VolumeTrainingInputResult, training_bars=training_bars, cutoff=cutoff,
                   partition=partition, manifest=manifest, normalized_groups=tuple(groups),
                   member_evidence=tuple(evidence), selected_inputs=canonical,
                   reasons=reasons, training_input_hash=input_hash)


def _session_evidence(record_id, member, profiles, partition, cutoff):
    """Normalize and assess each declared calendar even when all its bars omit."""
    reasons = _member_reasons(member, "exchange_session")
    session = rejection = economic_hash = None
    if not reasons:
        result = normalize_exchange_session(member.decode_raw_body(), **_envelope(member))
        session, rejection = result.value, result.rejection
        if rejection is not None:
            reasons.append("normalization_failed")
        else:
            reasons.extend(_session_reasons(session, partition, cutoff))
            profile = profiles[member.profile_id]
            if any(getattr(session, name) != profile[name] for name in ("source", "fidelity", "availability_basis")):
                reasons.append("profile_mismatch")
            economic_hash = _snapshot_hash(_calendar_snapshot(session))
    return _freeze(VolumeTrainingInputEvidence, record_id=record_id, member=member,
                   session_record_id=record_id, session=session, bar=None, assessment=None,
                   rejection=rejection, minute_index=None, economic_hash=economic_hash,
                   reasons=tuple(reasons), omission_reasons=())


def _bar_evidence(record_id, member, profiles, session_row, partition, cutoff):
    """Keep source, interval and eligible-volume evidence independent of close/VWAP."""
    reasons = _member_reasons(member, "underlying_bar")
    bar = rejection = assessment = minute = economic_hash = None
    omissions = ()
    session = session_row.session
    if not reasons:
        env = member.decode_envelope()
        meta = normalize_observation_meta(env["metadata"], **_envelope(member))
        rejection = meta.rejection
        if meta.value is not None:
            result = normalize_underlying_bar(member.decode_raw_body(), meta=meta.value,
                                             event_id=env["event_id"], receive_sequence=env["receive_sequence"])
            bar, rejection = result.value, result.rejection
        if rejection is not None:
            reasons.append("normalization_failed")
        if bar is not None:
            assessment = assess_underlying_bar(bar, session, as_of=cutoff)
            reasons.extend(assessment.availability_reasons)
            profile = profiles[member.profile_id]
            if any(getattr(bar.meta, name) != profile[name] for name in ("source", "feed_class", "fidelity", "availability_basis")):
                reasons.append("profile_mismatch")
            checks = (
                (bar.price_basis != "raw", "price_basis_not_raw"),
                (bar.meta.is_fill_forward, "fill_forward"),
                (bool(bar.meta.quality_flags), "quality_flags_present"),
                (bar.supersedes_revision_id == bar.revision_id, "revision_self_supersession"),
                (bar.volume_definition_id is not None and bar.volume_definition_id != partition.volume_definition_id, "volume_definition_mismatch"),
            )
            reasons.extend(reason for failed, reason in checks if failed)
            omissions = tuple(reason for reason in assessment.volume_reasons if reason in ("volume_missing", "volume_definition_unknown"))
            reasons.extend(reason for reason in assessment.volume_reasons if reason not in omissions)
            identity = identify_underlying_bar(bar)
            economic_hash = identity.economic_content_hash
            reasons.extend(identity.economic_reasons)
            if session is not None and session.opens_at is not None and bar.interval_end is not None:
                elapsed = bar.interval_end - session.opens_at
                if elapsed > timedelta(0) and elapsed % _MINUTE == timedelta(0):
                    minute = elapsed // _MINUTE
    return _freeze(VolumeTrainingInputEvidence, record_id=record_id, member=member,
                   session_record_id=session_row.record_id, session=session, bar=bar,
                   assessment=assessment, rejection=rejection, minute_index=minute,
                   economic_hash=economic_hash, reasons=tuple(reasons), omission_reasons=omissions)


def _session_reasons(session, partition, cutoff):
    """Enforce explicit allowlist, actual regular calendar and a fully ended day."""
    try:
        reasons, _ = _calendar_facts(session, expected_calendar="XNYS",
                                     expected_session_date=session.session_date, cutoff=cutoff)
    except (ValueError, OverflowError):
        reasons = ["time_conversion_unsupported"]
    if session.session_date not in partition.training_sessions:
        reasons.append("session_not_training")
    if session.closes_at is not None and session.closes_at > cutoff:
        reasons.append("training_session_incomplete")
    return reasons


def _supplied_reasons(supplied, declared, partition, cutoff):
    """Require actual full receipt matches; reconcile only admitted economic copies."""
    reasons, seen = [], set()
    for group in supplied:
        reasons.extend(_session_reasons(group.session, partition, cutoff))
        matching = [g for g in declared if g.session == group.session]
        if not matching:
            reasons.append("supplied_session_unregistered")
        else:
            seen.add(_snapshot_hash(_calendar_snapshot(group.session)))
        for bar in group.bars:
            if not any(bar in g.bars for g in matching):
                reasons.append("supplied_bar_unregistered")
    for group in declared:
        if _snapshot_hash(_calendar_snapshot(group.session)) not in seen:
            reasons.append("declared_session_missing")
        actual = {identify_underlying_bar(bar).economic_content_hash for g in supplied if g.session == group.session for bar in g.bars if bar in group.bars}
        if any(identify_underlying_bar(bar).economic_content_hash not in actual for bar in group.bars):
            reasons.append("declared_bar_missing")
    return reasons


def _input_hash(partition, cutoff, evidence, canonical):
    """Commit exact declared identity and canonical selected/omitted economic facts."""
    sessions = sorted({row.economic_hash for row in evidence if row.bar is None and row.economic_hash is not None})
    return _snapshot_hash({
        "record_kind": "options_lab.volume_training_inputs", "schema_version": 1,
        "partition": {
            "partition_id": partition.partition_id, "record_id": partition.record_id,
            "raw_hash": partition.raw_hash, "input_manifest_id": partition.input_manifest_id,
            "input_manifest_hash": partition.input_manifest_hash,
            "training_sessions": [d.isoformat() for d in partition.training_sessions],
            "validation_sessions": [d.isoformat() for d in partition.validation_sessions],
            "test_sessions": [d.isoformat() for d in partition.test_sessions],
            "volume_definition_id": partition.volume_definition_id,
            "training_inputs": [[s, list(b)] for s, b in partition.training_inputs],
        },
        "cutoff": cutoff.isoformat(), "calendar_hashes": sessions,
        "selected_inputs": [{"session_date": row.session.session_date.isoformat(),
                             "minute_index": row.minute_index, "economic_hash": row.economic_hash,
                             "omission_reasons": list(row.omission_reasons)} for row in canonical],
        "selection": "distinct_regular_completed_training_days_no_imputation",
    })


def _calendar_snapshot(session):
    """Reuse P06's explicit calendar inventory, excluding only receipt locators."""
    snapshot = _session_snapshot(session)
    del snapshot["received_at"], snapshot["raw_ref"]
    return snapshot


def _member_reasons(member, kind):
    """Return finite reference resolution failures without inventing a member."""
    return ["unknown_member"] if member is None else ["member_kind_mismatch"] if member.kind != kind else []


def _envelope(member):
    """Extract the exact existing owner envelope from immutable admitted bytes."""
    env = member.decode_envelope()
    return dict(event_id=env["event_id"], raw_ref=env["raw_ref"],
                received_at=datetime.fromisoformat(env["simulated_received_at"].replace("Z", "+00:00")))


def _list(raw, field):
    """Reject non-exact list containers before iteration or comparisons."""
    if type(raw) is not list:
        _fail(field, "invalid_type")
    return raw


def _require_manifest(manifest):
    """Require the existing factory-derived fixture authority type."""
    if type(manifest) is not VerifiedFixtureManifest:
        raise TypeError("manifest must be a VerifiedFixtureManifest")


def _freeze(cls, **values):
    """Populate one private factory outcome from already validated concrete facts."""
    result = object.__new__(cls)
    for name, value in values.items():
        object.__setattr__(result, name, value)
    return result
