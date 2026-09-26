"""Score verified fixed-right forecasts for one conservative initial contract."""

from dataclasses import dataclass
from decimal import Decimal, Inexact, Rounded, localcontext

from .account import AccountSnapshot
from .bar_inputs import _UnsupportedIdentity, _identity_decimal
from .bundle_availability import BundleAssessment, assess_bundle
from .bundle_inputs import _make
from .bundles import VerifiedBundle, _execution_hash
from .calibration_inputs import _matches_retained
from .candidates import CandidateAssessment, CandidateSet, select_candidates
from .config import StrategyConfig
from .context import DecisionContext
from .context_recheck import _standard_time, recheck_decision_context
from .feature_vector import FeatureResult, FeatureVector, build_features


@dataclass(frozen=True)
class Forecast:
    """This class represents a predictor's attempted-return claim and bucket."""

    mean_attempt_return: Decimal
    overprediction_penalty: Decimal | None
    calibration_bucket: str
    bundle_hash: str


@dataclass(frozen=True, init=False)
class ScoredCandidate:
    """This class represents one reached selected-vector score in dollars."""

    candidate: CandidateAssessment
    vector: FeatureVector
    forecast: Forecast
    threshold_return: Decimal
    uncertainty_rule_id: str
    conservative_return: Decimal
    capital_dollars: Decimal
    causal_adverse_dollars: Decimal
    utility_dollars: Decimal

    def __init__(self) -> None:
        """Block caller-authored score evidence.

        :returns: None.
        :raises   TypeError: Always; use the scorer.
        """
        raise TypeError("ScoredCandidate values come from _score_candidates")


@dataclass(frozen=True, init=False)
class DecisionResult:
    """This class represents the immutable entry choice or a safe cash result."""

    selected: ScoredCandidate | None
    scored: tuple[ScoredCandidate, ...]
    supplied_vectors: tuple[FeatureVector | None, ...]
    feature_results: tuple[FeatureResult, ...]
    bundle_assessment: BundleAssessment | None
    reasons: tuple[str, ...]
    entry_ready: bool
    operational_allowed: bool = False
    economic_allowed: bool = False

    def __init__(self) -> None:
        """Block caller-authored entry readiness.

        :returns: None.
        :raises   TypeError: Always; use the scorer.
        """
        raise TypeError("DecisionResult values come from _score_candidates")


class FixedJsonPredictor:
    """This class provides literal fixed-right forecasts from verified JSON rows."""

    def __init__(self, bundle: VerifiedBundle) -> None:
        """Retain one verified descriptor without treating it as current permission.

        :param    bundle: Actual content-verified fixed-right bundle.
        :returns: None.
        :raises   TypeError: If bundle lacks the exact verified owner type.
        """
        if type(bundle) is not VerifiedBundle:
            raise TypeError("bundle must be an exact VerifiedBundle")
        self._bundle = bundle

    @property
    def metadata(self) -> VerifiedBundle:
        """Return the retained descriptor for one scorer capture.

        :returns: Original verified bundle descriptor.
        """
        return self._bundle

    def predict(self, vector: FeatureVector) -> Forecast:
        """Interpret one actual right's fixed row and declared penalty.

        :param    vector: Exact selected vector carrying contract identity.
        :returns: Literal attempted-return forecast or unknown penalty.
        :raises   TypeError: If vector has the wrong exact owner type.
        :raises   ValueError: If no fixed row matches the vector's right.
        """
        if type(vector) is not FeatureVector:
            raise TypeError("vector must be an exact FeatureVector")
        row = next((row for row in self._bundle.model.rows if row.right == vector.contract.right), None)
        if row is None:
            raise ValueError("fixed model row missing")
        metadata = self._bundle.calibration_metadata
        bucket = None if metadata is None else next((item for item in metadata.buckets
            if item.bucket_id == row.calibration_bucket), None)
        return Forecast(row.mean_attempt_return, None if bucket is None else bucket.penalty,
                        row.calibration_bucket, self._bundle.bundle_hash)


def _cash(vectors, scored=(), features=(), assessment=None, reason="input_mismatch"):
    """Retain reached immutable evidence with one bounded cash reason.

    :param    vectors: Supplied selected-vector sequence.
    :param    scored: Completed score prefix.
    :param    features: Completed fresh feature assessments.
    :param    assessment: Reached fresh bundle assessment, if any.
    :param    reason: Code-owned safe reason.
    :returns: Immutable cash decision.
    """
    return _make(DecisionResult, selected=None, scored=tuple(scored), supplied_vectors=vectors,
                 feature_results=tuple(features), bundle_assessment=assessment,
                 reasons=(reason,), entry_ready=False, operational_allowed=False,
                 economic_allowed=False)


def _finite(value):
    """Check a bounded exact finite decimal before any money arithmetic.

    :param    value: Forecast or owner numeric claim.
    :returns: Whether the exact finite identity is supported.
    """
    if type(value) is not Decimal or not value.is_finite():
        return False
    try:
        return _identity_decimal(value) is not None
    except _UnsupportedIdentity:
        return False


def _score_dollars(cap: Decimal, bid: Decimal, ask: Decimal, mean: Decimal,
                   threshold: Decimal, penalty: Decimal) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    """Apply the one-contract causal hurdle with exact terminating decimals.

    :param    cap: Original decision ask cap per share.
    :param    bid: Current selected bid per share.
    :param    ask: Current selected ask per share.
    :param    mean: Expected attempted net return in return units.
    :param    threshold: Frozen decision threshold in return units.
    :param    penalty: Supported uncertainty in return units.
    :returns: Conservative return, capital, adverse dollars, utility dollars.
    """
    with localcontext() as arithmetic:
        arithmetic.prec = 10000
        arithmetic.traps[Inexact] = arithmetic.traps[Rounded] = True
        capital = Decimal(100) * cap
        adverse = max(Decimal("0.005") * capital, Decimal(100) * (ask - bid))
        conservative = mean - threshold - penalty
        return conservative, capital, adverse, capital * conservative - adverse


def _score_candidates(context: DecisionContext, candidates: CandidateSet,
                      vectors: tuple[FeatureVector | None, ...], predictor: object,
                      config: StrategyConfig) -> DecisionResult:
    """Score actual selected candidates after fresh owner proof.

    :param    context: Original decision context.
    :param    candidates: Original ranked candidate set.
    :param    vectors: Selected complete feature vectors in call/put order.
    :param    predictor: One metadata descriptor and single-vector prediction method.
    :param    config: Original strategy configuration.
    :returns: One entry choice or cash with reached evidence.
    """
    if (type(context) is not DecisionContext or type(candidates) is not CandidateSet
            or type(vectors) is not tuple or type(config) is not StrategyConfig):
        raise TypeError("context, candidates, vectors and config require exact owner types")
    if len(vectors) > 2:
        return _cash(vectors, reason="vector_count_mismatch")
    try:
        decision_at = object.__getattribute__(context, "decision_at")
    except AttributeError:
        return _cash(vectors, reason="context_time_mismatch")
    if not _standard_time(decision_at):
        return _cash(vectors, reason="context_time_mismatch")
    checked = recheck_decision_context(context, config=config, now=decision_at)
    if not checked.valid:
        return _cash(vectors, reason="context_integrity_failure")
    fresh_context = checked.context
    owners = tuple(component.value for component in fresh_context.components
                   if component.member.kind == "account_snapshot" and component.disposition == "selected")
    fresh_account = owners[0] if len(owners) == 1 and type(owners[0]) is AccountSnapshot else None
    try:
        retained_account = object.__getattribute__(candidates, "account")
    except AttributeError:
        return _cash(vectors, reason="account_integrity_failure")
    if not _matches_retained(retained_account, fresh_account, set()):
        return _cash(vectors, reason="account_integrity_failure")
    fresh_candidates = select_candidates(fresh_context, fresh_account, config)
    if not _matches_retained(candidates, fresh_candidates, set()):
        return _cash(vectors, reason="candidate_integrity_failure")
    try:
        descriptor = predictor.metadata
    except Exception:
        return _cash(vectors, reason="predictor_metadata_failure")
    if type(descriptor) is not VerifiedBundle:
        return _cash(vectors, reason="predictor_metadata_invalid")
    assessment = assess_bundle(descriptor, context=fresh_context, config=config,
                               now=decision_at, requested_use="core_fixture")
    if not assessment.available or assessment.bundle is None:
        return _cash(vectors, assessment=assessment, reason="bundle_unavailable")
    model = assessment.bundle
    if model.model.model_kind == "cash":
        return _cash(vectors, assessment=assessment,
                     reason="cash_champion" if not vectors else "cash_vectors_present")
    selected = fresh_candidates.selected
    if len(vectors) != len(selected):
        return _cash(vectors, assessment=assessment, reason="vector_count_mismatch")
    prediction = model.manifest.prediction_contract
    profile = None if prediction is None else prediction.base_cost_profile
    fee = None if fresh_account is None else fresh_account.applicable_round_trip_fees
    if (profile is None or fee is None or not _finite(fee)
            or profile.fee_charging_rule != "filled_round_trip_once_no_fill_zero_v1"
            or profile.base_execution_hash != _execution_hash(config)
            or profile.target_definition_id != config.execution.target_definition
            or any(choice.quote_budget_assessment is None
                   or choice.quote_budget_assessment.round_trip_fees != profile.filled_attempt_round_trip_fee
                   for choice in selected)):
        return _cash(vectors, assessment=assessment, reason="base_fee_mismatch")
    if (prediction.threshold_return is None or not _finite(prediction.threshold_return)
            or prediction.uncertainty_rule_id is None or model.spec is None
            or model.normalization_result is None or model.normalization_result.value is None):
        return _cash(vectors, assessment=assessment, reason="prediction_contract_incomplete")
    rows = {row.right: row for row in model.model.rows}
    support = {row.bucket_id: row for row in assessment.calibration if row.supported}
    scored, features = [], []
    for choice, supplied in zip(selected, vectors):
        if type(supplied) is not FeatureVector:
            return _cash(vectors, scored, features, assessment, "feature_missing")
        greek = None if choice.greek_readiness is None else choice.greek_readiness.greek
        feature = build_features(fresh_context, choice.option_quote, greek,
            now=decision_at, spec=model.spec, normalization=model.normalization_result.value)
        features.append(feature)
        if feature.vector is None or not _matches_retained(supplied, feature.vector, set()):
            return _cash(vectors, scored, features, assessment, "feature_integrity_failure")
        try:
            forecast = predictor.predict(feature.vector)
        except Exception:
            return _cash(vectors, scored, features, assessment, "predictor_failure")
        if type(forecast) is not Forecast:
            return _cash(vectors, scored, features, assessment, "forecast_invalid")
        row = rows.get(choice.contract.right)
        bucket = support.get(forecast.calibration_bucket) if type(forecast.calibration_bucket) is str else None
        if (row is None or bucket is None or type(forecast.bundle_hash) is not str
                or forecast.bundle_hash != model.bundle_hash
                or forecast.calibration_bucket != row.calibration_bucket
                or bucket.bucket_id != row.calibration_bucket
                or not _finite(forecast.mean_attempt_return)
                or forecast.mean_attempt_return != row.mean_attempt_return
                or not _finite(forecast.overprediction_penalty)
                or forecast.overprediction_penalty < 0
                or forecast.overprediction_penalty != bucket.penalty
                or bucket.penalty_method != prediction.uncertainty_rule_id):
            return _cash(vectors, scored, features, assessment, "forecast_mismatch")
        ask, bid, cap = choice.option_quote.ask, choice.option_quote.bid, choice.original_ask_cap
        if not all(_finite(value) for value in (ask, bid, cap)) or cap <= 0 or ask < bid:
            return _cash(vectors, scored, features, assessment, "price_integrity_failure")
        conservative, capital, adverse, utility = _score_dollars(cap, bid, ask,
            forecast.mean_attempt_return, prediction.threshold_return, forecast.overprediction_penalty)
        scored.append(_make(ScoredCandidate, candidate=choice, vector=feature.vector,
            forecast=forecast, threshold_return=prediction.threshold_return,
            uncertainty_rule_id=prediction.uncertainty_rule_id,
            conservative_return=conservative, capital_dollars=capital,
            causal_adverse_dollars=adverse, utility_dollars=utility))
    if not scored:
        return _cash(vectors, scored, features, assessment, "no_selected_candidates")
    largest = max(item.utility_dollars for item in scored)
    winners = [item for item in scored if item.utility_dollars == largest]
    if largest <= 0:
        return _cash(vectors, scored, features, assessment, "no_positive_utility")
    if len(winners) != 1:
        return _cash(vectors, scored, features, assessment, "utility_tie")
    return _make(DecisionResult, selected=winners[0], scored=tuple(scored),
                 supplied_vectors=vectors, feature_results=tuple(features),
                 bundle_assessment=assessment, reasons=(), entry_ready=True,
                 operational_allowed=False, economic_allowed=False)
