from dataclasses import FrozenInstanceError, replace
from datetime import timedelta
from decimal import Decimal, localcontext

import pytest

from options_lab.bars import UnderlyingBar
from options_lab.features import FeatureState, FeatureUpdate, update_features

from factories import CLOSE, OPEN, bar, session


def advance(state: FeatureState, observed: UnderlyingBar, calendar=None, as_of=None):
    """Apply one bar at its availability unless an explicit cutoff is supplied."""
    return update_features(
        state,
        observed,
        calendar or state.session,
        as_of=as_of or observed.available_at,
    )


def test_empty_state_and_accepted_append_are_immutable_and_deterministic() -> None:
    calendar = session()
    initial = FeatureState(calendar, as_of=OPEN)
    observed = bar()
    result = advance(initial, observed)

    assert initial.bars == initial.retired_bars == ()
    assert initial.last_event_order is None
    assert result.outcome == "accepted" and result.update_reasons == ()
    assert result.previous_state is initial and result.input_bar is observed
    assert result.assessment.bar is observed
    assert result.next_state.bars == (observed,)
    assert result.next_state.retired_bars == ()
    assert result.next_state.last_event_order == (
        observed.available_at,
        observed.receive_sequence,
    )
    assert result.next_state.as_of == observed.available_at
    assert initial.bars == ()
    assert FeatureState(calendar, as_of=OPEN).input_hash == initial.input_hash
    with pytest.raises(FrozenInstanceError):
        result.next_state.bars = ()


def test_update_record_is_the_only_derived_result_owner() -> None:
    initial = FeatureState(session(), as_of=OPEN)
    observed = bar()
    direct = FeatureUpdate(initial, observed, initial.session, as_of=observed.available_at)
    delegated = update_features(initial, observed, initial.session, as_of=observed.available_at)
    assert direct == delegated
    with pytest.raises(TypeError):
        FeatureUpdate(initial, observed, initial.session, observed.available_at)


def test_unsuitable_input_recomputes_assessment_and_preserves_state() -> None:
    initial = FeatureState(session(), as_of=OPEN)
    observed = bar(close_price=None, volume=None)
    result = advance(initial, observed)
    assert result.outcome == "rejected"
    assert result.update_reasons == ("close_price_missing",)
    assert result.assessment.price_history_reasons == ("close_price_missing",)
    assert result.assessment.volume_reasons == ("volume_missing",)
    assert result.next_state is initial
    assert result.input_bar is observed


def test_missing_volume_and_vwap_still_append_usable_close() -> None:
    initial = FeatureState(session(), as_of=OPEN)
    observed = bar(
        volume=None,
        volume_definition_id=None,
        vwap_numerator=None,
        vwap_denominator=None,
        vwap_definition_id=None,
    )
    result = advance(initial, observed)
    assert result.outcome == "accepted"
    assert result.assessment.price_history_suitable
    assert not result.assessment.volume_available
    assert not result.assessment.exact_vwap_available
    assert result.next_state.bars == (observed,)


def test_gap_appends_but_late_missing_interval_is_rejected() -> None:
    initial = FeatureState(session(), as_of=OPEN)
    first = advance(initial, bar(0)).next_state
    gapped = advance(first, bar(2)).next_state
    late = bar(
        1,
        receive_sequence=4,
        meta_changes={"available_at": gapped.bars[-1].available_at + timedelta(seconds=1)},
    )
    result = advance(gapped, late)
    assert tuple(item.interval_start for item in gapped.bars) == (
        OPEN,
        OPEN + timedelta(minutes=2),
    )
    assert result.outcome == "rejected"
    assert result.update_reasons == ("late_backfill",)
    assert result.next_state is gapped


def test_exact_duplicate_ignores_receipt_locator_and_sequence() -> None:
    initial = FeatureState(session(), as_of=OPEN)
    accepted = bar()
    current = advance(initial, accepted).next_state
    duplicate = replace(
        accepted,
        meta=replace(
            accepted.meta,
            raw_ref="synthetic://redelivery",
            received_at=accepted.meta.received_at + timedelta(hours=1),
        ),
        receive_sequence=999,
    )
    result = advance(current, duplicate, as_of=duplicate.available_at + timedelta(hours=1))
    assert result.outcome == "duplicate"
    assert result.update_reasons == ("duplicate",)
    assert result.next_state is current
    assert result.next_state.input_hash == current.input_hash
    assert result.input_bar is duplicate


@pytest.mark.parametrize(
    "changed",
    [
        {"close_price": Decimal("101")},
        {"meta_changes": {"available_at": OPEN + timedelta(minutes=1, seconds=2)}},
    ],
)
def test_changed_content_under_same_source_identity_conflicts(changed) -> None:
    initial = FeatureState(session(), as_of=OPEN)
    current = advance(initial, bar()).next_state
    result = advance(current, bar(**changed), as_of=OPEN + timedelta(hours=1))
    assert result.outcome == "rejected"
    assert result.update_reasons == ("source_identity_conflict",)
    assert result.next_state is current


def test_assessment_failure_precedes_identity_comparison() -> None:
    initial = FeatureState(session(), as_of=OPEN)
    current = advance(initial, bar()).next_state
    changed = bar(meta_changes={"quality_flags": ("suspect",)})
    result = advance(current, changed, as_of=OPEN + timedelta(hours=1))
    assert result.update_reasons == ("quality_flags_present",)


def test_event_order_uses_availability_then_explicit_sequence() -> None:
    initial = FeatureState(session(), as_of=OPEN)
    first_bar = bar(
        0,
        receive_sequence=4,
        meta_changes={"available_at": OPEN + timedelta(minutes=10)},
    )
    current = advance(initial, first_bar, as_of=first_bar.available_at).next_state
    same_time = first_bar.available_at

    earlier = bar(1, meta_changes={"available_at": OPEN + timedelta(minutes=2)})
    assert advance(current, earlier, as_of=OPEN + timedelta(minutes=11)).update_reasons == (
        "out_of_order",
    )

    for sequence in (None, 4):
        tied = bar(1, receive_sequence=sequence, meta_changes={"available_at": same_time})
        result = advance(current, tied, as_of=OPEN + timedelta(hours=1))
        assert result.update_reasons == ("event_order_ambiguous",)

    lower = bar(1, receive_sequence=3, meta_changes={"available_at": same_time})
    assert advance(current, lower, as_of=OPEN + timedelta(hours=1)).update_reasons == (
        "out_of_order",
    )

    ordered = bar(1, receive_sequence=5, meta_changes={"available_at": same_time})
    assert advance(current, ordered, as_of=OPEN + timedelta(hours=1)).outcome == "accepted"


def test_state_time_session_and_price_stream_changes_reject() -> None:
    calendar = session()
    accepted = bar(meta_changes={"available_at": OPEN + timedelta(minutes=10)})
    current = advance(
        FeatureState(calendar, as_of=OPEN), accepted, as_of=accepted.available_at
    ).next_state
    later = bar(1, meta_changes={"available_at": OPEN + timedelta(minutes=11)})
    assert advance(current, later, as_of=OPEN - timedelta(seconds=1)).update_reasons == (
        "state_time_regression",
    )
    other_session = session(provider_record_id="revised-calendar")
    assert advance(current, later, calendar=other_session).update_reasons == (
        "session_mismatch",
    )
    changed_source = bar(
        1,
        meta_changes={
            "source": "other-bars",
            "available_at": OPEN + timedelta(minutes=11),
        },
    )
    assert advance(current, changed_source).update_reasons == ("price_stream_changed",)

    old_changed_source = bar(
        1,
        meta_changes={
            "source": "other-bars",
            "available_at": OPEN + timedelta(minutes=2),
        },
    )
    assert advance(
        current,
        old_changed_source,
        as_of=current.bars[-1].available_at + timedelta(hours=1),
    ).update_reasons == ("out_of_order",)


def test_valid_current_revision_corrects_without_mutating_old_state() -> None:
    initial = FeatureState(session(), as_of=OPEN)
    original = bar()
    old_state = advance(initial, original).next_state
    correction = bar(
        close_price=Decimal("101"),
        revision_id="r2",
        supersedes_revision_id="r1",
        receive_sequence=2,
        meta_changes={"available_at": original.available_at + timedelta(seconds=1)},
    )
    result = advance(old_state, correction)
    assert result.outcome == "corrected" and result.update_reasons == ()
    assert result.next_state.bars == (correction,)
    assert result.next_state.retired_bars == (original,)
    assert result.next_state.input_hash != old_state.input_hash
    assert old_state.bars == (original,) and old_state.retired_bars == ()


def test_future_or_invalid_lineage_correction_does_not_retire_selected_bar() -> None:
    initial = FeatureState(session(), as_of=OPEN)
    original = bar()
    current = advance(initial, original).next_state
    correction = bar(
        revision_id="r2",
        supersedes_revision_id=None,
        receive_sequence=2,
        meta_changes={"available_at": original.available_at + timedelta(seconds=5)},
    )
    unavailable = advance(current, correction, as_of=original.available_at)
    assert unavailable.outcome == "rejected"
    assert "available_after_decision" in unavailable.update_reasons
    assert unavailable.next_state.retired_bars == ()

    invalid = advance(current, correction)
    assert invalid.update_reasons == ("revision_lineage_mismatch",)
    assert invalid.next_state is current and invalid.next_state.retired_bars == ()


def test_retired_identities_remain_duplicate_or_conflict_without_rollback() -> None:
    initial = FeatureState(session(), as_of=OPEN)
    first = bar()
    state_a = advance(initial, first).next_state
    second = bar(
        revision_id="r2",
        supersedes_revision_id="r1",
        receive_sequence=2,
        meta_changes={"available_at": first.available_at + timedelta(seconds=1)},
    )
    state_b = advance(state_a, second).next_state
    redelivery = replace(
        first,
        meta=replace(first.meta, raw_ref="synthetic://old-redelivery"),
        receive_sequence=100,
    )
    duplicate = advance(state_b, redelivery, as_of=OPEN + timedelta(hours=1))
    assert duplicate.outcome == "duplicate"
    assert duplicate.next_state is state_b
    assert duplicate.next_state.bars == (second,)
    assert duplicate.next_state.retired_bars == (first,)

    moved = replace(
        first,
        meta=replace(
            first.meta,
            interval_start=OPEN + timedelta(minutes=3),
            interval_end=OPEN + timedelta(minutes=4),
            available_at=OPEN + timedelta(minutes=4),
        ),
    )
    conflict = advance(state_b, moved, as_of=OPEN + timedelta(hours=1))
    assert conflict.update_reasons == ("source_identity_conflict",)
    assert conflict.next_state is state_b


def test_correction_must_supersede_current_selected_revision() -> None:
    initial = FeatureState(session(), as_of=OPEN)
    first = bar()
    state_a = advance(initial, first).next_state
    second = bar(
        revision_id="r2",
        supersedes_revision_id="r1",
        receive_sequence=2,
        meta_changes={"available_at": first.available_at + timedelta(seconds=1)},
    )
    state_b = advance(state_a, second).next_state
    branch = bar(
        revision_id="r3",
        supersedes_revision_id="r1",
        receive_sequence=3,
        meta_changes={"available_at": first.available_at + timedelta(seconds=2)},
    )
    result = advance(state_b, branch)
    assert result.update_reasons == ("revision_lineage_mismatch",)
    assert result.next_state is state_b


def test_multiple_replacements_keep_every_accepted_identity() -> None:
    initial = FeatureState(session(), as_of=OPEN)
    revisions = [bar()]
    current = advance(initial, revisions[0]).next_state
    for number in range(2, 5):
        prior = revisions[-1]
        revised = bar(
            revision_id=f"r{number}",
            supersedes_revision_id=prior.revision_id,
            receive_sequence=number,
            meta_changes={"available_at": prior.available_at + timedelta(seconds=1)},
        )
        revisions.append(revised)
        current = advance(current, revised).next_state
    assert current.bars == (revisions[-1],)
    assert current.retired_bars == tuple(revisions[:-1])

    for prior in revisions[:-1]:
        assert advance(current, prior, as_of=OPEN + timedelta(hours=1)).outcome == "duplicate"
        changed = replace(prior, close_price=prior.close_price + Decimal("1"))
        assert advance(current, changed, as_of=OPEN + timedelta(hours=1)).update_reasons == (
            "source_identity_conflict",
        )


def test_ordinary_append_preserves_retired_evidence_and_selected_only_hash() -> None:
    initial = FeatureState(session(), as_of=OPEN)
    first = bar()
    state_a = advance(initial, first).next_state
    correction = bar(
        revision_id="r2",
        supersedes_revision_id="r1",
        receive_sequence=2,
        meta_changes={"available_at": first.available_at + timedelta(seconds=1)},
    )
    corrected = advance(state_a, correction).next_state
    appended = advance(corrected, bar(1, receive_sequence=3)).next_state
    assert appended.retired_bars == (first,)
    assert appended.bars == (correction, bar(1, receive_sequence=3))
    duplicate = advance(appended, first, as_of=OPEN + timedelta(hours=1))
    assert duplicate.next_state.input_hash == appended.input_hash


def test_retired_history_is_excluded_from_selected_input_hash() -> None:
    calendar = session()
    selected = bar(
        revision_id="r2",
        supersedes_revision_id="r1",
        receive_sequence=2,
        meta_changes={"available_at": OPEN + timedelta(minutes=2)},
    )
    old_a = bar(provider_record_id="old-a", close_price=Decimal("99"))
    old_b = bar(provider_record_id="old-b", close_price=Decimal("101"))

    state_a = advance(FeatureState(calendar, as_of=OPEN), old_a).next_state
    state_b = advance(FeatureState(calendar, as_of=OPEN), old_b).next_state
    corrected_a = advance(state_a, selected).next_state
    corrected_b = advance(state_b, selected).next_state

    assert corrected_a.bars == corrected_b.bars == (selected,)
    assert corrected_a.retired_bars != corrected_b.retired_bars
    assert corrected_a.input_hash == corrected_b.input_hash
    duplicate = replace(
        selected,
        meta=replace(selected.meta, raw_ref="synthetic://correction-redelivery"),
        receive_sequence=99,
    )
    repeated = advance(corrected_a, duplicate, as_of=OPEN + timedelta(hours=1))
    assert repeated.outcome == "duplicate"
    assert repeated.next_state.retired_bars == (old_a,)


def test_30_and_31_closes_and_a_gap_remain_explicit_in_selected_history() -> None:
    calendar = session()
    current = FeatureState(calendar, as_of=OPEN)
    for minute in range(30):
        current = advance(current, bar(minute)).next_state
    assert len(current.bars) == 30
    current = advance(current, bar(30)).next_state
    assert len(current.bars) == 31
    assert all(
        right.interval_start - left.interval_start == timedelta(minutes=1)
        for left, right in zip(current.bars, current.bars[1:])
    )

    gapped = FeatureState(calendar, as_of=OPEN)
    for minute in (*range(15), *range(16, 32)):
        gapped = advance(gapped, bar(minute)).next_state
    assert len(gapped.bars) == 31
    assert gapped.bars[15].interval_start - gapped.bars[14].interval_start == timedelta(minutes=2)


def test_component_definition_changes_do_not_change_the_raw_price_stream() -> None:
    initial = FeatureState(session(), as_of=OPEN)
    first = advance(initial, bar()).next_state
    changed = bar(
        1,
        volume_definition_id="other-volume",
        vwap_definition_id="other-vwap",
        volume=None,
        vwap_numerator=None,
        vwap_denominator=None,
    )
    result = advance(first, changed)
    assert result.outcome == "accepted"
    assert result.assessment.price_history_suitable
    assert not result.assessment.volume_available
    assert not result.assessment.exact_vwap_available


def test_revision_without_a_current_interval_cannot_be_appended() -> None:
    initial = FeatureState(session(), as_of=OPEN)
    revised = bar(2, revision_id="r2", supersedes_revision_id="r1")
    result = advance(initial, revised)
    assert result.update_reasons == ("revision_lineage_mismatch",)
    assert result.next_state is initial


def test_shifted_correction_cannot_turn_into_a_later_append() -> None:
    initial = FeatureState(session(), as_of=OPEN)
    original = bar()
    current = advance(initial, original).next_state
    shifted = bar(
        2,
        revision_id="r2",
        supersedes_revision_id="r1",
        receive_sequence=2,
    )
    result = advance(current, shifted)
    assert result.update_reasons == ("revision_lineage_mismatch",)
    assert result.next_state is current


def test_unsupported_economic_identity_is_bounded_rejection_evidence() -> None:
    initial = FeatureState(session(), as_of=OPEN)
    extreme = bar(close_price=Decimal("1e1001"))
    result = advance(initial, extreme)
    assert result.outcome == "rejected"
    assert result.update_reasons == ("decimal_representation_unsupported",)
    assert result.next_state is initial


def test_public_state_cannot_restore_or_supply_derived_history() -> None:
    calendar = session()
    initial = FeatureState(calendar, as_of=OPEN)
    with pytest.raises(TypeError):
        FeatureState(calendar, as_of=OPEN, bars=(bar(),))
    with pytest.raises(FrozenInstanceError):
        initial.input_hash = "caller-supplied"
    assert FeatureState(
        session(provider_record_id="different-session-reference"),
        as_of=OPEN,
    ).input_hash != initial.input_hash


def test_full_regular_session_is_retained_without_truncation() -> None:
    current = FeatureState(session(), as_of=OPEN)
    for minute in range(390):
        current = advance(current, bar(minute)).next_state
    assert len(current.bars) == 390
    assert current.bars[0].interval_start == OPEN
    assert current.bars[-1].interval_end == CLOSE


def test_state_hash_is_semantic_and_independent_of_decimal_context_and_cutoff() -> None:
    calendar = session()
    observed = bar(close_price=Decimal("100.2500"))
    first = advance(FeatureState(calendar, as_of=OPEN), observed).next_state
    equivalent = replace(observed, close_price=Decimal("100.25"))
    with localcontext() as context:
        context.prec = 2
        second = advance(FeatureState(calendar, as_of=OPEN + timedelta(seconds=1)), equivalent).next_state
    assert first.input_hash == second.input_hash
    changed_zero = replace(observed, volume=Decimal("0"))
    assert advance(FeatureState(calendar, as_of=OPEN), changed_zero).next_state.input_hash != first.input_hash


def test_exact_trusted_public_argument_types_are_required() -> None:
    calendar = session()
    initial = FeatureState(calendar, as_of=OPEN)
    observed = bar()
    with pytest.raises(TypeError):
        FeatureState(object(), as_of=OPEN)
    with pytest.raises(ValueError):
        FeatureState(calendar, as_of=OPEN.replace(tzinfo=None))
    with pytest.raises(TypeError):
        update_features(object(), observed, calendar, as_of=observed.available_at)
    with pytest.raises(TypeError):
        update_features(initial, object(), calendar, as_of=observed.available_at)
    with pytest.raises(TypeError):
        update_features(initial, observed, object(), as_of=observed.available_at)


def test_consecutive_tail_preserves_incomplete_opening_prefix() -> None:
    current = FeatureState(session(), as_of=OPEN)
    for minute in (0, *range(2, 33)):
        result = advance(current, bar(minute))
        assert result.outcome == "accepted"
        current = result.next_state
    assert len(current.bars) == 32
    tail = current.bars[-31:]
    assert tail[0].interval_start == OPEN + timedelta(minutes=2)
    assert all(
        right.interval_start == left.interval_end
        for left, right in zip(tail, tail[1:])
    )
    assert current.bars[0].interval_end < tail[0].interval_start
    late = bar(1, meta_changes={"available_at": current.as_of + timedelta(seconds=1)})
    rejected = advance(current, late)
    assert rejected.update_reasons == ("late_backfill",)
    assert rejected.next_state is current


def test_next_session_requires_an_explicit_empty_state() -> None:
    calendar = session()
    original = bar()
    old_state = advance(FeatureState(calendar, as_of=OPEN), original).next_state
    offset = timedelta(days=4)
    next_calendar = replace(
        calendar, session_date=calendar.session_date + offset,
        opens_at=OPEN + offset, closes_at=CLOSE + offset,
        available_at=OPEN + offset, received_at=OPEN + offset,
        provider_record_id="XNYS-next-session", raw_ref="synthetic://next-session",
    )
    next_bar = bar(provider_record_id="next-session-bar", meta_changes={
        "interval_start": original.interval_start + offset,
        "interval_end": original.interval_end + offset,
        "available_at": original.available_at + offset,
        "received_at": original.meta.received_at + offset,
    })
    rejected = advance(old_state, next_bar, calendar=next_calendar)
    assert rejected.update_reasons == ("session_mismatch",)
    assert rejected.next_state is old_state
    fresh = FeatureState(next_calendar, as_of=OPEN + offset)
    accepted = advance(fresh, next_bar)
    assert accepted.outcome == "accepted"
    assert accepted.next_state.bars == (next_bar,)
    assert old_state.bars == (original,)
