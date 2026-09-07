"""Complete immutable fixture vectors from original causal context and frozen statistics."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, DecimalException, Inexact
import hashlib
import json
import re

from ._input_parsing import _InvalidInput, _fail, _parse_string, _require_shape
from ._validation import _require_nonempty_string, _trusted_datetime
from .admission import _canonical_bytes
from .bar_inputs import _identity_decimal, _UnsupportedIdentity
from .bars import BarAssessment, assess_underlying_bar
from .coherence import CoherenceAssessment, assess_quote_coherence
from .config import _MAX_DECIMAL_DIGITS, _snapshot_hash, config_hash, policy_hash
from .context import ContextComponent, DecisionContext
from .contracts import ContractId, ContractReferenceAssessment, assess_contract_reference
from .feature_math import (
    NUMERIC_CONVENTION_ID, RETURN_TRANSFORM_ID, ReturnFeatureResult,
    _checked, _context, _log_ratio, _numeric_snapshot, calculate_return_features,
)
from .greeks import FIXTURE_GREEK_METHOD, GreekObservation, GreekReadiness, _method_hash, assess_greek_readiness
from .quote_content import _option_snapshot, identify_quote_content
from .quotes import QuoteObservation
from .sessions import InstrumentTradability, SessionAssessment, assess_decision_slot, assess_session
from .volume import VOLUME_NORMALIZATION_ID
from .volume_normalization import FeatureNormalization
from .vwap_features import (
    CLOSE_VOLUME_PROXY_TRANSFORM_ID, ELIGIBLE_VOLUME_DEFINITION_ID, EXACT_VWAP_TRANSFORM_ID,
    VwapFeatureResult, calculate_vwap_feature,
)


_NAMES = (
    "log_return_1m", "log_return_5m", "log_return_15m", "log_return_30m",
    "realized_vol_5m", "realized_vol_15m", "realized_vol_30m", "session_vwap_log_distance",
    "minute_volume", "minute_volume_zscore", "underlying_spread_fraction", "minutes_from_open",
    "minutes_to_common_close", "option_right", "log_moneyness", "calendar_dte",
    "option_bid", "option_ask", "option_spread_fraction", "option_quote_age_seconds", "delta", "iv",
)
_UNITS = ("natural_log_ratio",) * 4 + ("unannualized_sqrt_sum_squared_logs",) * 3 + (
    "natural_log_ratio", "eligible_shares", "population_zscore", "fraction", "minutes", "minutes",
    "call_+1_put_-1", "natural_log_ratio", "calendar_days", "USD_per_share", "USD_per_share",
    "fraction", "seconds", "signed_option_price_per_underlying_price", "annualized_volatility_fraction",
)
_MODE_EXCLUSIONS = {
    "exact_trade_dollars": ("volume_missing", "volume_definition_unknown"),
    "close_volume_proxy": ("vwap_definition_unknown", "vwap_numerator_missing", "vwap_denominator_missing",
                           "vwap_numerator_without_volume", "vwap_numerator_nonpositive"),
}


@dataclass(frozen=True, init=False)
class FeatureSpec:
    """This class represents one of two code-owned complete numerical definitions."""

    mode: str
    feature_names: tuple[str, ...]
    feature_units: tuple[str, ...]
    fidelity: str
    feature_schema_id: str
    transform_id: str

    def __init__(self) -> None:
        """Reject caller-defined formulas or identities.

        :returns: None.
        :raises TypeError: Always; select a code-owned constant.
        """
        raise TypeError("FeatureSpec values are code-owned constants")

    @property
    def schema_snapshot(self) -> dict[str, object]:
        """Return fresh ordered field, unit, encoding and missingness semantics.

        :returns: Explicit complete numerical schema.
        """
        return {"record_kind": "options_lab.feature_schema", "schema_version": 1,
                "family": "spy-completed-minute-minimal-v2", "mode": self.mode, "fidelity": self.fidelity,
                "fields": [{"name": n, "unit": u, "encoding": "canonical_finite_decimal"}
                           for n, u in zip(self.feature_names, self.feature_units)],
                "missingness": "all_22_or_unavailable_no_imputation"}

    @property
    def transform_snapshot(self) -> dict[str, object]:
        """Return explicit arithmetic, currentness and source-definition commitments.

        :returns: Complete concrete transform without containing package identities.
        """
        method = FIXTURE_GREEK_METHOD
        return {"record_kind": "options_lab.feature_transform", "schema_version": 1,
                "schema": self.schema_snapshot, "numeric": _numeric_snapshot(), "numeric_id": NUMERIC_CONVENTION_ID,
                "return_transform_id": RETURN_TRANSFORM_ID,
                "vwap_transform_id": EXACT_VWAP_TRANSFORM_ID if self.mode == "exact_trade_dollars" else CLOSE_VOLUME_PROXY_TRANSFORM_ID,
                "volume_definition_id": ELIGIBLE_VOLUME_DEFINITION_ID, "normalization_id": VOLUME_NORMALIZATION_ID,
                "current_volume_source": "same_actual_training_source_feed_fidelity_availability_basis",
                "source_gate": "actual_selected_P08_members_current_P07_Greek_and_P08_coherence",
                "excluded_readiness_reasons": list(_MODE_EXCLUSIONS[self.mode]),
                "spot": "exact_current_underlying_bid_plus_ask_divided_by_2_raw",
                "close": "raw_completed_minute_close", "strike": "exact_raw_contract_strike",
                "endpoint": "original_decision_exact_completed_minute_no_lag_full_session_vwap_prefix",
                "scheduled_identity": "original_P04_nonnull_slot_key_equal_context_now_equal_decision",
                "zscore": "working80(current_volume_minus_stored_mean)/stored_stddev_then_output34_no_refit",
                "spread": "exact_side_difference_divided_by_own_exact_midpoint_work80_output34",
                "time": "integer_timedelta_microseconds_divided_by_60000000_minutes_or_1000000_seconds",
                "common_close": "minimum_actual_current_P04_common_close_across_applicable_statuses",
                "moneyness": "working80_ln(S/strike)_output34_both_rights",
                "dte": "expiry_date_minus_authoritative_session_date_calendar_days",
                "quote_age": "original_decision_minus_option_source_event_at_not_receipt_or_sides",
                "observed_values": "volume_bid_ask_delta_iv_exact_unrounded",
                "greek_method_hash": _method_hash(method), "delta_unit": method.delta_unit, "iv_unit": method.iv_unit,
                "option_price_basis": method.option_price_basis, "underlying_price_basis": method.underlying_price_basis}


def _freeze(cls, **fields):
    """Create a concrete immutable result only from this owner's derived facts."""
    result = object.__new__(cls)
    for name, value in fields.items():
        object.__setattr__(result, name, value)
    return result


def _spec(mode):
    """Construct one complete fixed definition and derive both actual identities."""
    names = _NAMES if mode == "exact_trade_dollars" else (*_NAMES[:7], "session_close_volume_proxy_log_distance", *_NAMES[8:])
    spec = _freeze(FeatureSpec, mode=mode, feature_names=names, feature_units=_UNITS,
                   fidelity="exact_supplied_trade_contributions" if mode == "exact_trade_dollars" else "close_volume_proxy")
    object.__setattr__(spec, "feature_schema_id", _snapshot_hash(spec.schema_snapshot))
    object.__setattr__(spec, "transform_id", _snapshot_hash(spec.transform_snapshot))
    return spec


EXACT_VWAP_SPEC = _spec("exact_trade_dollars")
CLOSE_VOLUME_PROXY_SPEC = _spec("close_volume_proxy")
FEATURE_SCHEMA_ID, TRANSFORM_ID = EXACT_VWAP_SPEC.feature_schema_id, EXACT_VWAP_SPEC.transform_id
PROXY_FEATURE_SCHEMA_ID, PROXY_TRANSFORM_ID = CLOSE_VOLUME_PROXY_SPEC.feature_schema_id, CLOSE_VOLUME_PROXY_SPEC.transform_id


@dataclass(frozen=True, init=False)
class FeatureVector:
    """This class represents a complete frozen22-value observation, never permission."""

    context: DecisionContext
    quote: QuoteObservation
    greek: GreekObservation
    spec: FeatureSpec
    normalization: FeatureNormalization
    values: tuple[Decimal, ...]
    contract: ContractId
    decision_id: str
    decision_at: datetime
    required_bar_endpoint: datetime
    feature_schema_id: str
    transform_id: str
    normalization_hash: str
    config_hash: str
    policy_hash: str
    input_manifest_id: tuple[str, str]
    context_input_digest: str
    available_at: datetime
    source_components: tuple[ContextComponent, ...]
    return_features: ReturnFeatureResult
    vwap_feature: VwapFeatureResult
    greek_readiness: GreekReadiness
    input_bytes: bytes
    input_hash: str
    content_hash: str
    origin: str
    fidelity_tier: int
    permitted_use: str
    operational_allowed: bool
    economic_allowed: bool

    def __init__(self) -> None:
        """Prevent caller-written successful values, readiness or hash authority.

        :returns: None.
        :raises TypeError: Always; use build_features.
        """
        raise TypeError("FeatureVector values come from build_features")

    @property
    def input_snapshot(self) -> dict[str, object]:
        """Return fresh explicit input fields from the originally finalized bytes.

        :returns: Canonical semantic dependencies, original times and real roots.
        """
        return json.loads(self.input_bytes)

    @property
    def snapshot(self) -> dict[str, object]:
        """Return complete canonical output fields excluding their own content hash.

        :returns: Fresh exact ordered values with actual dependency commitments.
        """
        return {"record_kind": "options_lab.feature_vector", "schema_version": 1,
                "inputs": self.input_snapshot, "input_hash": self.input_hash,
                "values": [_identity_decimal(v) for v in self.values], "available_at": self.available_at.isoformat(),
                "origin": self.origin, "fidelity_tier": self.fidelity_tier, "permitted_use": self.permitted_use,
                "operational_allowed": self.operational_allowed, "economic_allowed": self.economic_allowed,
                "fidelity": self.spec.fidelity}


@dataclass(frozen=True, init=False)
class FeatureResult:
    """This class represents full reached arithmetic and actual owner gate evidence."""

    context: DecisionContext
    quote: QuoteObservation
    greek: GreekObservation | None
    now: datetime
    spec: FeatureSpec
    normalization: FeatureNormalization
    return_features: ReturnFeatureResult | None
    vwap_feature: VwapFeatureResult | None
    greek_readiness: GreekReadiness | None
    coherence_assessments: tuple[CoherenceAssessment, ...]
    session_assessments: tuple[SessionAssessment, ...]
    reference_assessments: tuple[ContractReferenceAssessment, ...]
    endpoint_bar_assessment: BarAssessment | None
    source_components: tuple[ContextComponent, ...]
    reasons: tuple[str, ...]
    vector: FeatureVector | None

    def __init__(self) -> None:
        """Prevent caller-forged assessment outcomes.

        :returns: None.
        :raises TypeError: Always; use build_features.
        """
        raise TypeError("FeatureResult values come from build_features")


def build_features(context: DecisionContext, quote: QuoteObservation, greek: GreekObservation | None, *,
                   now: datetime, spec: FeatureSpec, normalization: FeatureNormalization) -> FeatureResult:
    """Build all22 required values at the original decision or retain cash evidence.

    :param context: Exact admitted causal context from P08.
    :param quote: Actual selected current option quote object.
    :param greek: Actual selected co-valued Greek object, or explicit absence.
    :param now: Original aware decision instant, never a fresh submission clock.
    :param spec: One of the two code-owned complete definitions.
    :param normalization: Actual P10B factory-derived frozen artifact.
    :returns: Complete immutable vector or all reached component/gate failures.
    :raises TypeError: If any trusted argument has an unexpected exact type.
    :raises ValueError: If now is naive or unrepresentable.
    """
    for name, value, cls in (("context", context, DecisionContext), ("quote", quote, QuoteObservation),
                             ("spec", spec, FeatureSpec), ("normalization", normalization, FeatureNormalization)):
        if type(value) is not cls:
            raise TypeError(name + " has an unexpected exact type")
    if greek is not None and type(greek) is not GreekObservation:
        raise TypeError("greek must be a GreekObservation or None")
    now = _trusted_datetime("now", now)
    decision, state, manifest = context.decision_at, context.feature_state, context.manifest
    config = context.timing.session_assessment.config
    reasons = []
    if now != decision:
        reasons.append("original_decision_mismatch")
    supported = spec is EXACT_VWAP_SPEC or spec is CLOSE_VOLUME_PROXY_SPEC
    if not supported:
        reasons.append("unsupported_spec")
    if manifest is None or context.input_digest is None:
        reasons.append("context_identity_missing")
    elif context.input_manifest_id != (manifest.fixture_id, manifest.payload_sha256):
        reasons.append("context_manifest_mismatch")
    chash, phash = config_hash(config), policy_hash(config)
    if (chash, phash) != (context.config_hash, context.policy_hash):
        reasons.append("context_config_mismatch")
    if not any(q is quote for q in context.option_quotes):
        reasons.append("quote_not_selected")
    selected_greeks = tuple(g for g in context.greeks if g.contract == quote.contract)
    if greek is None or len(selected_greeks) != 1 or selected_greeks[0] is not greek:
        reasons.append("greek_not_selected")
    underlying = tuple(q for q in context.underlying_quotes if q.symbol == quote.contract.underlying)
    spot = underlying[0] if len(underlying) == 1 else None
    if spot is None:
        reasons.append("underlying_not_selected")
    returns = None if state is None else calculate_return_features(state, endpoint=decision)
    vwap = None if state is None or spot is None or not supported else calculate_vwap_feature(state, spot, endpoint=decision, mode=spec.mode)
    endpoint = None if state is None or not state.bars or state.bars[-1].interval_end != decision else assess_underlying_bar(state.bars[-1], context.session, as_of=decision)
    if endpoint is None:
        reasons.append("required_bar_endpoint_missing")
    else:
        reasons.extend((*endpoint.availability_reasons, *endpoint.price_history_reasons, *endpoint.volume_reasons))
        if endpoint.bar.volume_definition_id != ELIGIBLE_VOLUME_DEFINITION_ID:
            reasons.append("volume_definition_unsupported")
    exclusions = _MODE_EXCLUSIONS[spec.mode] if supported else ()
    reasons.extend(r for r in context.input_reasons if r not in exclusions)
    for component in (returns, vwap):
        if component is not None:
            reasons.extend(component.reasons)
    readiness = None if manifest is None or spot is None else assess_greek_readiness(greek, quote, spot, method=manifest.greek_method, decision_at=decision)
    proofs = tuple(p for p in context.coherence_evidence if p.contract == quote.contract)
    coherence = () if manifest is None or spot is None else tuple(assess_quote_coherence(
        p, quote, spot, manifest=manifest, decision_at=decision, max_quote_age=config.execution.max_quote_age)
        for p in (proofs or (None,)))
    if readiness is not None:
        reasons.extend(readiness.reasons)
    for assessment in coherence:
        reasons.extend(assessment.reasons)
    if greek is None or greek.as_of is None or not any(a.supports_instant(greek.as_of) for a in coherence):
        reasons.append("greek_coherence_missing")
    sessions, references, required, current_reasons = _current(context, quote, config)
    reasons.extend(current_reasons)
    required = (*required, quote, greek, spot, *proofs, *(() if state is None else state.bars))
    components = tuple(c for c in context.selected_components if any(c.value is value for value in required if value is not None))
    if any(not any(c.value is value for c in components) for value in required if value is not None):
        reasons.append("source_membership_missing")
    volume_bucket, normalization_reasons = _normalization(normalization, context, endpoint)
    reasons.extend(normalization_reasons)
    result = _freeze(FeatureResult, context=context, quote=quote, greek=greek, now=now, spec=spec,
                     normalization=normalization, return_features=returns, vwap_feature=vwap, greek_readiness=readiness,
                     coherence_assessments=coherence, session_assessments=sessions, reference_assessments=references,
                     endpoint_bar_assessment=endpoint, source_components=components, reasons=(), vector=None)
    if not reasons:
        try:
            if spec.feature_schema_id != _snapshot_hash(spec.schema_snapshot) or spec.transform_id != _snapshot_hash(spec.transform_snapshot):
                reasons.append("spec_identity_mismatch")
            else:
                values = (*returns.values, vwap.distance, *_remaining(result, volume_bucket))
                if len(values) != 22 or any(type(v) is not Decimal or not v.is_finite() for v in values):
                    raise _UnsupportedIdentity
                times = tuple(c.available_at for c in components) + (normalization.available_at,)
                if any(t is None or t > decision for t in times):
                    reasons.append("required_availability_unusable")
                else:
                    inputs = _canonical_bytes(_input_snapshot(result, chash, phash))
                    vector = _freeze(FeatureVector, context=context, quote=quote, greek=greek, spec=spec,
                                     normalization=normalization, values=values, contract=quote.contract,
                                     decision_id=context.decision_id, decision_at=decision, required_bar_endpoint=decision,
                                     feature_schema_id=spec.feature_schema_id, transform_id=spec.transform_id,
                                     normalization_hash=normalization.content_hash, config_hash=chash, policy_hash=phash,
                                     input_manifest_id=context.input_manifest_id, context_input_digest=context.input_digest,
                                     available_at=max(times), source_components=components, return_features=returns,
                                     vwap_feature=vwap, greek_readiness=readiness, input_bytes=inputs,
                                     input_hash=hashlib.sha256(inputs).hexdigest(), origin="synthetic", fidelity_tier=0,
                                     permitted_use="core_fixture", operational_allowed=False, economic_allowed=False)
                    object.__setattr__(vector, "content_hash", _snapshot_hash(vector.snapshot))
                    object.__setattr__(result, "vector", vector)
        except (DecimalException, _UnsupportedIdentity, MemoryError, RecursionError):
            reasons.append("arithmetic_precision_unsupported")
    object.__setattr__(result, "reasons", tuple(dict.fromkeys(reasons)))
    return result


def _applicable(value, decision):
    """Exclude only demonstrably outside valid effective intervals; retain unknowns."""
    start, end = value.effective_from, value.effective_until
    if start is None or (type(value) is InstrumentTradability and end is None) or (end is not None and end <= start):
        return True
    return not (decision < start or (end is not None and decision >= end))


def _current(context, quote, config):
    """Reassess every relevant current hours/reference root with existing owners."""
    decision, reasons = context.decision_at, []
    timing = assess_decision_slot(context.session, None, config=config, decision_at=decision, now=decision)
    if timing.slot_key is None or timing.slot_key != context.slot_key:
        reasons.append("original_slot_invalid")
    statuses = tuple(s for s in context.tradability if s.contract in (None, quote.contract) and _applicable(s, decision))
    sessions = tuple(assess_session(context.session, s, config=config, now=decision) for s in (statuses or (None,)))
    for assessment in sessions:
        reasons.extend((*assessment.session_reasons, *assessment.instrument_hours_reasons, *assessment.operability_reasons))
        if assessment.common_opens_at is None or assessment.common_closes_at is None:
            reasons.append("common_hours_missing")
        elif not assessment.common_opens_at <= decision < assessment.common_closes_at:
            reasons.append("outside_common_hours")
    mappings = tuple(m for m in context.provider_mappings if m.contract == quote.contract)
    refs = tuple(r for r in context.contract_references if r.contract == quote.contract and _applicable(r, decision))
    if not mappings or not refs:
        reasons.append("contract_reference_missing")
    assessments = tuple(assess_contract_reference(m, r, decision_at=decision) for m in mappings for r in refs)
    for assessment in assessments:
        reasons.extend(assessment.reasons)
    return sessions, assessments, (context.session, *statuses, *mappings, *refs), reasons


def _normalization(normalization, context, endpoint):
    """Check actual frozen identities and current-session compatibility without fitting."""
    baseline, reasons = normalization.baseline, []
    try:
        if baseline.content_hash != _snapshot_hash(baseline.snapshot) or normalization.content_hash != _snapshot_hash(normalization.snapshot):
            reasons.append("normalization_content_mismatch")
        member = next((m for m in normalization.manifest.members if m.record_id == normalization.record_id), None)
        partition = next((m for m in baseline.manifest.members if m.record_id == baseline.partition.record_id), None)
        if member is None or member.kind != "feature_normalization" or member.raw_hash != normalization.raw_hash:
            reasons.append("normalization_member_mismatch")
        else:
            raw = member.decode_raw_body()
            if (raw["baseline_snapshot"] != baseline.snapshot or raw["baseline_content_hash"] != baseline.content_hash
                    or raw["training_fixture_id"] != baseline.manifest.fixture_id
                    or raw["training_payload_sha256"] != baseline.manifest.payload_sha256):
                reasons.append("normalization_binding_mismatch")
        if partition is None or partition.kind != "volume_partition" or partition.raw_hash != baseline.partition.raw_hash:
            reasons.append("partition_member_mismatch")
        if (baseline.partition.input_manifest_id, baseline.partition.input_manifest_hash) != (baseline.manifest.fixture_id, baseline.manifest.payload_sha256):
            reasons.append("training_manifest_mismatch")
    except (_UnsupportedIdentity, MemoryError, RecursionError):
        reasons.append("arithmetic_precision_unsupported")
    if (baseline.numeric_id, baseline.normalization_id, baseline.volume_definition_id) != (NUMERIC_CONVENTION_ID, VOLUME_NORMALIZATION_ID, ELIGIBLE_VOLUME_DEFINITION_ID):
        reasons.append("normalization_definition_mismatch")
    if normalization.availability_basis != "measured" or not baseline.cutoff <= normalization.available_at <= context.decision_at:
        reasons.append("normalization_availability_unusable")
    session = context.session
    bucket = None
    if session is None or session.opens_at is None:
        reasons.append("normalization_session_missing")
    else:
        if baseline.cutoff >= session.opens_at or any(d >= session.session_date for d in baseline.partition.training_sessions):
            reasons.append("normalization_training_not_prior")
        if any(d >= session.session_date for b in baseline.buckets for d in b.contributing_sessions):
            reasons.append("normalization_contributor_not_prior")
        elapsed = context.decision_at - session.opens_at
        if elapsed <= timedelta(0) or elapsed % timedelta(minutes=1):
            reasons.append("normalization_minute_invalid")
        else:
            minute = elapsed // timedelta(minutes=1)
            bucket = next((b for b in baseline.buckets if b.minute_index == minute), None)
        if bucket is None:
            reasons.append("normalization_bucket_missing")
        elif bucket.reasons or bucket.sample_count < 20 or bucket.mean is None or bucket.population_stddev is None or bucket.population_stddev <= 0:
            reasons.append("normalization_bucket_unready")
    if endpoint is not None:
        training_ids = {record for _, records in baseline.partition.training_inputs for record in records}
        sample = next((m for m in baseline.manifest.members if m.record_id in training_ids and m.kind == "underlying_bar"), None)
        if sample is None:
            reasons.append("normalization_source_missing")
        else:
            meta = sample.decode_envelope()["metadata"]
            if any(getattr(endpoint.bar.meta, key) != meta[key] for key in ("source", "feed_class", "fidelity", "availability_basis")):
                reasons.append("normalization_source_mismatch")
    return bucket, reasons


def _remaining(result, bucket):
    """Compute the remaining14 fields using exact facts and the fixed numeric order."""
    exact, work, output = _context(_MAX_DECIMAL_DIGITS), _context(80), _context(34)
    exact.traps[Inexact] = True
    quote, spot, decision = result.quote, result.vwap_feature.underlying_quote, result.context.decision_at
    def derived(value):
        """Round one complete supported derived quantity once."""
        return _checked(output.plus(_checked(value, work)), output)
    def spread(q):
        """Compute each quote's own exact numerator and midpoint denominator."""
        bid, ask = Decimal(_identity_decimal(q.bid)), Decimal(_identity_decimal(q.ask))
        mid = _checked(exact.divide(_checked(exact.add(bid, ask), exact), Decimal(2)), exact)
        return derived(work.divide(_checked(exact.subtract(ask, bid), exact), mid))
    volume = result.endpoint_bar_assessment.bar.volume
    difference = _checked(exact.subtract(volume, bucket.mean), exact)
    score = derived(work.divide(_checked(work.subtract(volume, bucket.mean), work), bucket.population_stddev))
    if difference != 0 and score == 0:
        raise _UnsupportedIdentity
    close = min(s.common_closes_at for s in result.session_assessments)
    return (volume, score, spread(spot), derived(work.divide(Decimal(_micros(decision - result.context.session.opens_at)), Decimal(60000000))),
            derived(work.divide(Decimal(_micros(close - decision)), Decimal(60000000))),
            Decimal(1 if quote.contract.right == "call" else -1),
            derived(_log_ratio(result.vwap_feature.midpoint, quote.contract.strike, work)),
            Decimal((quote.contract.expiry - result.context.session.session_date).days), quote.bid, quote.ask,
            spread(quote), derived(work.divide(Decimal(_micros(decision - quote.meta.event_at)), Decimal(1000000))),
            result.greek.delta, result.greek.iv)


def _micros(delta):
    """Convert timedelta to exact integer microseconds without floating-point seconds."""
    return (delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds


def _input_snapshot(result, chash, phash):
    """Project explicit actual owner identities and original member availability."""
    context, normalization = result.context, result.normalization
    baseline, readiness = normalization.baseline, result.greek_readiness
    method = context.manifest.greek_method
    if _method_hash(method) != _method_hash(FIXTURE_GREEK_METHOD) or method.method_spec_hash != _method_hash(method):
        raise _UnsupportedIdentity
    return {"record_kind": "options_lab.feature_inputs", "schema_version": 1,
            "decision_id": context.decision_id, "decision_at": context.decision_at.isoformat(),
            "required_bar_endpoint": context.decision_at.isoformat(), "slot_key": context.slot_key,
            "contract": _option_snapshot(result.quote)["contract"],
            "config_hash": chash, "policy_hash": phash, "context_input_digest": context.input_digest,
            "input_manifest_id": list(context.input_manifest_id),
            "feature_schema_id": result.spec.feature_schema_id, "transform_id": result.spec.transform_id,
            "state_input_hash": context.feature_state.input_hash, "state_as_of": context.feature_state.as_of.isoformat(),
            "return_input_hash": result.return_features.input_hash, "return_bar_hashes": list(result.return_features.bar_hashes),
            "return_transform_id": result.return_features.transform_id, "vwap_input_hash": result.vwap_feature.input_hash,
            "vwap_bar_hashes": list(result.vwap_feature.bar_hashes), "vwap_transform_id": result.vwap_feature.transform_id,
            "option_quote_hash": identify_quote_content(result.quote).content_hash,
            "underlying_quote_hash": result.vwap_feature.underlying_quote_hash,
            "greek_observed_input_hash": readiness.observed_input_hash, "greek_expected_input_hash": readiness.expected_input_hash,
            "greek_method_identity": list(readiness.expected_method_identity), "greek_method_hash": _method_hash(method),
            "source_records": [{"record_id": c.member.record_id, "kind": c.member.kind, "profile_id": c.member.profile_id,
                                "raw_hash": c.member.raw_hash, "envelope_hash": hashlib.sha256(c.member.envelope_bytes).hexdigest(),
                                "available_at": c.available_at.isoformat(),
                                "quote_hash": None if c.quote_identity is None else c.quote_identity.content_hash,
                                "bar_hash": None if c.bar_identity is None else c.bar_identity.full_content_hash}
                               for c in result.source_components],
            "normalization_hash": normalization.content_hash, "normalization_record_id": normalization.record_id,
            "normalization_raw_hash": normalization.raw_hash,
            "normalization_manifest": [normalization.manifest.fixture_id, normalization.manifest.payload_sha256],
            "normalization_available_at": normalization.available_at.isoformat(),
            "baseline_hash": baseline.content_hash, "training_input_hash": baseline.training_input_hash,
            "training_manifest": [baseline.manifest.fixture_id, baseline.manifest.payload_sha256],
            "partition_record_id": baseline.partition.record_id, "partition_raw_hash": baseline.partition.raw_hash,
            "baseline_cutoff": baseline.cutoff.isoformat()}


@dataclass(frozen=True, init=False)
class FeatureSpecInputRejection:
    """This class represents one safely rejected external spec selector and its receipt."""

    event_id: str
    raw_ref: str
    received_at: datetime
    field: str
    code: str
    stage: str

    def __init__(self) -> None:
        """Prevent fabricated parsing evidence.

        :returns: None.
        :raises TypeError: Always; use normalize_feature_spec.
        """
        raise TypeError("FeatureSpecInputRejection values come from normalization")


@dataclass(frozen=True, init=False)
class FeatureSpecValidation:
    """This class represents exactly one owned spec or safe raw rejection."""

    value: FeatureSpec | None
    rejection: FeatureSpecInputRejection | None

    def __init__(self) -> None:
        """Prevent caller-written successful selector evidence.

        :returns: None.
        :raises TypeError: Always; use normalize_feature_spec.
        """
        raise TypeError("FeatureSpecValidation values come from normalization")


def normalize_feature_spec(raw: object, *, event_id: str, raw_ref: str, received_at: datetime) -> FeatureSpecValidation:
    """Resolve a closed external schema/transform pair without arbitrary definitions.

    :param raw: Untrusted exact three-key selector dictionary.
    :param event_id: Trusted nonempty receipt identity.
    :param raw_ref: Trusted nonempty receipt locator.
    :param received_at: Trusted aware ingestion time, not feature availability.
    :returns: Matching code-owned constant or bounded retained raw rejection.
    :raises TypeError: If trusted envelope fields have unexpected exact types.
    :raises ValueError: If trusted envelope identity or time is invalid.
    """
    _require_nonempty_string("event_id", event_id)
    _require_nonempty_string("raw_ref", raw_ref)
    received_at = _trusted_datetime("received_at", received_at)
    try:
        _require_shape(raw, "$", ("schema_version", "feature_schema_id", "transform_id"))
        if type(raw["schema_version"]) is not int:
            _fail("schema_version", "invalid_type")
        if raw["schema_version"] != 1:
            _fail("schema_version", "unsupported_spec")
        for name in ("feature_schema_id", "transform_id"):
            parsed = _parse_string(raw[name], name)
            if re.fullmatch(r"[0-9a-f]{64}", parsed) is None:
                _fail(name, "invalid_value")
        spec = next((s for s in (EXACT_VWAP_SPEC, CLOSE_VOLUME_PROXY_SPEC)
                     if (s.feature_schema_id, s.transform_id) == (raw["feature_schema_id"], raw["transform_id"])), None)
        if spec is None:
            _fail("$", "unsupported_spec")
    except _InvalidInput as failure:
        return _freeze(FeatureSpecValidation, value=None, rejection=_freeze(FeatureSpecInputRejection,
                       event_id=event_id, raw_ref=raw_ref, received_at=received_at,
                       field=failure.args[0], code=failure.args[1], stage="feature_spec"))
    return _freeze(FeatureSpecValidation, value=spec, rejection=None)
