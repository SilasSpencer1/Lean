"""Exercise complete partial context-request normalization and safe diagnostics."""

from dataclasses import FrozenInstanceError, fields, replace
from datetime import datetime, timedelta, timezone, tzinfo
from inspect import signature

import pytest

import options_lab as public
import options_lab.context_inputs as api


UTC = timezone.utc
AT = datetime(2026, 9, 6, 14, 30, tzinfo=UTC)
RECEIVED = AT + timedelta(seconds=7)


def body(**changes):
    """Return a fresh valid request with explicit raw overrides."""
    return {"decision_id": "decision-1", "decision_at": "2026-09-06T10:30:00-04:00",
            "member_record_ids": ["member-b", "member-a", "member-b"], **changes}


def normalize(raw, **changes):
    """Call the actual boundary with an independent trusted envelope."""
    envelope = {"event_id": "request-event", "raw_ref": "raw://request", "received_at": RECEIVED}
    return api.normalize_context_request(raw, **(envelope | changes))


def diagnostics(result):
    """Expose actual ordered rejection paths and codes for literal assertions."""
    return tuple((item.field, item.code) for item in result.rejections)


def test_success_retains_header_unique_member_order_and_trusted_envelope():
    result = normalize(body(), received_at=RECEIVED.astimezone(timezone(timedelta(hours=2))))
    assert type(result) is api.ContextRequestValidation
    assert type(result.value) is api.ContextRequest
    assert (result.value.decision_id, result.value.decision_at) == ("decision-1", AT)
    assert result.value.decision_at.tzinfo is UTC
    assert result.member_record_ids == ("member-b", "member-a")
    assert (result.event_id, result.raw_ref, result.received_at) == (
        "request-event", "raw://request", RECEIVED,
    )
    assert result.received_at.tzinfo is UTC
    assert result.rejections == ()


@pytest.mark.parametrize(("raw", "expected"), [
    ({}, (("decision_id", "missing"), ("decision_at", "missing"), ("member_record_ids", "missing"))),
    ({"unexpected-secret": object()}, (("$", "unknown_fields"), ("decision_id", "missing"),
                                      ("decision_at", "missing"), ("member_record_ids", "missing"))),
    ({"decision_id": None, "member_record_ids": [None, "good", "", False]},
     (("decision_at", "missing"), ("decision_id", "invalid_type"),
      ("member_record_ids[0]", "invalid_type"), ("member_record_ids[2]", "invalid_value"),
      ("member_record_ids[3]", "invalid_type"))),
    ({"member_record_ids": ["good", None], "decision_at": "not-a-time", "extra": True},
     (("$", "unknown_fields"), ("decision_id", "missing"), ("decision_at", "invalid_timestamp"),
      ("member_record_ids[1]", "invalid_type"))),
    (body(decision_id="", decision_at=None, member_record_ids={}),
     (("decision_id", "invalid_value"), ("decision_at", "invalid_type"),
      ("member_record_ids", "invalid_type"))),
])
def test_all_independent_failures_follow_root_missing_scalar_then_index_order(raw, expected):
    result = normalize(raw)
    assert result.value is None
    assert diagnostics(result) == expected
    assert result.member_record_ids == (("good",) if "good" in raw.get("member_record_ids", []) else ())


@pytest.mark.parametrize(("changes", "expected"), [
    ({"member_record_ids": None}, (("member_record_ids", "invalid_type"),)),
    ({"member_record_ids": ("member-a",)}, (("member_record_ids", "invalid_type"),)),
    ({"extra": "secret"}, (("$", "unknown_fields"),)),
    ({"member_record_ids": ["member-a", None, "", "member-a", "member-b"]},
     (("member_record_ids[1]", "invalid_type"), ("member_record_ids[2]", "invalid_value"))),
])
def test_valid_header_survives_independent_member_and_shape_errors(changes, expected):
    result = normalize(body(**changes))
    assert (result.value.decision_id, result.value.decision_at) == ("decision-1", AT)
    assert diagnostics(result) == expected
    expected_ids = ("member-b", "member-a") if "extra" in changes else (
        ("member-a", "member-b") if type(changes["member_record_ids"]) is list else ()
    )
    assert result.member_record_ids == expected_ids


def test_missing_member_list_preserves_header_and_is_distinct_from_empty_list():
    raw = body()
    del raw["member_record_ids"]
    missing, empty = normalize(raw), normalize(body(member_record_ids=[]))
    assert missing.value == empty.value
    assert missing.member_record_ids == empty.member_record_ids == ()
    assert diagnostics(missing) == (("member_record_ids", "missing"),)
    assert empty.rejections == ()


@pytest.mark.parametrize(("value", "code"), [
    (None, "invalid_type"), (AT, "invalid_type"), (0, "invalid_type"),
    ("", "invalid_timestamp"), ("secret-invalid-time", "invalid_timestamp"),
    ("2026-09-06T14:30:00", "invalid_timestamp"),
    ("0001-01-01T00:00:00+01:00", "invalid_timestamp"),
    ("9999-12-31T23:59:59-01:00", "invalid_timestamp"),
])
def test_invalid_decision_time_retains_members_without_receipt_fallback(value, code):
    result = normalize(body(decision_at=value))
    assert result.value is None
    assert result.member_record_ids == ("member-b", "member-a")
    assert diagnostics(result) == (("decision_at", code),)
    assert result.received_at == RECEIVED


class Hostile:
    """This class represents a raw object whose protocols must never execute."""

    def __repr__(self):
        raise AssertionError("raw repr executed")

    def __str__(self):
        raise AssertionError("raw str executed")

    def __bool__(self):
        raise AssertionError("raw bool executed")

    def __iter__(self):
        raise AssertionError("raw iteration executed")

    def __eq__(self, other):
        raise AssertionError("raw equality executed")

    def __hash__(self):
        raise AssertionError("raw hash executed")


class HostileDict(dict):
    """This class represents a root subclass with unsafe traversal."""

    def items(self):
        raise AssertionError("dict subclass traversal executed")


class HostileList(list):
    """This class represents a list subclass with unsafe iteration."""

    def __iter__(self):
        raise AssertionError("list subclass traversal executed")


class StringSubclass(str):
    """This class represents an unacceptable scalar string subclass."""


class DateTimeSubclass(datetime):
    """This class represents an unacceptable trusted timestamp subclass."""


class CollidingKey:
    """This class represents a key armed after insertion beside a real key."""

    armed = False

    def __hash__(self):
        if self.armed:
            raise AssertionError("hostile key hash executed")
        return hash("decision_id")

    def __eq__(self, other):
        if self.armed:
            raise AssertionError("hostile key equality executed")
        return False

    def __repr__(self):
        raise AssertionError("hostile key repr executed")


@pytest.mark.parametrize("kind", ["none", "list", "tuple", "object", "dict_subclass"])
def test_wrong_root_has_only_one_bounded_rejection_without_traversal(kind):
    raw = {"none": None, "list": [], "tuple": (), "object": Hostile(),
           "dict_subclass": HostileDict(body())}[kind]
    result = normalize(raw)
    assert result.value is None and result.member_record_ids == ()
    assert diagnostics(result) == (("$", "expected_exact_dict"),)


@pytest.mark.parametrize("recognized_first", [False, True])
def test_hostile_colliding_keys_never_run_protocols_and_safe_fields_survive(recognized_first):
    key = CollidingKey()
    raw = body() if recognized_first else {key: Hostile()}
    raw.update({key: Hostile()} if recognized_first else body())
    key.armed = True
    result = normalize(raw)
    assert result.value.decision_id == "decision-1" and result.value.decision_at == AT
    assert result.member_record_ids == ("member-b", "member-a")
    assert diagnostics(result) == (("$", "unknown_fields"),)


def test_nonstring_and_subclass_keys_are_unknown_and_cannot_supply_missing_fields():
    raw = {StringSubclass("decision_id"): "claimed-header", 7: "secret",
           "decision_at": "2026-09-06T14:30:00Z", "member_record_ids": ["member-a"]}
    result = normalize(raw)
    assert result.value is None and result.member_record_ids == ("member-a",)
    assert diagnostics(result) == (("$", "unknown_fields"), ("decision_id", "missing"))


def test_hostile_values_and_exact_scalar_subclasses_are_rejected_without_protocols():
    result = normalize(body(decision_id=Hostile(), decision_at=StringSubclass("2026-09-06T14:30:00Z"),
                            member_record_ids=[Hostile(), StringSubclass("claimed-id"), "safe", ""]))
    assert result.value is None and result.member_record_ids == ("safe",)
    assert diagnostics(result) == (
        ("decision_id", "invalid_type"), ("decision_at", "invalid_type"),
        ("member_record_ids[0]", "invalid_type"), ("member_record_ids[1]", "invalid_type"),
        ("member_record_ids[3]", "invalid_value"),
    )
    result = normalize(body(member_record_ids=HostileList(["unsafe"])))
    assert result.value is not None and result.member_record_ids == ()
    assert diagnostics(result) == (("member_record_ids", "invalid_type"),)


@pytest.mark.parametrize(("field", "value", "error"), [
    ("event_id", None, TypeError), ("event_id", StringSubclass("event"), TypeError),
    ("event_id", "", ValueError), ("raw_ref", None, TypeError),
    ("raw_ref", StringSubclass("ref"), TypeError), ("raw_ref", "", ValueError),
    ("received_at", "2026-09-06T14:30:00Z", TypeError),
    ("received_at", DateTimeSubclass(2026, 9, 6, tzinfo=UTC), TypeError),
    ("received_at", datetime(2026, 9, 6), ValueError),
])
def test_every_trusted_argument_is_validated_before_raw_root_or_values(field, value, error):
    for raw in (Hostile(), body(decision_id=Hostile())):
        with pytest.raises(error, match=field):
            normalize(raw, **{field: value})


def test_result_retains_no_mutable_request_data_and_all_diagnostics_have_actual_envelope():
    raw = body(member_record_ids=["a", None, "", "b", None])
    result = normalize(raw)
    raw["member_record_ids"][:] = ["replacement"]
    raw["decision_id"] = "replacement"
    assert result.value.decision_id == "decision-1"
    assert result.member_record_ids == ("a", "b")
    assert diagnostics(result) == (("member_record_ids[1]", "invalid_type"),
                                   ("member_record_ids[2]", "invalid_value"),
                                   ("member_record_ids[4]", "invalid_type"))
    for item in result.rejections:
        assert (item.event_id, item.raw_ref, item.received_at) == (
            "request-event", "raw://request", RECEIVED,
        )
        assert item.stage == "context_request_normalization"
        assert item.reasons == (item.code,)
        assert type(item.reasons) is tuple


def test_header_result_and_rejection_are_frozen_and_factory_result_cannot_be_forged():
    result = normalize(body(member_record_ids=[None]))
    for owner, name, value in ((result, "value", None), (result, "member_record_ids", ()),
                               (result, "rejections", ()), (result.value, "decision_id", "new"),
                               (result.rejections[0], "code", "missing"),
                               (result.rejections[0], "stage", "other"),
                               (result.rejections[0], "reasons", ())):
        with pytest.raises(FrozenInstanceError):
            setattr(owner, name, value)
    for kwargs in ({}, {"value": result.value}, {"rejections": ()},
                   {"success": True}, {"reasons": ()}, {"member_record_ids": ("fake",)}):
        with pytest.raises(TypeError):
            api.ContextRequestValidation(**kwargs)
    with pytest.raises(TypeError):
        replace(result, rejections=())


@pytest.mark.parametrize(("decision_id", "decision_at", "error"), [
    ("", AT, ValueError), (None, AT, TypeError), (StringSubclass("id"), AT, TypeError),
    ("id", None, TypeError), ("id", "2026-09-06T14:30:00Z", TypeError),
    ("id", datetime(2026, 9, 6), ValueError),
    ("id", DateTimeSubclass(2026, 9, 6, tzinfo=UTC), TypeError),
])
def test_header_constructor_rejects_wrong_exact_or_missing_scalars(decision_id, decision_at, error):
    with pytest.raises(error):
        api.ContextRequest(decision_id, decision_at)


def test_header_constructor_normalizes_offset_and_keeps_only_header_fields():
    value = api.ContextRequest("id", AT.astimezone(timezone(timedelta(hours=-4))))
    assert value.decision_at == AT and value.decision_at.tzinfo is UTC
    assert tuple(item.name for item in fields(value)) == ("decision_id", "decision_at")
    assert tuple(item.name for item in fields(api.ContextRequestValidation)) == (
        "value", "member_record_ids", "event_id", "raw_ref", "received_at", "rejections",
    )
    assert tuple(signature(api.normalize_context_request).parameters) == (
        "raw", "event_id", "raw_ref", "received_at",
    )


@pytest.mark.parametrize(("field", "allowed"), [
    ("$", ("expected_exact_dict", "unknown_fields")),
    ("decision_id", ("missing", "invalid_type", "invalid_value")),
    ("decision_at", ("missing", "invalid_type", "invalid_timestamp")),
    ("member_record_ids", ("missing", "invalid_type")),
    ("member_record_ids[0]", ("invalid_type", "invalid_value")),
    ("member_record_ids[12]", ("invalid_type", "invalid_value")),
    ("member_record_ids[00]", ()), ("member_record_ids[01]", ()),
    ("member_record_ids[-1]", ()), ("member_record_ids[+1]", ()),
    ("member_record_ids[١]", ()), ("member_record_ids[1.0]", ()),
    ("member_record_ids[1]\n", ()), ("member_record_ids[1].secret", ()),
    ("member_record_ids[secret]", ()), ("secret-source-id", ()),
])
def test_rejection_constructor_accepts_exactly_emitted_field_code_pairs(field, allowed):
    for code in ("expected_exact_dict", "unknown_fields", "missing", "invalid_type",
                 "invalid_value", "invalid_timestamp", "invalid_date", "invalid_decimal", "secret"):
        if code in allowed:
            value = api.ContextInputRejection("event", AT, "raw://source", field, code)
            assert value.field == field and value.code == code
            assert value.reasons == (code,)
        else:
            with pytest.raises(ValueError):
                api.ContextInputRejection("event", AT, "raw://source", field, code)


@pytest.mark.parametrize(("field", "value", "error"), [
    ("event_id", "", ValueError), ("raw_ref", "", ValueError),
    ("event_id", StringSubclass("event"), TypeError), ("raw_ref", Hostile(), TypeError),
    ("received_at", None, TypeError), ("received_at", datetime(2026, 9, 6), ValueError),
    ("field", StringSubclass("decision_id"), TypeError), ("field", Hostile(), TypeError),
    ("field", "", ValueError), ("code", StringSubclass("missing"), TypeError),
    ("code", Hostile(), TypeError), ("code", "", ValueError),
])
def test_rejection_constructor_rejects_untrusted_or_subclass_scalars(field, value, error):
    kwargs = dict(event_id="event", received_at=AT, raw_ref="raw://source", field="decision_id", code="missing")
    kwargs[field] = value
    with pytest.raises(error):
        api.ContextInputRejection(**kwargs)


def test_closed_constructors_reject_subclass_owners_and_derived_arguments():
    for owner, args in ((api.ContextRequest, ("id", AT)),
                        (api.ContextInputRejection, ("event", AT, "ref", "decision_id", "missing")),
                        (api.ContextRequestValidation, ())):
        subclass = type("Derived", (owner,), {})
        with pytest.raises(TypeError):
            subclass(*args)
    for name in ("stage", "reasons", "success"):
        with pytest.raises(TypeError):
            api.ContextInputRejection("event", AT, "ref", "decision_id", "missing", **{name: ()})


class FailingTimezone(tzinfo):
    """This class represents a timezone that fails trusted datetime validation."""

    def utcoffset(self, value):
        raise RuntimeError("secret timezone failure")


def test_failing_trusted_timezone_is_controlled_before_raw_and_normalized_on_rejections():
    with pytest.raises(ValueError, match="received_at timezone evaluation failed"):
        normalize(Hostile(), received_at=datetime(2026, 9, 6, tzinfo=FailingTimezone()))
    rejection = api.ContextInputRejection("event", AT.astimezone(timezone(timedelta(hours=5))),
                                          "ref", "decision_id", "missing")
    assert rejection.received_at == AT and rejection.received_at.tzinfo is UTC


def test_every_invalid_list_entry_is_retained_in_numeric_index_order():
    result = normalize(body(member_record_ids=[None] * 12 + ["safe", "", "safe"]))
    assert result.member_record_ids == ("safe",)
    assert diagnostics(result) == tuple((f"member_record_ids[{index}]", "invalid_type")
                                        for index in range(12)) + (("member_record_ids[13]", "invalid_value"),)
    assert result.value is not None


def test_public_exports_expose_the_same_normalizer_and_exact_owners():
    for name in ("ContextRequest", "ContextInputRejection", "ContextRequestValidation",
                 "normalize_context_request"):
        assert getattr(public, name) is getattr(api, name)


def test_diagnostic_order_is_independent_of_raw_key_order():
    raw = {"member_record_ids": [None, "safe", ""], "decision_at": "bad-time",
           "unexpected": "secret", "decision_id": False}
    forward, reverse = normalize(raw), normalize(dict(reversed(tuple(raw.items()))))
    assert forward == reverse
    assert forward.member_record_ids == ("safe",) and forward.value is None
    assert diagnostics(forward) == (
        ("$", "unknown_fields"), ("decision_id", "invalid_type"),
        ("decision_at", "invalid_timestamp"), ("member_record_ids[0]", "invalid_type"),
        ("member_record_ids[2]", "invalid_value"),
    )
