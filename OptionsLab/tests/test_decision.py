"""Score actual selected contracts from admitted source and verified bundle owners."""

from datetime import datetime, timezone, tzinfo
from decimal import Decimal, Inexact, Rounded, ROUND_UP, localcontext
from copy import copy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from options_lab.admission import verify_fixture_bundle
from options_lab.bundle_availability import assess_bundle
from options_lab.bundle_manifest_inputs import normalize_bundle_manifest
from options_lab.bundles import verify_bundle
from options_lab.candidates import select_candidates
from options_lab.config import StrategyConfig
from options_lab.context import build_decision_context
from options_lab.context_inputs import normalize_context_request
from options_lab.decision import (DecisionResult, FixedJsonPredictor, Forecast, ScoredCandidate,
                                  _score_candidates, _score_dollars)
from options_lab.feature_vector import EXACT_VWAP_SPEC, build_features
from options_lab.runtime import measure_runtime
from options_lab.volume_normalization import normalize_feature_normalization
from test_bundle_schedule import admitted


NOW = datetime(2026, 9, 4, 14, 5, tzinfo=timezone.utc)
SOURCE = "p15-decision-source-v1"


def bundle(name="call"):
    """Verify a registered P15 model against all original A2 owners."""
    fixture = admitted("p15-decision-bundle-v1")
    body = next(m for m in fixture.members if m.record_id == name).decode_raw_body()
    manifest = normalize_bundle_manifest(body["manifest"], event_id="p15-test",
        raw_ref="fixture", received_at=NOW).value
    assert manifest is not None
    names = ["p14c2-sources-v1", "p14c2-calendar-v1", "p14b-schedule-v1",
        "p11-feature-vector-v1", SOURCE]
    normalizer = None
    if name != "cash":
        names = ["p14c-partition-samples-v1", "p14c-partition-memberships-v1",
            "p15-decision-partition-v1", "p15-decision-record-v1",
            "p15-decision-report-v1", "p15-decision-schedule-v1", *names]
        training, artifact = admitted("p14c-volume-training-v1"), admitted("p14c-volume-normalization-v1")
        member = next(m for m in artifact.members if m.record_id == "good")
        normalizer = normalize_feature_normalization(member.decode_raw_body(), manifest=artifact,
            record_id="good", training_manifest=training,
            decision_at=datetime(2026, 4, 30, 1, tzinfo=timezone.utc)).value
        assert normalizer is not None
    else:
        names.append("p14b1b-assembly-v1")
    result = verify_bundle(manifest, body["model_utf8"].encode(), fixture=fixture,
        spec=EXACT_VWAP_SPEC if name != "cash" else None, normalization=normalizer,
        config=StrategyConfig(), runtime=measure_runtime().value,
        upstream_fixtures=tuple(admitted(item) for item in names))
    assert result.value is not None, result.rejection
    return result.value


def source(normalizer=None, *, account_record="account_snapshot"):
    """Admit combined source bytes and build real selected owners and vectors."""
    raw = (Path(__file__).parent / "fixtures" / (SOURCE + ".json")).read_bytes()
    admitted = verify_fixture_bundle(SOURCE, raw, event_id="p15-source", raw_ref="fixture",
                                     received_at=NOW)
    assert admitted.value is not None, admitted.rejection
    ids = [m.record_id for m in admitted.value.members if m.record_id == "session"
           or m.record_id == account_record or m.record_id == "underlying_quote"
           or m.record_id.startswith("bars-") or m.record_id.startswith(("c99-", "c100-", "p99-", "pdelta-"))]
    request = normalize_context_request(dict(decision_id="decision", decision_at=NOW.isoformat(),
        member_record_ids=ids), event_id="request", raw_ref="request", received_at=NOW)
    context = build_decision_context(request, admitted.value, config=StrategyConfig(),
                                     previous_feature_state=None).context
    assert context is not None
    account = next(c.value for c in context.components if c.member.record_id == account_record)
    candidates = select_candidates(context, account, StrategyConfig())
    if normalizer is None:
        normalizer = bundle().normalization_result.value
    vectors = tuple(build_features(context, choice.option_quote, choice.greek_readiness.greek,
        now=NOW, spec=EXACT_VWAP_SPEC, normalization=normalizer).vector
        for choice in candidates.selected)
    return context, candidates, vectors


def test_decision_api_is_importable():
    """The scorer exposes one forecast and one internal scoring entry point."""
    assert all(item is not None for item in (Forecast, ScoredCandidate, DecisionResult, FixedJsonPredictor))
    assert callable(_score_candidates)


def test_combined_source_has_actual_selection_and_full_vectors():
    """Both selected rights have complete features from one admitted source."""
    context, candidates, vectors = source()
    assert context.feature_state is not None
    assert [c.contract.right for c in candidates.selected] == ["call", "put"]
    assert all(vector is not None and len(vector.values) == 22 for vector in vectors)


def test_registered_bundle_covers_actual_combined_source_and_support():
    """B2 independently rechecks source coverage and both C2 buckets."""
    model = bundle()
    context, candidates, vectors = source(model.normalization_result.value)
    result = assess_bundle(model, context=context, config=StrategyConfig(),
                           now=NOW, requested_use="core_fixture")
    assert result.available, result.reasons
    assert [item.bucket_id for item in result.calibration] == ["call", "put"]
    assert all(item.supported for item in result.calibration)
    assert len(candidates.selected) == len(vectors) == 2


def test_fixed_json_call_scores_one_contract():
    """The actual fixed row selects the profitable call after its one hurdle."""
    model = bundle()
    context, candidates, vectors = source(model.normalization_result.value)
    result = _score_candidates(context, candidates, vectors, FixedJsonPredictor(model), StrategyConfig())
    assert result.selected is not None
    assert result.selected.candidate.contract.right == "call"
    assert (result.selected.threshold_return, result.selected.forecast.overprediction_penalty,
            result.selected.conservative_return, result.selected.capital_dollars,
            result.selected.causal_adverse_dollars, result.selected.utility_dollars) == (
            Decimal(0), Decimal("0.01"), Decimal("0.07"), Decimal(510),
            Decimal(20), Decimal("15.7"))
    assert result.selected.uncertainty_rule_id == "fixture_fixed_penalty_v1"
    partition = model.calibration_partition
    assert partition.model_membership.rows and partition.tuning_membership.rows
    assert partition.frozen_at < partition.fold.evaluation_start
    assert {r.sample_id for r in partition.calibration_members}.isdisjoint(
        {r.sample_id for r in (*partition.model_membership.rows, *partition.tuning_membership.rows)})


def test_fixed_json_put_wins_and_exact_tie_is_cash():
    """Two different registered model rows drive put entry or exact tie cash."""
    for name, expected in (("put", "put"), ("tie", None)):
        model = bundle(name)
        context, candidates, vectors = source(model.normalization_result.value)
        result = _score_candidates(context, candidates, vectors, FixedJsonPredictor(model), StrategyConfig())
        assert [item.candidate.contract.right for item in result.scored] == ["call", "put"]
        assert (None if result.selected is None else result.selected.candidate.contract.right) == expected
        assert result.reasons == (() if expected else ("utility_tie",))


def test_cash_champion_uses_no_vectors_or_prediction():
    """Verified cash champion returns cash without inventing feature inputs."""
    model = bundle("cash")
    context, candidates, _ = source()
    class CashProbe:
        """This class represents a predictor that rejects any cash predict call."""

        metadata = model

        def predict(self, vector):
            """Fail if the scorer sends a fabricated cash vector.

            :param    vector: Unexpected vector.
            :returns: Nothing.
            :raises   AssertionError: Always.
            """
            raise AssertionError("cash predictor must not run")
    result = _score_candidates(context, candidates, (), CashProbe(), StrategyConfig())
    assert result.selected is None and result.scored == ()
    assert result.supplied_vectors == () and result.feature_results == ()
    assert result.reasons == ("cash_champion",)


def test_exact_dollar_counterexample_and_zero_boundary():
    """One-contract dollar utility beats return ranking and has strict zero."""
    small = _score_dollars(Decimal("2"), Decimal("2"), Decimal("2"),
                           Decimal("0.04"), Decimal(0), Decimal("0.01"))
    large = _score_dollars(Decimal("5"), Decimal("5"), Decimal("5"),
                           Decimal("0.03"), Decimal(0), Decimal("0.01"))
    assert small == (Decimal("0.03"), Decimal(200), Decimal(1), Decimal(5))
    assert large == (Decimal("0.02"), Decimal(500), Decimal("2.5"), Decimal("7.5"))
    assert large[-1] > small[-1]
    zero = _score_dollars(Decimal("2"), Decimal("2"), Decimal("2"),
                          Decimal("0.005"), Decimal(0), Decimal(0))
    assert zero[-1] == 0
    with localcontext() as ambient:
        ambient.prec = 2
        ambient.rounding = ROUND_UP
        ambient.traps[Inexact] = ambient.traps[Rounded] = True
        before = (ambient.flags.copy(), ambient.traps.copy())
        near = _score_dollars(Decimal("2"), Decimal("2"), Decimal("2"),
                              Decimal("0.0050000000000000000000000000000000000001"), Decimal(0), Decimal(0))
        assert near[-1] > 0 and (ambient.flags, ambient.traps) == before


def test_later_predictor_failure_keeps_call_score_and_no_rank_loser():
    """A failed put prediction returns global cash with completed call evidence."""
    model = bundle()
    context, candidates, vectors = source(model.normalization_result.value)
    fixed = FixedJsonPredictor(model)
    class FailingPut:
        """This class represents a predictor that fails only on the put vector."""

        metadata = model

        def predict(self, vector):
            """Return the call row and fail the later selected put.

            :param    vector: Actual selected vector.
            :returns: Call forecast.
            :raises   RuntimeError: For the put.
            """
            if vector.contract.right == "put":
                raise RuntimeError("untrusted payload text")
            return fixed.predict(vector)
    result = _score_candidates(context, candidates, vectors, FailingPut(), StrategyConfig())
    assert result.selected is None and result.reasons == ("predictor_failure",)
    assert [item.candidate.contract.right for item in result.scored] == ["call"]
    assert len(result.feature_results) == 2 and len(result.supplied_vectors) == 2
    assert any(row.rank == 2 for row in candidates.considered)
    assert not result.entry_ready and not result.operational_allowed


def test_mutated_selected_or_vector_fails_before_prediction():
    """Fresh owner comparison rejects copied success flags and damaged vectors."""
    model = bundle()
    context, candidates, vectors = source(model.normalization_result.value)
    class NoPredict:
        """This class represents a predictor whose hook must not be reached."""

        metadata = model

        def predict(self, vector):
            """Reject any prediction after failed source proof.

            :param    vector: Unexpected vector.
            :returns: Nothing.
            :raises   AssertionError: Always.
            """
            raise AssertionError("source proof did not stop prediction")
    altered = copy(candidates)
    object.__setattr__(altered, "selected_call", None)
    result = _score_candidates(context, altered, vectors, NoPredict(), StrategyConfig())
    assert result.reasons == ("candidate_integrity_failure",)
    wrong = copy(vectors[0])
    object.__setattr__(wrong, "values", (Decimal(999), *wrong.values[1:]))
    result = _score_candidates(context, candidates, (wrong, vectors[1]), NoPredict(), StrategyConfig())
    assert result.reasons == ("feature_integrity_failure",)


def test_no_positive_model_is_cash_with_both_actual_scores():
    """A valid model with no strictly positive utility keeps both calculations."""
    model = bundle("negative")
    context, candidates, vectors = source(model.normalization_result.value)
    result = _score_candidates(context, candidates, vectors, FixedJsonPredictor(model), StrategyConfig())
    assert result.selected is None and result.reasons == ("no_positive_utility",)
    assert len(result.scored) == 2 and max(item.utility_dollars for item in result.scored) < 0


def test_exact_current_fee_mismatch_and_missing_are_cash():
    """Registered account fees cannot be rewritten to fit frozen BASE costs."""
    model = bundle()
    for account_record in ("fee2-account_snapshot", "missing-fee-account_snapshot"):
        context, candidates, vectors = source(model.normalization_result.value,
                                              account_record=account_record)
        result = _score_candidates(context, candidates, vectors, FixedJsonPredictor(model), StrategyConfig())
        assert result.selected is None and result.reasons == ("base_fee_mismatch",)
        assert result.bundle_assessment.available
        assert result.scored == ()


def test_predictor_metadata_once_and_predict_once_per_selected_vector():
    """The scorer captures metadata once and sends only each actual vector."""
    model = bundle()
    context, candidates, vectors = source(model.normalization_result.value)
    fixed = FixedJsonPredictor(model)
    class Counting:
        """This class represents one mutable metadata and single-vector probe."""

        reads = 0
        calls = []

        @property
        def metadata(self):
            """Return valid metadata once, then an invalid descriptor.

            :returns: First read's actual bundle or later None.
            """
            self.reads += 1
            return model if self.reads == 1 else None

        def predict(self, vector):
            """Record one selected vector and return its fixed row.

            :param    vector: Actual selected feature vector.
            :returns: Its literal model forecast.
            """
            self.calls.append(vector)
            return fixed.predict(vector)
    probe = Counting()
    result = _score_candidates(context, candidates, vectors, probe, StrategyConfig())
    assert result.entry_ready and probe.reads == 1
    assert tuple(probe.calls) == vectors


@pytest.mark.parametrize("change", ("bool", "float", "nan", "missing_penalty", "negative_penalty",
                                  "wrong_bucket", "wrong_hash", "wrong_mean", "wrong_type"))
def test_untrusted_forecast_disagreement_is_cash(change):
    """Literal row, C2 bucket, finite return and bundle identity all must match."""
    model = bundle()
    context, candidates, vectors = source(model.normalization_result.value)
    fixed = FixedJsonPredictor(model)
    good = fixed.predict(vectors[0])
    bad = {
        "bool": replace(good, mean_attempt_return=True),
        "float": replace(good, mean_attempt_return=0.08),
        "nan": replace(good, mean_attempt_return=Decimal("NaN")),
        "missing_penalty": replace(good, overprediction_penalty=None),
        "negative_penalty": replace(good, overprediction_penalty=Decimal("-0.01")),
        "wrong_bucket": replace(good, calibration_bucket="put"),
        "wrong_hash": replace(good, bundle_hash="0" * 64),
        "wrong_mean": replace(good, mean_attempt_return=Decimal("0.09")),
        "wrong_type": None,
    }[change]
    probe = SimpleNamespace(metadata=model, predict=lambda vector: bad)
    result = _score_candidates(context, candidates, vectors, probe, StrategyConfig())
    assert result.selected is None
    assert result.reasons == (("forecast_invalid",) if change == "wrong_type" else ("forecast_mismatch",))
    assert result.scored == () and len(result.feature_results) == 1


def test_metadata_exception_and_damaged_bundle_hash_are_bounded():
    """Predictor exceptions and corrupt A2 retention cannot create entry readiness."""
    model = bundle()
    context, candidates, vectors = source(model.normalization_result.value)
    class ThrowingMetadata:
        """This class represents an external metadata property failure."""

        @property
        def metadata(self):
            """Raise without exposing arbitrary payload text.

            :returns: Nothing.
            :raises   RuntimeError: Always.
            """
            raise RuntimeError("untrusted metadata text")
    result = _score_candidates(context, candidates, vectors, ThrowingMetadata(), StrategyConfig())
    assert result.reasons == ("predictor_metadata_failure",)
    invalid = _score_candidates(context, candidates, vectors,
                                SimpleNamespace(metadata=None), StrategyConfig())
    assert invalid.reasons == ("predictor_metadata_invalid",)
    damaged = copy(model)
    object.__setattr__(damaged, "bundle_hash", "0" * 64)
    result = _score_candidates(context, candidates, vectors,
                               FixedJsonPredictor(damaged), StrategyConfig())
    assert result.reasons == ("bundle_unavailable",)
    assert not result.entry_ready and result.bundle_assessment is not None


def test_scoring_authority_constructors_are_closed():
    """Only the scorer can create successful result and score owners."""
    with pytest.raises(TypeError):
        DecisionResult()
    with pytest.raises(TypeError):
        ScoredCandidate()
    with pytest.raises(TypeError):
        _score_candidates("wrong context", None, (), None, StrategyConfig())


@pytest.mark.parametrize("damage,reason", (
    ("missing_decision_time", "context_time_mismatch"),
    ("naive_decision_time", "context_time_mismatch"),
    ("hostile_decision_timezone", "context_time_mismatch"),
    ("missing_candidate_account", "account_integrity_failure"),
    ("missing_account_fee_leaf", "account_integrity_failure"),
    ("missing_config_execution", "context_integrity_failure"),
))
def test_damaged_retained_owner_is_cash_before_predictor_hooks(damage, reason):
    """Damaged exact owner graphs produce bounded cash, never an ordinary exception."""
    model = bundle()
    context, candidates, vectors = source(model.normalization_result.value)
    config = StrategyConfig()
    class ForbiddenPredictor:
        """This class represents a predictor whose hooks are beyond the damaged owner."""

        reads = 0
        calls = 0

        @property
        def metadata(self):
            """Count forbidden metadata reads.

            :returns: Actual model if reached.
            """
            self.reads += 1
            return model

        def predict(self, vector):
            """Count forbidden forecast calls.

            :param    vector: Unexpected vector.
            :returns: Nothing.
            :raises   AssertionError: Always.
            """
            self.calls += 1
            raise AssertionError("predict hook was reached")
    probe = ForbiddenPredictor()
    if damage in ("missing_decision_time", "naive_decision_time", "hostile_decision_timezone"):
        context = copy(context)
        if damage == "missing_decision_time":
            object.__delattr__(context, "decision_at")
        elif damage == "naive_decision_time":
            object.__setattr__(context, "decision_at", datetime(2026, 9, 4, 14, 5))
        else:
            class HostileTimezone(tzinfo):
                """This class represents a timezone that must never be invoked."""

                def utcoffset(self, dt):
                    """Fail if the scorer or owner calls an untrusted timezone.

                    :param    dt: Associated datetime.
                    :returns: Nothing.
                    :raises   AssertionError: Always.
                    """
                    raise AssertionError("hostile timezone invoked")
            object.__setattr__(context, "decision_at",
                               datetime(2026, 9, 4, 14, 5, tzinfo=HostileTimezone()))
    elif damage == "missing_candidate_account":
        candidates = copy(candidates)
        object.__delattr__(candidates, "account")
    elif damage == "missing_account_fee_leaf":
        account, candidates = copy(candidates.account), copy(candidates)
        object.__delattr__(account, "applicable_round_trip_fees")
        object.__setattr__(candidates, "account", account)
    else:
        config = copy(config)
        object.__delattr__(config, "execution")
    result = _score_candidates(context, candidates, vectors, probe, config)
    assert result.selected is None and result.reasons == (reason,)
    assert result.supplied_vectors is vectors and not result.entry_ready
    assert probe.reads == probe.calls == 0
