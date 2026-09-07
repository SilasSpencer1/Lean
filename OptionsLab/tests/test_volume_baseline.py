"""Population moments, frozen statistics and actual admitted fitting regressions."""

from dataclasses import FrozenInstanceError, replace
from datetime import timedelta
from decimal import Decimal as D, Inexact, Rounded, ROUND_UP, localcontext
import hashlib
import json

import pytest

from test_volume_inputs import AT, inputs


def fit(name="training", *, cutoff=AT):
    """Fit actual admitted fixture records through the required public entry point."""
    from options_lab.volume import fit_volume_baseline
    manifest, partition, groups = inputs(name)
    return fit_volume_baseline(groups, cutoff, partition=partition, manifest=manifest)


def test_actual_admitted_population_literals_and_independent_sparse_bucket():
    result = fit()
    assert result.reasons == () and result.baseline is not None
    buckets = {bucket.minute_index: bucket for bucket in result.baseline.buckets}
    assert tuple(buckets) == (33, 34, 35)
    assert (buckets[35].sample_count, buckets[35].mean, buckets[35].population_stddev) == (20, D(1000), D(100))
    assert (buckets[34].sample_count, buckets[34].mean, buckets[34].population_stddev) == (
        20, D("1.5"), D("1.118033988749894848204586834365638"))
    assert buckets[34].ready and buckets[35].ready
    assert (buckets[33].sample_count, buckets[33].mean, buckets[33].population_stddev) == (18, D(10), D(0))
    assert buckets[33].reasons == ("insufficient_training_sessions", "population_stddev_zero")
    assert not buckets[33].ready
    assert sum(bool(row.omission_reasons) for row in result.inputs.selected_inputs) == 2
    assert buckets[35].contributing_sessions == result.inputs.partition.training_sessions
    assert result.baseline.training_input_hash == result.inputs.training_input_hash


@pytest.mark.parametrize("name", ["heldout", "future-bar", "wrong-source", "source-identity", "calendar-source-identity"])
def test_fitting_never_recovers_a_good_subset_from_failed_binding(name):
    result = fit(name)
    assert result.baseline is None and result.reasons == result.inputs.reasons
    assert result.buckets == ()
    assert result.inputs.training_input_hash is None


@pytest.mark.parametrize("name", ["all-omitted", "empty-bars"])
def test_no_eligible_volume_produces_no_invented_bucket(name):
    result = fit(name)
    assert result.baseline is not None and result.reasons == ()
    assert result.baseline.buckets == ()
    assert result.baseline.training_input_hash is not None


def test_actual_duplicate_receipts_and_permutation_preserve_frozen_baseline_identity():
    from options_lab.volume import fit_volume_baseline
    manifest, partition, groups = inputs("duplicate")
    original = fit_volume_baseline(groups, AT, partition=partition, manifest=manifest)
    shuffled = tuple(replace(group, bars=group.bars[::-1] + group.bars) for group in groups[::-1])
    result = fit_volume_baseline(shuffled + shuffled[:1], AT, partition=partition, manifest=manifest)
    assert result.baseline == original.baseline
    assert result.buckets[-1].sample_count == 20
    assert result.inputs.training_bars != original.inputs.training_bars


@pytest.mark.parametrize("precision", [2, 120])
def test_output_and_hash_ignore_ambient_context_and_preserve_its_flags(precision):
    expected = fit()
    with localcontext() as context:
        context.prec, context.rounding = precision, ROUND_UP
        context.Emin, context.Emax, context.clamp = -2, 2, 1
        context.traps[Inexact] = context.traps[Rounded] = True
        context.flags[Inexact] = True
        flags = context.flags.copy()
        assert fit() == expected
        assert context.flags == flags


def test_baseline_snapshot_is_complete_fresh_and_matches_actual_hash():
    from options_lab.volume import VOLUME_NORMALIZATION_ID
    from options_lab.feature_math import NUMERIC_CONVENTION_ID
    result = fit()
    baseline = result.baseline
    snapshot = baseline.snapshot
    encoded = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()
    assert baseline.content_hash == hashlib.sha256(encoded).hexdigest()
    assert snapshot["training_input_hash"] == result.inputs.training_input_hash
    assert snapshot["numeric_id"] == NUMERIC_CONVENTION_ID
    assert snapshot["normalization_id"] == VOLUME_NORMALIZATION_ID
    assert snapshot["buckets"][-1]["mean"] == "1000"
    assert "content_hash" not in snapshot and "catalog_sha256" not in snapshot
    snapshot["buckets"].clear()
    assert len(baseline.snapshot["buckets"]) == 3
    later = fit(cutoff=AT + timedelta(microseconds=1)).baseline
    assert later.buckets == baseline.buckets and later.content_hash != baseline.content_hash
    assert baseline.manifest.origin == "synthetic" and baseline.manifest.fidelity_tier == 0
    assert not baseline.manifest.operational_allowed and not baseline.manifest.economic_allowed


def test_outcomes_cannot_be_forged_or_mutated_and_trusted_types_remain_strict():
    from options_lab.volume import VolumeBucket, VolumeBaseline, VolumeBaselineFit, fit_volume_baseline
    result = fit()
    for cls in (VolumeBucket, VolumeBaseline, VolumeBaselineFit):
        with pytest.raises(TypeError):
            cls()
    with pytest.raises(FrozenInstanceError):
        result.baseline.buckets[-1].mean = D(0)
    with pytest.raises(FrozenInstanceError):
        result.baseline.content_hash = "0" * 64
    manifest, partition, groups = inputs()
    with pytest.raises(TypeError):
        fit_volume_baseline(list(groups), AT, partition=partition, manifest=manifest)
    with pytest.raises(ValueError):
        fit_volume_baseline(groups, AT.replace(tzinfo=None), partition=partition, manifest=manifest)


def numeric_inputs(name="numeric"):
    """Normalize the separate registered numerical fixture with actual public owners."""
    from datetime import datetime
    from pathlib import Path
    from options_lab import (
        verify_fixture_bundle, normalize_volume_partition, TrainingSessionBars,
        normalize_exchange_session, normalize_observation_meta, normalize_underlying_bar,
    )
    fixture_id = "p10a-volume-moments-v1"
    payload = (Path(__file__).parent / "fixtures" / (fixture_id + ".json")).read_bytes()
    manifest = verify_fixture_bundle(fixture_id, payload, event_id="numeric", received_at=AT, raw_ref="numeric").value
    assert manifest is not None
    members = {m.record_id: m for m in manifest.members}
    partition = normalize_volume_partition(members[name].decode_raw_body(), manifest=manifest, record_id=name).value
    groups = []
    for session_id, bar_ids in partition.training_inputs:
        member = members[session_id]
        env = member.decode_envelope()
        session = normalize_exchange_session(member.decode_raw_body(), event_id=env["event_id"], received_at=datetime.fromisoformat(env["simulated_received_at"]), raw_ref=env["raw_ref"]).value
        bars = []
        for record in bar_ids:
            member = members[record]
            env = member.decode_envelope()
            meta = normalize_observation_meta(env["metadata"], event_id=env["event_id"], received_at=datetime.fromisoformat(env["simulated_received_at"]), raw_ref=env["raw_ref"]).value
            bars.append(normalize_underlying_bar(member.decode_raw_body(), meta=meta, event_id=env["event_id"], receive_sequence=env["receive_sequence"]).value)
        groups.append(TrainingSessionBars(session, tuple(bars)))
    return manifest, partition, tuple(groups)


def numeric_fit(name="numeric"):
    """Run the actual public fitter on admitted numerical observations."""
    from options_lab.volume import fit_volume_baseline
    manifest, partition, groups = numeric_inputs(name)
    return fit_volume_baseline(groups, AT, partition=partition, manifest=manifest)


@pytest.mark.parametrize("minute,count,mean,stddev,reasons", [
    (1, 20, "1", "1", ()),
    (2, 19, "9", "5.477225575051661134569697828008021", ("insufficient_training_sessions",)),
    (3, 3, "1.333333333333333333333333333333333", "1.247219128924647128527916244105516", ("insufficient_training_sessions",)),
    (4, 1, "7", "0", ("insufficient_training_sessions", "population_stddev_zero")),
    (5, 20, "0", "0", ("population_stddev_zero",)),
    (6, 20, "1e90", "1", ()),
    (7, 20, "1000", "1e-100", ()),
    (8, 20, "2", "1", ()),
])
def test_independent_population_oracles_and_exact_cancellation(minute, count, mean, stddev, reasons):
    result = numeric_fit()
    assert result.reasons == () and result.baseline is not None
    bucket = next(b for b in result.buckets if b.minute_index == minute)
    assert (bucket.sample_count, bucket.mean, bucket.population_stddev) == (count, D(mean), D(stddev))
    assert bucket.reasons == reasons
    assert bucket.ready == (not reasons)
    assert len(set(bucket.contributing_sessions)) == count


@pytest.mark.parametrize("name", ["product-bound", "sum-bound", "variance-bound"])
def test_exact_intermediate_or_working_variance_limits_reject_whole_fit(name):
    result = numeric_fit(name)
    assert result.inputs.reasons == () and result.inputs.training_input_hash is not None
    assert result.baseline is None and result.reasons == ("arithmetic_precision_unsupported",)
    assert len(result.buckets) == 9 and result.buckets[0].ready
    last = result.buckets[-1]
    assert last.mean is last.population_stddev is None
    assert last.reasons == ("insufficient_training_sessions", "arithmetic_precision_unsupported")
    assert last.sample_count == (1 if name == "product-bound" else 2)


@pytest.mark.parametrize("name", ["numeric", "sum-bound"])
def test_extreme_exact_moments_and_failures_ignore_ambient_rounding(name):
    expected = numeric_fit(name)
    with localcontext() as context:
        context.prec = 2
        context.rounding = ROUND_UP
        context.traps[Inexact] = context.traps[Rounded] = True
        assert numeric_fit(name) == expected


def test_frozen_34_digit_mean_is_stored_without_unpublished_precision():
    result = numeric_fit()
    bucket = next(b for b in result.buckets if b.minute_index == 6)
    # Exact mathematical mean is 10^90+1, but the published mean has 34 digits.
    assert bucket.mean == D("1e90")
    assert bucket.population_stddev == 1
    assert bucket.snapshot["mean"] == "1" + "0" * 90
    assert len(bucket.mean.as_tuple().digits) == 34


def test_observed_decimal_spelling_and_signed_zero_do_not_change_fitted_identity():
    from options_lab.volume import fit_volume_baseline
    manifest, partition, groups = numeric_inputs()
    original = fit_volume_baseline(groups, AT, partition=partition, manifest=manifest)
    changed = tuple(replace(g, bars=tuple(replace(bar, volume=D("-0.000")) if bar.volume == 0 else bar for bar in g.bars)) for g in groups)
    result = fit_volume_baseline(changed, AT, partition=partition, manifest=manifest)
    assert result.baseline == original.baseline
    assert result.buckets[4].snapshot["mean"] == result.buckets[4].snapshot["population_stddev"] == "0"


def test_normalization_id_commits_to_its_actual_complete_definition():
    baseline = fit().baseline
    snapshot = baseline.normalization_snapshot
    encoded = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    assert baseline.normalization_id == hashlib.sha256(encoded).hexdigest()
    snapshot["numeric_convention"]["working_precision"] = 2
    assert baseline.normalization_snapshot["numeric_convention"]["working_precision"] == 80


def test_actual_calendar_first_minute_remains_one_across_dst_when_fitting():
    result = fit("dst")
    assert result.reasons == () and len(result.buckets) == 1
    bucket = result.buckets[0]
    assert (bucket.minute_index, bucket.sample_count, bucket.mean, bucket.population_stddev) == (1, 2, D(900), D(0))
    assert bucket.contributing_sessions == result.inputs.partition.training_sessions


def test_raw_omission_is_hashed_before_fit_and_reduces_only_its_bucket():
    result = fit("raw-volume")
    assert result.reasons == () and result.baseline is not None
    assert result.inputs.training_input_hash is not None
    buckets = {b.minute_index: b for b in result.buckets}
    assert buckets[35].sample_count == 19 and not buckets[35].ready
    assert buckets[34].sample_count == 20 and buckets[34].ready
