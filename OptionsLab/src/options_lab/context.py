"""Assemble causal current-market evidence from finite verified fixture streams."""

from dataclasses import dataclass, field, replace
from datetime import datetime
import hashlib
import json
from typing import Literal

from ._input_parsing import _InvalidInput, _parse_contract_id, _parse_date, _parse_string, _parse_timestamp
from ._validation import _require_nonempty_string, _trusted_datetime
from .admission import VerifiedFixtureManifest, VerifiedFixtureMember
from .coherence import (
    CoherenceAssessment, CoherenceInputRejection, QuoteCoherenceEvidence,
    assess_quote_coherence, normalize_quote_coherence,
)
from .config import StrategyConfig, config_hash, policy_hash
from .context_inputs import ContextInputRejection, ContextRequestValidation
from .contract_inputs import (
    ContractReferenceRejection, ProviderContractMappingRejection,
    normalize_contract_reference, normalize_provider_contract_mapping,
)
from .contracts import (
    ContractId, ContractReference, ContractReferenceAssessment, ProviderContractMapping,
    assess_contract_reference,
)
from .features import FeatureState, FeatureUpdate
from .greek_inputs import GreekInputRejection, normalize_greek_observation
from .greeks import GreekObservation, GreekReadiness, assess_greek_readiness
from .observations import InputRejection, ObservationMeta, normalize_observation_meta
from .quote_content import QuoteContentIdentity, identify_quote_content
from .quote_inputs import QuoteInputRejection, normalize_quote_observation
from .quotes import QuoteObservation, _assess_quote_only
from .session_inputs import (
    SessionInputRejection, normalize_exchange_session, normalize_instrument_tradability,
)
from .sessions import (
    EntryTimingAssessment, ExchangeSession, InstrumentTradability, SessionAssessment,
    assess_decision_slot, assess_session,
)
from .ticks import TickInputRejection, TickRule, normalize_tick_rule
from .underlying import UnderlyingQuote, assess_underlying_quote
from .underlying_inputs import UnderlyingQuoteInputRejection, normalize_underlying_quote


_INTEGRITY = (
    "manifest_missing", "unknown_member", "previous_state_unverified", "normalization_failed",
    "availability_unknown", "source_identity_unknown", "profile_mismatch", "target_unknown",
    "source_identity_conflict", "lineage_invalid", "lineage_unorderable", "lineage_cycle",
    "lineage_fork", "current_roots_conflict", "stream_order_ambiguous",
    "incomplete_context_input_set", "quote_pair_missing", "greek_coherence_missing",
)
_CONTRACT_CODES = {
    "contract": ("expected_exact_dict", "unknown_fields"),
    "contract.underlying": ("missing", "invalid_type", "invalid_value"),
    "contract.expiry": ("missing", "invalid_type", "invalid_date"),
    "contract.right": ("missing", "invalid_type", "invalid_value"),
    "contract.strike": ("missing", "invalid_type", "invalid_decimal"),
    "contract.multiplier": ("missing", "invalid_type"),
    "contract.deliverable_id": ("missing", "invalid_type", "invalid_value"),
}


@dataclass(frozen=True)
class ContextMemberRejection:
    """This class represents a bounded member prerequisite or assembly failure."""

    event_id: str
    received_at: datetime
    raw_ref: str
    field: str
    code: str

    def __post_init__(self) -> None:
        """
        Validate trusted facts and only emitted diagnostic pairs.

        :returns: None.
        :raises TypeError: If a trusted scalar has an incorrect exact type.
        :raises ValueError: If an identity, time, or diagnostic is invalid.
        """
        if type(self) is not ContextMemberRejection:
            raise TypeError("rejection must be an exact ContextMemberRejection")
        for name in ("event_id", "raw_ref", "field", "code"):
            _require_nonempty_string(name, getattr(self, name))
        object.__setattr__(self, "received_at", _trusted_datetime("received_at", self.received_at))
        if self.code not in (_INTEGRITY if self.field == "member" else _CONTRACT_CODES.get(self.field, ())):
            raise ValueError("rejection field and code are incompatible")

    @property
    def stage(self) -> Literal["context_assembly"]:
        """Return the fixed assembly stage.

        :returns: The context assembly stage.
        """
        return "context_assembly"

    @property
    def reasons(self) -> tuple[str, ...]:
        """Return the one bounded diagnostic reason.

        :returns: A one-item immutable code tuple.
        """
        return (self.code,)


MarketValue = (
    QuoteObservation | UnderlyingQuote | GreekObservation | QuoteCoherenceEvidence | TickRule
    | ExchangeSession | InstrumentTradability | ProviderContractMapping | ContractReference
)
ContextRejection = (
    ContextInputRejection | ContextMemberRejection | InputRejection | QuoteInputRejection
    | UnderlyingQuoteInputRejection | GreekInputRejection | CoherenceInputRejection
    | TickInputRejection | SessionInputRejection | ProviderContractMappingRejection
    | ContractReferenceRejection
)


@dataclass(frozen=True, init=False)
class ContextComponent:
    """This class represents one actual member, partial normalization, and disposition."""

    member: VerifiedFixtureMember
    requested: bool
    contract: ContractId | None
    metadata: ObservationMeta | None
    value: MarketValue | None
    available_at: datetime | None
    disposition: Literal["audit", "future", "duplicate", "superseded", "unresolved", "selected", "omitted"]
    reasons: tuple[str, ...]
    rejection_indexes: tuple[int, ...]
    quote_identity: QuoteContentIdentity | None

    def __init__(self) -> None:
        """Prevent caller-authored selection evidence.

        :returns: None.
        :raises TypeError: Always; use build_decision_context.
        """
        raise TypeError("ContextComponent values come from build_decision_context")


@dataclass(frozen=True, init=False)
class DecisionContext:
    """
    This class represents immutable causal market evidence, never trade authorization.

    Tick rules are complete known lineage tips, including adverse rules and advance
    schedules; applicability requires an actual-price consumer. The digest is absent
    when a supplied prior feature state cannot be rebound to admitted history.
    Status/reference tuples retain complete known tips and separate owner assessments,
    including schedules and adverse applicability. History remains explicitly absent.
    """

    decision_id: str
    decision_at: datetime
    slot_key: str | None
    timing: EntryTimingAssessment
    manifest: VerifiedFixtureManifest | None
    input_manifest_id: tuple[str, str] | None
    option_quotes: tuple[QuoteObservation, ...]
    underlying_quotes: tuple[UnderlyingQuote, ...]
    greeks: tuple[GreekObservation, ...]
    coherence_evidence: tuple[QuoteCoherenceEvidence, ...]
    tick_rules: tuple[TickRule, ...]
    greek_readiness: tuple[GreekReadiness, ...]
    coherence_assessments: tuple[CoherenceAssessment, ...]
    session: ExchangeSession | None
    tradability: tuple[InstrumentTradability, ...]
    provider_mappings: tuple[ProviderContractMapping, ...]
    contract_references: tuple[ContractReference, ...]
    session_assessments: tuple[SessionAssessment, ...]
    reference_assessments: tuple[ContractReferenceAssessment, ...]
    feature_state: None
    bars: tuple[()]
    rejections: tuple[ContextRejection, ...]
    input_reasons: tuple[str, ...]
    config_hash: str
    policy_hash: str
    input_digest: str | None
    origin: Literal["synthetic"]
    fidelity_tier: Literal[0]
    permitted_use: Literal["core_fixture"]
    operational_allowed: Literal[False]
    economic_allowed: Literal[False]

    def __init__(self) -> None:
        """Prevent caller-provided currentness, success, or digest authority.

        :returns: None.
        :raises TypeError: Always; use build_decision_context.
        """
        raise TypeError("DecisionContext values come from build_decision_context")


@dataclass(frozen=True, init=False)
class ContextBuildResult:
    """This class represents the full request audit and any representable context."""

    request: ContextRequestValidation
    manifest: VerifiedFixtureManifest | None
    context: DecisionContext | None
    components: tuple[ContextComponent, ...]
    rejections: tuple[ContextRejection, ...]
    feature_updates: tuple[FeatureUpdate, ...]
    previous_feature_state: FeatureState | None

    def __init__(self) -> None:
        """Prevent contradictory caller-authored partial assembly results.

        :returns: None.
        :raises TypeError: Always; use build_decision_context.
        """
        raise TypeError("ContextBuildResult values come from build_decision_context")

    @property
    def entry_input_failed(self) -> bool:
        """
        Report assembly failure without claiming that missing entry inputs exist.

        :returns: True for a missing header or retained causal input failures.
        """
        return self.context is None or bool(self.context.input_reasons)


@dataclass
class _Row:
    """This class represents private work for one contained member occurrence."""

    member: VerifiedFixtureMember
    envelope: dict
    body: dict
    requested: bool
    contract: ContractId | None = None
    metadata: ObservationMeta | None = None
    value: MarketValue | None = None
    available_at: datetime | None = None
    source: str | None = None
    source_id: str | None = None
    target: tuple | None = None
    failures: list = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    disposition: str = "audit"
    quote_identity: QuoteContentIdentity | None = None


def build_decision_context(
    request: ContextRequestValidation, manifest: VerifiedFixtureManifest | None, *,
    config: StrategyConfig, previous_feature_state: FeatureState | None,
) -> ContextBuildResult:
    """
    Resolve finite participating streams and retain all inspected member evidence.

    Requested records establish scope, not currentness. Proven future availability
    stays outside causal selection and hashing. Unknown current facts suppress
    affected selections; inspected omitted records never become selected values.
    A supplied prior state remains unverified audit evidence and prevents a digest;
    it is never silently reset. Missing later inputs do not claim entry readiness.

    :param request: Exact factory-normalized partial request and ingestion facts.
    :param manifest: Actual verified fixture, or explicit absent evidence.
    :param config: Exact validated strategy configuration.
    :param previous_feature_state: Prior state retained for audit, unverified here.
    :returns: Frozen partial result with native failures and a context when time exists.
    :raises TypeError: If a trusted argument has the wrong exact type.
    """
    if type(request) is not ContextRequestValidation:
        raise TypeError("request must be an exact ContextRequestValidation")
    if manifest is not None and type(manifest) is not VerifiedFixtureManifest:
        raise TypeError("manifest must be an exact VerifiedFixtureManifest or None")
    if type(config) is not StrategyConfig:
        raise TypeError("config must be an exact StrategyConfig")
    if previous_feature_state is not None and type(previous_feature_state) is not FeatureState:
        raise TypeError("previous_feature_state must be an exact FeatureState or None")
    chash, phash = config_hash(config), policy_hash(config)
    rejections = list(request.rejections)
    global_reasons = []
    members = {} if manifest is None else {m.record_id: m for m in manifest.members}
    if manifest is None:
        global_reasons.append("manifest_missing")
    for record_id in sorted(request.member_record_ids):
        if record_id not in members:
            global_reasons.append("unknown_member")
    if previous_feature_state is not None:
        global_reasons.append("previous_state_unverified")
    for reason in global_reasons:
        rejections.append(ContextMemberRejection(
            request.event_id, request.received_at, request.raw_ref, "member", reason,
        ))
    rows = _resolve(request, manifest, members)
    if request.value is not None:
        _select(rows, request.value.decision_at)
        _quote_evidence(rows, request.value.decision_at, config)
        for row in rows:
            if row.disposition == "selected" and type(row.value) is ExchangeSession:
                assessment = assess_session(row.value, None, config=config, now=request.value.decision_at)
                if any(reason in assessment.session_reasons for reason in (
                    "calendar_unsupported", "session_wrong_date",
                )) or "time_conversion_unsupported" in assessment.entry_reasons:
                    row.disposition = "audit"
    components = []
    causal_rejections = list(rejections)
    for row in rows:
        failures = row.failures + [
            _failure(row, "member", reason)
            for reason in dict.fromkeys(row.reasons) if reason in _INTEGRITY
        ]
        indexes = tuple(range(len(rejections), len(rejections) + len(failures)))
        rejections.extend(failures)
        if row.disposition != "future":
            causal_rejections.extend(failures)
        components.append(_freeze(
            ContextComponent, member=row.member, requested=row.requested,
            contract=row.contract, metadata=row.metadata, value=row.value,
            available_at=row.available_at, disposition=row.disposition,
            reasons=tuple(dict.fromkeys(row.reasons)), rejection_indexes=indexes,
            quote_identity=row.quote_identity,
        ))
    context = None
    if request.value is not None:
        context = _context(
            request, manifest, config, rows, tuple(causal_rejections), chash, phash,
            global_reasons,
        )
    return _freeze(
        ContextBuildResult, request=request, manifest=manifest, context=context,
        components=tuple(components), rejections=tuple(rejections), feature_updates=(),
        previous_feature_state=previous_feature_state,
    )


def _resolve(request, manifest, members) -> list[_Row]:
    """Normalize requested scope, finite stream siblings, and explicit linked members."""
    envelopes = {key: member.decode_envelope() for key, member in members.items()}
    bodies = {key: member.decode_raw_body() for key, member in members.items()}
    selected = set(request.member_record_ids) & members.keys()
    if request.value is not None:
        causal = {
            key for key in members
            if (at := _available(envelopes[key], bodies[key])) is None
            or at <= request.value.decision_at
        }
        # Future requests remain audit evidence and cannot introduce causal streams.
        streams = {envelopes[key]["stream_id"] for key in selected & causal}
        selected.update(key for key, env in envelopes.items() if env["stream_id"] in streams)
        while True:
            linked = set()
            for key in causal:
                parent = envelopes[key]["supersedes_record_id"]
                if key in selected or parent in selected & causal:
                    linked.update((key, parent))
            added = (linked & members.keys()) - selected
            if not added:
                break
            selected.update(added)
    profiles = {} if manifest is None else {
        p["profile_id"]: p for p in json.loads(manifest.modeled_source_profiles_bytes)
    }
    rows = []
    for key in sorted(selected):
        row = _Row(members[key], envelopes[key], bodies[key], key in request.member_record_ids)
        _normalize(row, profiles[row.member.profile_id])
        rows.append(row)
    return rows


def _normalize(row: _Row, profile: dict) -> None:
    """Dispatch to actual owners, parsing independent contract and metadata prerequisites."""
    kind, raw, env = row.member.kind, row.body, row.envelope
    kwargs = dict(
        event_id=env["event_id"], raw_ref=env["raw_ref"],
        received_at=_parse_timestamp(env["simulated_received_at"], "received_at"),
    )
    if kind not in ("underlying_quote", "exchange_session"):
        try:
            row.contract = _parse_contract_id(
                env["contract"] if env["contract"] is not None else raw.get("contract")
            )
        except _InvalidInput as failure:
            # Body-owning normalizers retain their own contract failure below.
            if kind in ("option_quote", "greek_observation"):
                row.failures.append(_failure(row, *failure.args))
    if kind in ("option_quote", "underlying_quote"):
        result = normalize_observation_meta(env["metadata"], **kwargs)
        row.metadata = result.value
        if result.rejection is not None:
            row.failures.append(result.rejection)
    result = None
    if kind == "option_quote" and row.contract is not None and row.metadata is not None:
        result = normalize_quote_observation(
            raw, contract=row.contract, meta=row.metadata, event_id=env["event_id"],
        )
    elif kind == "underlying_quote" and row.metadata is not None:
        result = normalize_underlying_quote(raw, meta=row.metadata, event_id=env["event_id"])
    elif kind == "greek_observation" and row.contract is not None:
        result = normalize_greek_observation(raw, contract=row.contract, **kwargs)
    elif kind == "quote_coherence":
        result = normalize_quote_coherence(raw, **kwargs)
    elif kind == "tick_rule":
        result = normalize_tick_rule(raw, **kwargs)
    elif kind == "exchange_session":
        result = normalize_exchange_session(raw, **kwargs)
    elif kind == "instrument_tradability":
        result = normalize_instrument_tradability(raw, **kwargs)
    elif kind == "provider_contract_mapping":
        result = normalize_provider_contract_mapping(raw, **kwargs)
    elif kind == "contract_reference":
        result = normalize_contract_reference(raw, **kwargs)
    if result is not None:
        row.value = result.value
        if result.rejection is not None:
            row.failures.append(result.rejection)
    facts = env["metadata"] if kind in ("option_quote", "underlying_quote") else raw
    row.available_at = _available(env, raw)
    row.source = _string(facts.get("provider" if kind == "provider_contract_mapping" else "source"))
    row.source_id = _string(facts.get("evidence_id" if kind == "quote_coherence" else "provider_record_id"))
    if kind == "provider_contract_mapping":
        row.source_id = env["event_id"]
        symbol = _string(raw.get("symbol"))
        row.target = (kind, row.source, symbol) if row.source and symbol else None
    elif kind in ("exchange_session", "instrument_tradability"):
        try:
            day = _parse_date(raw.get("session_date"), "session_date")
        except _InvalidInput:
            day = None
        identity = _string(raw.get("calendar")) if kind == "exchange_session" else (
            row.contract if raw.get("contract", {}) is not None else _string(raw.get("instrument_ref"))
        )
        row.target = (kind, identity, day) if identity is not None and day is not None else None
    elif kind == "underlying_quote":
        symbol = _string(raw.get("symbol"))
        row.target = (kind, symbol) if symbol is not None else None
    elif row.contract is not None:
        row.target = (kind, row.contract)
        if kind == "quote_coherence":
            protocol, method = _string(raw.get("protocol_id")), _string(raw.get("method"))
            row.target = (*row.target, protocol, method) if protocol and method else None
        elif kind == "tick_rule":
            definition = _string(raw.get("rule_id"))
            row.target = (*row.target, definition) if definition else None
    if row.value is None:
        row.reasons.append("normalization_failed")
    if row.available_at is None:
        row.reasons.append("availability_unknown")
    if row.source is None or row.source_id is None:
        row.reasons.append("source_identity_unknown")
    if row.target is None:
        row.reasons.append("target_unknown")
    claims = (
        ("source", "feed_class", "fidelity", "availability_basis")
        if row.metadata is not None else ("source",)
    )
    if kind in ("exchange_session", "instrument_tradability"):
        claims = ("source", "fidelity", "availability_basis")
    elif kind == "contract_reference":
        claims = ("source", "availability_basis")
    elif kind == "provider_contract_mapping":
        claims = ("availability_basis",)
    if row.source != profile["source"] or any(facts.get(name) != profile[name] for name in claims):
        row.reasons.append("profile_mismatch")
    if kind == "greek_observation" and facts.get("availability_basis") != profile["availability_basis"]:
        row.reasons.append("profile_mismatch")


def _select(rows: list[_Row], cutoff: datetime) -> None:
    """Resolve source duplicates, explicit causal graphs, and omitted current scope."""
    causal = {}
    for row in rows:
        if row.available_at is not None and row.available_at > cutoff:
            row.disposition = "future"
            row.reasons.append("available_after_decision")
        else:
            causal[row.member.record_id] = row
    aliases = {}
    identities = {}
    for key, row in causal.items():
        identity = (row.member.kind, row.envelope["stream_id"], row.source, row.source_id)
        if row.source is None or row.source_id is None:
            continue
        prior = identities.get(identity)
        if prior is None:
            identities[identity] = row
        elif _same_source(prior, row):
            aliases[key] = prior.member.record_id
            row.disposition = "duplicate"
        else:
            prior.reasons.append("source_identity_conflict")
            row.reasons.append("source_identity_conflict")
    for row in causal.values():
        identity = (row.member.kind, row.envelope["stream_id"], row.source, row.source_id)
        prior = identities.get(identity)
        if prior is not None and "source_identity_conflict" in prior.reasons:
            row.reasons.append("source_identity_conflict")
    # Prefer an actual requested occurrence when a source redelivery represents a tip.
    for original in sorted(set(aliases.values())):
        occurrences = [original, *(key for key, value in aliases.items() if value == original)]
        chosen = min((key for key in occurrences if causal[key].requested), default=original)
        for key in occurrences:
            if key == chosen:
                aliases.pop(key, None)
                causal[key].disposition = "audit"
            else:
                aliases[key] = chosen
                causal[key].disposition = "duplicate"
    active = {key: row for key, row in causal.items() if key not in aliases}
    edges = {key: set() for key in active}
    children = {key: set() for key in active}
    for key, row in active.items():
        parent = row.envelope["supersedes_record_id"]
        if parent is None:
            continue
        parent = aliases.get(parent, parent)
        if parent not in active:
            row.reasons.append("lineage_invalid")
            continue
        prior = active[parent]
        edges[key].add(parent)
        edges[parent].add(key)
        children[parent].add(key)
        if (
            key == parent or row.target != prior.target or row.target is None
            or row.source != prior.source
            or row.envelope["stream_id"] != prior.envelope["stream_id"]
        ):
            row.reasons.append("lineage_invalid")
        if not _later(row, prior):
            row.reasons.append("lineage_unorderable")
    for key, descendants in children.items():
        if len(descendants) > 1:
            active[key].reasons.append("lineage_fork")
    # ponytail: quadratic comparison is bounded to contained fixture records;
    # index stream/time buckets if an admitted live stream ever needs this path.
    ordered = list(active.values())
    for index, left in enumerate(ordered):
        for right in ordered[index + 1:]:
            if (
                left.envelope["stream_id"] == right.envelope["stream_id"]
                and left.available_at is not None
                and left.available_at == right.available_at
            ):
                a, b = left.envelope["receive_sequence"], right.envelope["receive_sequence"]
                if a is None or b is None or a == b:
                    left.reasons.append("stream_order_ambiguous")
                    right.reasons.append("stream_order_ambiguous")
    groups = []
    remaining = set(active)
    while remaining:
        group, pending = set(), {min(remaining)}
        while pending:
            key = pending.pop()
            if key not in group:
                group.add(key)
                pending.update(edges[key] - group)
        remaining -= group
        tips = [key for key in sorted(group) if not children[key]]
        if not tips:
            for key in group:
                active[key].reasons.append("lineage_cycle")
        groups.append((group, tips))
    # Unknown target/key or unlinked raw failure cannot declare an unaffected price band.
    for row in ordered:
        if (
            row.target is None or row.source is None or row.source_id is None
            or (row.value is None and not edges[row.member.record_id])
        ):
            for other in ordered:
                if other.member.kind == row.member.kind and (
                    (row.target is None and (
                        # A mapping can change contract; it cannot bound an unknown provider/symbol.
                        row.member.kind == "provider_contract_mapping"
                        or row.contract is None or row.contract == other.contract
                    ))
                    or (row.target is not None and row.target == other.target)
                ):
                    other.reasons.append("target_unknown")
    for index, (group, tips) in enumerate(groups):
        targets = {active[key].target for key in group}
        for other_group, _ in groups[index + 1:]:
            common = targets & {active[key].target for key in other_group}
            if any(target is not None and target[0] not in ("tick_rule", "instrument_tradability", "contract_reference") for target in common):
                for key in group | other_group:
                    active[key].reasons.append("current_roots_conflict")
        for key in tips:
            if not active[key].requested:
                active[key].reasons.append("incomplete_context_input_set")
                # Missing a separate scoped tip invalidates the complete target set.
                for other in ordered:
                    if other.target == active[key].target:
                        other.reasons.append("incomplete_context_input_set")
    for group, tips in groups:
        unresolved = any(active[key].reasons for key in group)
        for key in group:
            row = active[key]
            if unresolved:
                row.disposition = "unresolved" if row.requested else "omitted"
            elif key in tips:
                row.disposition = "selected"
            else:
                row.disposition = "superseded"


def _same_source(left: _Row, right: _Row) -> bool:
    """Compare complete source content, excluding only actual ingestion fields."""
    if left.envelope["supersedes_record_id"] != right.envelope["supersedes_record_id"]:
        return False
    a, b = left.value, right.value
    if a is None or b is None:
        return (
            left.body == right.body
            and left.envelope["metadata"] == right.envelope["metadata"]
            and left.envelope["contract"] == right.envelope["contract"]
        )
    if type(a) is not type(b):
        return False
    if type(a) in (QuoteObservation, UnderlyingQuote):
        return replace(a, meta=replace(a.meta, received_at=b.meta.received_at, raw_ref=b.meta.raw_ref)) == b
    if type(a) in (GreekObservation, ExchangeSession, InstrumentTradability):
        return replace(a, received_at=b.received_at, raw_ref=b.raw_ref) == b
    return replace(a, raw_ref=b.raw_ref) == b


def _later(row: _Row, prior: _Row) -> bool:
    """Compare availability first, then known increasing same-stream sequence."""
    if row.available_at is None or prior.available_at is None:
        return False
    if row.available_at != prior.available_at:
        return row.available_at > prior.available_at
    a, b = row.envelope["receive_sequence"], prior.envelope["receive_sequence"]
    return row.envelope["stream_id"] == prior.envelope["stream_id"] and a is not None and b is not None and a > b


def _quote_evidence(rows, cutoff, config) -> None:
    """Retain bounded identities and owner market reasons without changing chronology."""
    for row in rows:
        if type(row.value) not in (QuoteObservation, UnderlyingQuote):
            continue
        row.quote_identity = identify_quote_content(row.value)
        if row.disposition == "future":
            continue
        row.reasons.extend(row.quote_identity.reasons)
        if type(row.value) is QuoteObservation:
            observation, reasons = _assess_quote_only(
                row.value, decision_at=cutoff, max_quote_age=config.execution.max_quote_age,
            )
        else:
            result = assess_underlying_quote(
                row.value, decision_at=cutoff, max_quote_age=config.execution.max_quote_age,
            )
            observation, reasons = result.observation, result.quote_reasons
        if row.disposition == "selected":
            row.reasons.extend(observation.live_quote_reasons)
            row.reasons.extend(reasons)


def _context(request, manifest, config, rows, rejections, chash, phash, global_reasons):
    """Project selected facts and bind actual causal commitments with owner identities."""
    header = request.value
    selected = [row.value for row in rows if row.disposition == "selected"]
    options = tuple(value for value in selected if type(value) is QuoteObservation)
    underlying = tuple(value for value in selected if type(value) is UnderlyingQuote)
    greeks = tuple(value for value in selected if type(value) is GreekObservation)
    proofs = tuple(value for value in selected if type(value) is QuoteCoherenceEvidence)
    ticks = tuple(value for value in selected if type(value) is TickRule)
    sessions = [value for value in selected if type(value) is ExchangeSession]
    session = sessions[0] if len(sessions) == 1 else None
    statuses = tuple(value for value in selected if type(value) is InstrumentTradability)
    mappings = tuple(value for value in selected if type(value) is ProviderContractMapping)
    references = tuple(value for value in selected if type(value) is ContractReference)
    session_assessments = tuple(
        assess_session(session, status, config=config, now=header.decision_at)
        for status in (statuses or (None,))
    ) if session is not None or statuses else ()
    reference_assessments = []
    for reference in references:
        compatible = [mapping for mapping in mappings if mapping.contract == reference.contract]
        if not compatible and len(mappings) == 1 and len({r.contract for r in references}) == 1:
            # One supplied target pair can expose a mismatch without decoding its symbol.
            compatible = list(mappings)
        if len(compatible) == 1:
            reference_assessments.append(assess_contract_reference(
                compatible[0], reference, decision_at=header.decision_at,
            ))
    readiness, coherence = [], []
    reasons = list(global_reasons)
    for failure in request.rejections:
        reasons.extend(failure.reasons)
    for row in rows:
        if row.disposition != "future":
            reasons.extend(row.reasons)
            for failure in row.failures:
                reasons.extend(failure.reasons)
    for value in (*greeks, *proofs):
        option_pair = [q for q in options if q.contract == value.contract]
        underlying_pair = [q for q in underlying if q.symbol == value.contract.underlying]
        if len(option_pair) != 1 or len(underlying_pair) != 1:
            reasons.append("quote_pair_missing")
            continue
        if type(value) is GreekObservation:
            assessment = assess_greek_readiness(
                value, option_pair[0], underlying_pair[0], method=manifest.greek_method,
                decision_at=header.decision_at,
            )
            readiness.append(assessment)
        else:
            assessment = assess_quote_coherence(
                value, option_pair[0], underlying_pair[0], manifest=manifest,
                decision_at=header.decision_at, max_quote_age=config.execution.max_quote_age,
            )
            coherence.append(assessment)
        reasons.extend(assessment.reasons)
    for greek in greeks:
        if greek.as_of is None or not any(
            proof.evidence.contract == greek.contract and proof.supports_instant(greek.as_of)
            for proof in coherence
        ):
            reasons.append("greek_coherence_missing")
    reasons = tuple(dict.fromkeys(reasons))
    timing = assess_decision_slot(
        session, None, config=config, decision_at=header.decision_at, now=header.decision_at,
    )
    manifest_id = None if manifest is None else (manifest.fixture_id, manifest.payload_sha256)
    known_ids = {m.record_id for m in manifest.members} if manifest else set()
    snapshot = {
        "domain": "options_lab.decision_context.v1", "decision_id": header.decision_id,
        "decision_at": header.decision_at.isoformat(), "config_hash": chash,
        "policy_hash": phash, "input_manifest_id": manifest_id,
        "slot_key": timing.slot_key, "input_reasons": reasons,
        "request_rejections": [(r.field, r.code) for r in request.rejections],
        "unknown_member_ids": sorted(set(request.member_record_ids) - known_ids),
        "members": [{
            "record_id": r.member.record_id, "profile_id": r.member.profile_id,
            "kind": r.member.kind, "body": r.member.raw_body_bytes.decode("ascii"),
            "envelope": r.member.envelope_bytes.decode("ascii"),
            "requested": r.requested, "disposition": r.disposition,
            "reasons": tuple(dict.fromkeys(r.reasons)),
            "quote_content_hash": None if r.quote_identity is None else r.quote_identity.content_hash,
            "normalization_rejections": [(
                failure.stage,
                [(d.field, d.code) for d in failure.diagnostics]
                if type(failure) is InputRejection else [(failure.field, failure.code)],
            ) for failure in r.failures],
        } for r in rows if r.disposition != "future"],
    }
    digest = hashlib.sha256(json.dumps(
        snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False,
    ).encode("ascii")).hexdigest()
    if "previous_state_unverified" in global_reasons:
        digest = None
    return _freeze(
        DecisionContext, decision_id=header.decision_id, decision_at=header.decision_at,
        slot_key=timing.slot_key, timing=timing, manifest=manifest, input_manifest_id=manifest_id,
        option_quotes=options, underlying_quotes=underlying, greeks=greeks,
        coherence_evidence=proofs, tick_rules=ticks,
        greek_readiness=tuple(readiness), coherence_assessments=tuple(coherence), session=session, tradability=statuses,
        provider_mappings=mappings, contract_references=references,
        session_assessments=session_assessments, reference_assessments=tuple(reference_assessments),
        feature_state=None, bars=(), rejections=rejections,
        input_reasons=reasons, config_hash=chash, policy_hash=phash, input_digest=digest, origin="synthetic",
        fidelity_tier=0, permitted_use="core_fixture", operational_allowed=False, economic_allowed=False,
    )


def _failure(row, path, code):
    """Associate a closed diagnostic with the actual member ingestion envelope."""
    env = row.envelope
    return ContextMemberRejection(
        env["event_id"], _parse_timestamp(env["simulated_received_at"], "received_at"),
        env["raw_ref"], path, code,
    )


def _string(value):
    """Read only one safe source identity scalar without repairing missing claims."""
    try:
        return _parse_string(value, "identity")
    except _InvalidInput:
        return None


def _available(envelope, body):
    """Read actual availability independently; missing or malformed never means future."""
    facts = envelope["metadata"] if envelope["metadata"] is not None else body
    try:
        return _parse_timestamp(facts.get("available_at"), "available_at", nullable=True)
    except _InvalidInput:
        return None


def _freeze(cls, **values):
    """Construct only private derived context facts after assembly has finished."""
    result = object.__new__(cls)
    for name, value in values.items():
        object.__setattr__(result, name, value)
    return result
