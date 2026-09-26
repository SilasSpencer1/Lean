"""Actual admitted calendar and fit membership retain complete source facts."""

import importlib.util
from copy import copy, deepcopy
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from options_lab.admission import _canonical_bytes, verify_fixture_bundle
from options_lab.bundle_inputs import normalize_model_bytes


FOLDER = Path(__file__).parent / "fixtures"
RECEIPT = dict(event_id="calibration-test", raw_ref="synthetic://calibration-test",
               received_at=datetime(2026, 9, 30, tzinfo=timezone.utc))


def admitted(name):
    """Load only actual current catalog backed fixture bytes."""
    result = verify_fixture_bundle(name, (FOLDER / (name + ".json")).read_bytes(), **RECEIPT)
    assert result.value is not None, result.rejection
    return result.value


def model():
    """Inspect exact current fixed-right bytes from existing bundle fixture."""
    fixture = admitted("p14a-bundle-content-v1")
    body = next(m for m in fixture.members if m.record_id == "fixed").decode_raw_body()
    return normalize_model_bytes(body["model_utf8"].encode(), **RECEIPT).value


def api():
    """Require the concrete owner before exercising its admitted public boundaries."""
    assert importlib.util.find_spec("options_lab.calibration_inputs") is not None
    from options_lab import calibration_inputs
    return calibration_inputs


def test_calendar_and_membership_have_concrete_public_normalizers():
    assert callable(api().normalize_calendar_descriptor)
    assert callable(api().normalize_fit_membership)


def test_complete_calendar_preserves_early_close_and_september_tail():
    source, fixture = admitted("p14c-fit-sources-v1"), admitted("p14c-fit-membership-v1")
    body = next(m for m in fixture.members if m.record_id == "calendar").decode_raw_body()
    result = api().normalize_calendar_descriptor(body, fixture=fixture, record_id="calendar",
                                                  upstream_fixtures=(source,))
    assert result.value is not None, result.rejection
    value = result.value
    assert len(value.sessions) + len(value.closed_dates) == 150
    assert value.sessions[-1].session.session_date.isoformat() == "2026-09-30"
    assert any(item.session.kind == "early_close" and item.session.session_date.isoformat() == "2026-07-02"
               for item in value.sessions)
    assert value.available_at is None
    assert value.content_hash and value.content_hash == api()._snapshot_hash(value.snapshot())


def test_both_memberships_keep_all_rows_and_adverse_times():
    source, fixture = admitted("p14c-fit-sources-v1"), admitted("p14c-fit-membership-v1")
    for record, role in (("model", "model"), ("tuning", "tuning")):
        body = next(m for m in fixture.members if m.record_id == record).decode_raw_body()
        result = api().normalize_fit_membership(body, fixture=fixture, record_id=record,
            model=model(), upstream_fixtures=(source,))
        assert result.value is not None, result.rejection
        value = result.value
        assert value.role == role and len(value.rows) == 2
        assert [row.bucket_id for row in value.rows] == [row.contract.right for row in value.rows]
        assert value.content_hash == api()._snapshot_hash(value.snapshot())
        if role == "tuning":
            assert value.rows[0].available_at is None and value.rows[1].label_available_at < value.rows[1].feature_available_at


def test_registered_adverse_members_are_exclusive_safe_rejections():
    source, fixture = admitted("p14c-fit-sources-v1"), admitted("p14c-fit-membership-v1")
    for record, expected in (("model-duplicate", "duplicate_reference"),
                             ("model-wrong-hash", "raw_hash_mismatch"),
                             ("model-bad-bucket", "bucket_mismatch")):
        body = next(m for m in fixture.members if m.record_id == record).decode_raw_body()
        result = api().normalize_fit_membership(body, fixture=fixture, record_id=record,
            model=model(), upstream_fixtures=(source,))
        assert result.value is None and result.rejection.code == expected
    body = next(m for m in fixture.members if m.record_id == "calendar-incomplete").decode_raw_body()
    result = api().normalize_calendar_descriptor(body, fixture=fixture, record_id="calendar-incomplete",
                                                  upstream_fixtures=(source,))
    assert result.value is None and result.rejection.code == "incomplete_coverage"


def test_trusted_types_and_factory_only_immutable_values():
    source, fixture = admitted("p14c-fit-sources-v1"), admitted("p14c-fit-membership-v1")
    body = next(m for m in fixture.members if m.record_id == "model").decode_raw_body()
    call = api().normalize_fit_membership
    for changed in (dict(fixture=object()), dict(record_id=1), dict(upstream_fixtures=[source]),
                    dict(upstream_fixtures=(object(),)), dict(model=object())):
        arguments = dict(fixture=fixture, record_id="model", model=model(), upstream_fixtures=(source,)) | changed
        with pytest.raises((TypeError, ValueError)):
            call(body, **arguments)
    result = call(body, fixture=fixture, record_id="model", model=model(), upstream_fixtures=(source,))
    for value in (result, result.value, result.value.rows[0], result.value.sample_refs[0]):
        with pytest.raises(TypeError):
            type(value)()
        with pytest.raises(TypeError):
            replace(value)
        with pytest.raises(FrozenInstanceError):
            value.extra = True


def test_hostile_raw_leaves_fail_before_equality_or_iteration():
    source, fixture = admitted("p14c-fit-sources-v1"), admitted("p14c-fit-membership-v1")
    class Hostile:
        def __eq__(self, other):
            raise AssertionError("hostile equality ran")

        def __repr__(self):
            raise AssertionError("hostile repr ran")

    calendar = next(m for m in fixture.members if m.record_id == "calendar").decode_raw_body()
    calendar["calendar"] = Hostile()
    result = api().normalize_calendar_descriptor(calendar, fixture=fixture, record_id="calendar",
                                                  upstream_fixtures=(source,))
    assert result.value is None and result.rejection.code == "invalid_value"
    body = next(m for m in fixture.members if m.record_id == "model").decode_raw_body()
    body["sample_refs"] = [Hostile()]
    result = api().normalize_fit_membership(body, fixture=fixture, record_id="model", model=model(),
                                            upstream_fixtures=(source,))
    assert result.value is None and result.rejection.code == "expected_exact_dict"
    parsed = copy(model())
    row = copy(parsed.rows[0])
    object.__setattr__(row, "mean_attempt_return", Hostile())
    object.__setattr__(parsed, "rows", (row, parsed.rows[1]))
    result = api().normalize_fit_membership(next(m for m in fixture.members if m.record_id == "model").decode_raw_body(),
        fixture=fixture, record_id="model", model=parsed, upstream_fixtures=(source,))
    assert result.value is None and result.rejection.code == "retained_content_mismatch"


def test_limits_and_actual_counterparts_precede_authority():
    source, fixture = admitted("p14c-fit-sources-v1"), admitted("p14c-fit-membership-v1")
    calendar = next(m for m in fixture.members if m.record_id == "calendar").decode_raw_body()
    huge = deepcopy(calendar)
    huge["coverage_end_date"] = "2040-01-01"
    result = api().normalize_calendar_descriptor(huge, fixture=fixture, record_id="calendar",
                                                  upstream_fixtures=(source,))
    assert result.value is None and result.rejection.code == "resource_limit"
    unordered = deepcopy(calendar)
    unordered["closed_dates"][:2] = reversed(unordered["closed_dates"][:2])
    result = api().normalize_calendar_descriptor(unordered, fixture=fixture, record_id="calendar",
                                                  upstream_fixtures=(source,))
    assert result.value is None and result.rejection.code == "invalid_value"
    body = next(m for m in fixture.members if m.record_id == "model").decode_raw_body()
    long = deepcopy(body)
    long["sample_refs"] *= 4097
    result = api().normalize_fit_membership(long, fixture=fixture, record_id="model", model=model(),
                                            upstream_fixtures=(source,))
    assert result.value is None and result.rejection.code == "resource_limit"
    for roots, code in (((source, source), "duplicate_reference"),
                        ((source, admitted("p08a-greek-ready-v1")), "unused_reference"),
                        ((source, fixture), "containing_reference")):
        result = api().normalize_fit_membership(body, fixture=fixture, record_id="model", model=model(),
                                                upstream_fixtures=roots)
        assert result.value is None and result.rejection.code == code
    changed = copy(model())
    object.__setattr__(changed, "model_hash", "0" * 64)
    result = api().normalize_fit_membership(body, fixture=fixture, record_id="model", model=changed,
                                            upstream_fixtures=(source,))
    assert result.value is None and result.rejection.code == "model_mismatch"


def test_corrupted_retained_envelope_still_returns_safe_rejection():
    """A copied owner cannot make rejection reporting decode its corrupt receipt."""
    source, fixture = admitted("p14c-fit-sources-v1"), admitted("p14c-fit-membership-v1")
    copied = copy(fixture)
    member = copy(next(m for m in fixture.members if m.record_id == "model"))
    object.__setattr__(member, "envelope_bytes", b"{")
    object.__setattr__(copied, "members", tuple(member if m.record_id == "model" else m for m in fixture.members))
    body = next(m for m in fixture.members if m.record_id == "model").decode_raw_body()
    result = api().normalize_fit_membership(body, fixture=copied, record_id="model", model=model(),
                                            upstream_fixtures=(source,))
    assert result.value is None and result.rejection.code == "retained_content_mismatch"
    assert result.rejection.event_id == RECEIPT["event_id"]
    assert result.rejection.member is None
    forged = copy(fixture)
    row = copy(next(m for m in fixture.members if m.record_id == "calendar"))
    envelope = row.decode_envelope()
    envelope["event_id"] = "forged-event"
    object.__setattr__(row, "envelope_bytes", _canonical_bytes(envelope))
    object.__setattr__(forged, "members", tuple(row if m.record_id == "calendar" else m for m in fixture.members))
    result = api().normalize_calendar_descriptor({}, fixture=forged, record_id="calendar")
    assert result.value is None and result.rejection.event_id == RECEIPT["event_id"]
    assert result.rejection.member is None


def test_large_exact_body_rejects_before_canonical_serialization():
    """Finite references can collectively exceed the separate 8 MiB body cap."""
    source, fixture = admitted("p14c-fit-sources-v1"), admitted("p14c-fit-membership-v1")
    body = next(m for m in fixture.members if m.record_id == "model").decode_raw_body()
    seed = body["sample_refs"][0]
    body["sample_refs"] = [seed | {"fixture_id": "f" * 1020 + f"{i:04d}",
                                  "record_id": "r" * 1020 + f"{i:04d}"} for i in range(4096)]
    result = api().normalize_fit_membership(body, fixture=fixture, record_id="model", model=model(),
                                            upstream_fixtures=(source,))
    assert result.value is None and result.rejection.code == "resource_limit"


def test_json_size_preflight_counts_escape_boundaries_exactly():
    """The resource guard matches the owned ASCII JSON encoder at edge code points."""
    for value in ("\x00", "\b", "\t", "\n", "\f", "\r", "\x1f", '"', "\\",
                  "\x7e", "\x7f", "\x80", "\uffff", "\U00010000"):
        raw = {"key": [value, value + "x"]}
        assert api()._body_size(raw) == len(_canonical_bytes(raw))
