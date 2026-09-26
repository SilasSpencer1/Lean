"""Fresh context recheck uses the original admitted request and P06 history."""

from copy import copy
from decimal import Decimal
from datetime import datetime, timedelta, timezone, tzinfo
import importlib
import importlib.util

import pytest

from options_lab.config import StrategyConfig
from options_lab.context import build_decision_context
from options_lab.context_inputs import normalize_context_request
from test_context import build as partial_context, scenario
from test_feature_vector import context as market_context


NOW = datetime(2026, 9, 4, 14, 5, tzinfo=timezone.utc)


def recheck(value, *, now=NOW, config=None):
    """Import the real source owner after test setup to expose missing API as RED."""
    assert importlib.util.find_spec("options_lab.context_recheck") is not None
    return importlib.import_module("options_lab.context_recheck").recheck_decision_context(
        value, config=StrategyConfig() if config is None else config, now=now)


def test_context_retains_original_request_for_fresh_source_rebuild():
    """The context owner keeps its exact request instead of inferred member IDs."""
    result = market_context()
    assert result.context is not None
    assert result.context.request is result.request
    assert result.context.previous_feature_state is result.previous_feature_state


def test_recheck_rederives_actual_registered_current_context():
    """A sound current context survives fresh A0 admission and full comparison."""
    result = market_context()
    assert result.context is not None
    checked = recheck(result.context)
    assert checked.valid and checked.reasons == ()
    assert checked.context is not result.context
    assert checked.context.input_digest == result.context.input_digest
    assert checked.context.manifest is not result.manifest


def test_recheck_replays_real_retained_bar_state_before_context_rebuild():
    """P06 prior history remains usable after a source-bound replay."""
    original = market_context()
    assert original.context is not None and original.context.feature_state is not None
    later = NOW + timedelta(seconds=1)
    request = normalize_context_request(dict(decision_id="later", decision_at=later.isoformat(),
        member_record_ids=["session", "bars-correction", "good-option_quote", "good-underlying_quote",
                           "good-greek", "good-proof", "good-instrument_tradability",
                           "good-provider_contract_mapping", "good-contract_reference"]),
        event_id="request", raw_ref="request", received_at=later)
    retained = build_decision_context(request, original.manifest, config=StrategyConfig(),
        previous_feature_state=original.context.feature_state)
    assert retained.context is not None and retained.context.input_digest is not None
    assert "previous_state_unverified" not in retained.context.input_reasons
    checked = recheck(retained.context, now=later)
    assert checked.valid and checked.reasons == ()
    assert checked.previous_feature_state is not retained.previous_feature_state
    assert checked.context.input_digest == retained.context.input_digest


def test_recheck_rejects_tampered_retained_request_without_using_it_as_authority():
    """A copied member selection cannot be trusted over real original bytes."""
    result = market_context()
    assert result.context is not None
    altered = copy(result.context)
    request = copy(result.request)
    object.__setattr__(request, "member_record_ids", ("session",))
    object.__setattr__(altered, "request", request)
    checked = recheck(altered)
    assert not checked.valid and "retained_context_mismatch" in checked.reasons


def test_recheck_replays_retired_correction_and_keeps_equivalent_duplicate_audit():
    """A corrected P06 history and P08 duplicate survive their actual owners."""
    first = market_context()
    assert first.context is not None and first.context.feature_state is not None
    ids = ["session", "bars-correction", *[m.record_id for m in first.manifest.members
           if m.record_id.startswith("good-")]]
    previous = first.context.feature_state
    for second in (1, 2):
        at = NOW + timedelta(seconds=second)
        request = normalize_context_request(dict(decision_id=str(second), decision_at=at.isoformat(),
            member_record_ids=ids), event_id="request", raw_ref="request", received_at=at)
        result = build_decision_context(request, first.manifest, config=StrategyConfig(),
                                        previous_feature_state=previous)
        assert result.context is not None and result.context.input_reasons == ()
        assert recheck(result.context, now=at).valid
        previous = result.context.feature_state
    assert previous is not None and len(previous.retired_bars) == 1

    duplicated = scenario("duplicate")
    assert duplicated.context is not None
    assert any(c.disposition == "duplicate" for c in duplicated.context.components)
    assert recheck(duplicated.context, now=duplicated.context.decision_at).valid


def test_recheck_rejects_partial_request_changed_config_and_clock():
    """No fresh proof is granted when original request errors or bindings differ."""
    original = market_context()
    assert original.context is not None
    assert recheck(original.context, now=NOW + timedelta(seconds=1)).reasons == ("context_time_mismatch",)
    changed = recheck(original.context, config=StrategyConfig(max_entries_per_session=2))
    assert changed.reasons == ("retained_context_mismatch",)
    request = normalize_context_request(dict(decision_id="partial", decision_at=NOW.isoformat(),
        member_record_ids=["session", 1]), event_id="request", raw_ref="request", received_at=NOW)
    partial = build_decision_context(request, original.manifest, config=StrategyConfig(),
                                     previous_feature_state=None)
    assert partial.context is not None and request.rejections
    assert recheck(partial.context).reasons == ("retained_request_mismatch",)


def test_recheck_rejects_damaged_manifest_and_prior_without_hostile_equality():
    """Nested wrong trusted types remain bounded before unsafe owner equality."""
    class Hostile:
        def __eq__(self, other):
            raise AssertionError("hostile equality called")

    original = market_context()
    assert original.context is not None and original.context.feature_state is not None
    damaged = copy(original.context)
    manifest = copy(original.manifest)
    object.__setattr__(manifest, "payload_bytes", b"{}")
    object.__setattr__(damaged, "manifest", manifest)
    assert recheck(damaged).reasons == ("fresh_manifest_rejected",)

    prior = copy(original.context.feature_state)
    bar = copy(prior.bars[0])
    object.__setattr__(bar, "close_price", Hostile())
    object.__setattr__(prior, "bars", (bar, *prior.bars[1:]))
    damaged = copy(original.context)
    object.__setattr__(damaged, "previous_feature_state", prior)
    assert recheck(damaged).reasons == ("previous_state_replay_failed",)


def test_recheck_rejects_damaged_nested_config_before_rebuilding():
    """A copied trusted config leaf cannot call arithmetic or equality hooks."""
    class Hostile:
        def __eq__(self, other):
            raise AssertionError("hostile equality called")

    original = market_context()
    assert original.context is not None
    config = copy(StrategyConfig())
    object.__setattr__(config, "min_abs_delta", Hostile())
    assert recheck(original.context, config=config).reasons == ("retained_config_mismatch",)
    config = copy(StrategyConfig())
    object.__setattr__(config, "min_abs_delta", Decimal("sNaN"))
    assert recheck(original.context, config=config).reasons == ("retained_config_mismatch",)


def test_recheck_preserves_truthful_partial_market_context_without_granting_readiness():
    """Reproduction of partial facts does not erase their owner input reasons."""
    original = partial_context(["greek-member-1"])
    assert original.context is not None and original.context.input_reasons
    checked = recheck(original.context, now=original.context.decision_at)
    assert checked.valid
    assert checked.context.input_reasons == original.context.input_reasons


def test_recheck_rejects_prior_clock_hash_and_missing_request_fields():
    """Neither copied cursor identity nor unavailable prior bars can pass replay."""
    original = market_context()
    assert original.context is not None and original.context.feature_state is not None
    for field, replacement in (("input_hash", "0" * 64),
                               ("as_of", NOW - timedelta(seconds=1))):
        old = copy(original.context.feature_state)
        object.__setattr__(old, field, replacement)
        damaged = copy(original.context)
        object.__setattr__(damaged, "previous_feature_state", old)
        assert recheck(damaged).reasons == ("previous_state_replay_failed",)
    request = copy(original.request)
    object.__delattr__(request, "member_record_ids")
    damaged = copy(original.context)
    object.__setattr__(damaged, "request", request)
    assert recheck(damaged).reasons == ("retained_request_mismatch",)


def test_recheck_guards_hostile_timezone_and_top_level_types():
    """Unrecognized tzinfo does not execute hooks and ordinary wrong types raise."""
    class HostileZone(tzinfo):
        def utcoffset(self, value):
            raise AssertionError("hostile timezone called")

    original = market_context()
    assert original.context is not None
    damaged = copy(original.context)
    object.__setattr__(damaged, "decision_at", NOW.replace(tzinfo=HostileZone()))
    assert recheck(damaged).reasons == ("context_time_mismatch",)
    owner = importlib.import_module("options_lab.context_recheck")
    for kwargs in (dict(context=object(), config=StrategyConfig(), now=NOW),
                   dict(context=original.context, config=object(), now=NOW),
                   dict(context=original.context, config=StrategyConfig(), now=object())):
        with pytest.raises(TypeError):
            owner.recheck_decision_context(**kwargs)
    with pytest.raises(ValueError):
        recheck(original.context, now=NOW.replace(tzinfo=HostileZone()))


@pytest.mark.parametrize("field,replacement", [
    ("event_id", ""),
    ("raw_ref", ""),
    ("received_at", datetime(1, 1, 1, tzinfo=timezone(timedelta(hours=14)))),
])
def test_recheck_bounds_damaged_source_receipt_before_trusted_admission(field, replacement):
    """Malformed copied receipt fields return a reason before A0 raises."""
    original = market_context()
    assert original.context is not None
    manifest = copy(original.manifest)
    object.__setattr__(manifest, field, replacement)
    damaged = copy(original.context)
    object.__setattr__(damaged, "manifest", manifest)
    assert recheck(damaged).reasons == ("retained_manifest_mismatch",)


@pytest.mark.parametrize("field,multiplier", [
    ("members", 12),
    ("coherence_protocol_ids", 2049),
    ("tick_definition_ids", 4097),
])
def test_recheck_bounds_retained_source_collections_before_scanning(field, multiplier):
    """Oversized copied tuples cannot make preflight traverse unbounded facts."""
    original = market_context()
    assert original.context is not None
    manifest = copy(original.manifest)
    object.__setattr__(manifest, field, getattr(manifest, field) * multiplier)
    from options_lab.calibration_inputs import _retained_fixture_shape
    assert not _retained_fixture_shape(manifest)
    damaged = copy(original.context)
    object.__setattr__(damaged, "manifest", manifest)
    assert recheck(damaged).reasons == ("retained_manifest_mismatch",)
