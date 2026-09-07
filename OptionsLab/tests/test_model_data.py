"""Safe data-only model inspection, original-byte identity and bounded failures."""

from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, Inexact, Rounded, localcontext
import hashlib
import importlib.util
import json

import pytest


CASH = b'{"schema_version":1,"format_id":"options_lab.cash_json.v1"}'
FIXED = {"schema_version": 1, "format_id": "options_lab.fixed_by_right_json.v1",
         "call": {"mean_attempt_return": "-0.0250", "calibration_bucket": "same\\bucket"},
         "put": {"mean_attempt_return": "+.035", "calibration_bucket": "same\\bucket"}}
RECEIPT = {"event_id": "inspection-1", "raw_ref": "memory:model",
           "received_at": datetime(2026, 9, 7, tzinfo=timezone(timedelta(hours=-4)))}


def inspect(payload, **receipt):
    """Exercise the real public operation, with a clear missing-owner RED."""
    assert importlib.util.find_spec("options_lab.bundle_inputs") is not None
    from options_lab.bundle_inputs import normalize_model_bytes
    return normalize_model_bytes(payload, **(RECEIPT | receipt))


def encoded(raw):
    """Encode ordinary test inputs without production serialization helpers."""
    return json.dumps(raw, separators=(",", ":")).encode()


def rejected(payload, field, code):
    """Assert exclusive bounded failure and the actual normalized receipt."""
    result = inspect(payload)
    assert result.value is None
    failure = result.rejection
    assert (failure.stage, failure.field, failure.code) == ("model_data", field, code)
    for item in (result, failure):
        assert item.event_id == RECEIPT["event_id"] and item.raw_ref == RECEIPT["raw_ref"]
        assert item.received_at == datetime(2026, 9, 7, 4, tzinfo=timezone.utc)
    return result


def test_cash_and_fixed_rows_keep_original_bytes_and_exact_signed_numbers():
    cash = inspect(CASH)
    assert cash.rejection is None and cash.value.model_kind == "cash"
    assert cash.value.format_id == "options_lab.cash_json.v1" and cash.value.rows == ()
    assert cash.value.original_model_bytes is CASH
    assert cash.value.model_hash == "b240254407fd6f4e0fa223fd85c472f8726199f01d4e65ff43b309b7ff3d7b16"
    payload = encoded(FIXED)
    value = inspect(payload).value
    assert value.model_kind == "fixture_fixed_by_right"
    assert value.format_id == FIXED["format_id"] and value.original_model_bytes is payload
    assert [(r.right, r.mean_attempt_return, r.calibration_bucket) for r in value.rows] == [
        ("call", Decimal("-0.025"), "same\\bucket"), ("put", Decimal("0.035"), "same\\bucket")]
    assert value.model_hash == "d3c773914c0832c2ce0586f9ecbbef79e8e542cff625fc9ef65cb36874b50b15"
    altered = inspect(payload + b"\n ").value
    assert altered.rows == value.rows and altered.model_hash != value.model_hash
    assert altered.original_model_bytes == payload + b"\n "
    assert cash.event_id == RECEIPT["event_id"] and cash.raw_ref == RECEIPT["raw_ref"]
    assert cash.received_at == datetime(2026, 9, 7, 4, tzinfo=timezone.utc)


@pytest.mark.parametrize("payload,code", [
    (None, "invalid_type"), (bytearray(CASH), "invalid_type"), (memoryview(CASH), "invalid_type"),
    (CASH.decode(), "invalid_type"), (b"\xff", "invalid_utf8"), (b"\x80\x04N.", "invalid_utf8"),
    (b"__import__('os').system('secret')", "invalid_json"), (b"", "invalid_json"),
    (b'{"x":1,"x":2}', "duplicate_key"), (b'{"x":NaN}', "nonfinite_number"),
    (b'{"x":Infinity}', "nonfinite_number"), (b'{"x":1e9999}', "nonfinite_number"),
    (b"[" * 2000 + b"]" * 2000, "resource_limit"), (b" " * 65537, "resource_limit"),
])
def test_wrong_bytes_and_decoder_failures_never_become_cash(payload, code):
    rejected(payload, "model_bytes", code)


@pytest.mark.parametrize("raw,field,code", [
    (None, "$", "expected_exact_dict"), ([], "$", "expected_exact_dict"),
    ({}, "schema_version", "missing"),
    ({"schema_version": True, "format_id": "options_lab.cash_json.v1"}, "schema_version", "invalid_type"),
    ({"schema_version": 2, "format_id": "options_lab.cash_json.v1"}, "schema_version", "invalid_value"),
    (FIXED | {"format_id": "pickle"}, "format_id", "invalid_value"),
    (FIXED | {"format_id": []}, "format_id", "invalid_type"),
    (FIXED | {"call": []}, "call", "expected_exact_dict"),
    (FIXED | {"call": {"mean_attempt_return": "1"}}, "call.calibration_bucket", "missing"),
    (FIXED | {"import": "secret"}, "$", "unknown_fields"),
    (FIXED | {"secret" * 1000: None}, "$", "unknown_fields"),
    ({k: v for k, v in FIXED.items() if k != "put"}, "put", "missing"),
])
def test_closed_shapes_and_tokens_reject_without_echoing_payload(raw, field, code):
    result = rejected(encoded(raw), field, code)
    assert "secret" not in repr(result)


@pytest.mark.parametrize("mean,code", [(True, "invalid_type"), (1, "invalid_type"),
    (0.1, "invalid_type"), (None, "invalid_type"), ({}, "invalid_type"), ([], "invalid_type"),
    ("1e2", "invalid_decimal"), ("NaN", "invalid_decimal"), ("Infinity", "invalid_decimal"),
    (" 1", "invalid_decimal"), ("", "invalid_decimal"), ("١", "invalid_decimal"),
    ("1" * 1003, "resource_limit"), ("1" * 1001, "invalid_decimal"),
    ("0." + "0" * 1000 + "1", "resource_limit"), ("." + "0" * 1000 + "1", "invalid_decimal")])
def test_means_require_bounded_exact_fixed_point_strings(mean, code):
    rejected(encoded(FIXED | {"call": FIXED["call"] | {"mean_attempt_return": mean}}),
             "call.mean_attempt_return", code)


@pytest.mark.parametrize("bucket,code", [(True, "invalid_type"), ({}, "invalid_type"),
    ([], "invalid_type"), ("", "invalid_value"), ("x" * 1025, "resource_limit"),
    ("\ud800", "invalid_value"), ("\udfff", "invalid_value")])
def test_bucket_identifiers_are_exact_bounded_and_surrogate_free(bucket, code):
    rejected(encoded(FIXED | {"put": FIXED["put"] | {"calibration_bucket": bucket}}),
             "put.calibration_bucket", code)


def test_inclusive_bounds_and_decimal_context_do_not_round_or_rewrite_bytes():
    assert inspect(CASH + b" " * (65536 - len(CASH))).value is not None
    for mean in ("1" * 1000, "-" + "1" * 1000 + ".", "." + "0" * 999 + "1", "0" * 1002):
        raw = FIXED | {"call": {"mean_attempt_return": mean, "calibration_bucket": "é" * 1024}}
        payload = encoded(raw)
        with localcontext() as ctx:
            ctx.prec = 1
            ctx.traps[Inexact] = ctx.traps[Rounded] = True
            value = inspect(payload).value
        assert value.rows[0].mean_attempt_return == Decimal(mean)
        assert value.model_hash == hashlib.sha256(payload).hexdigest()


def test_factory_records_cannot_be_constructed_replaced_or_mutated():
    result = inspect(encoded(FIXED))
    failure = rejected(b"", "model_bytes", "invalid_json").rejection
    for item in (result, result.value, result.value.rows[0], failure):
        with pytest.raises(TypeError):
            type(item)()
        with pytest.raises(TypeError):
            replace(item)
        with pytest.raises(FrozenInstanceError):
            item.forged = True
    with pytest.raises(TypeError):
        result.value.rows[0] = result.value.rows[1]
    class HostileBytes(bytes):
        """This class represents an unsupported bytes subclass with hostile hooks."""
        def __len__(self):
            """Fail if inspection calls a subclass hook."""
            raise AssertionError("must not inspect subclass")
    rejected(HostileBytes(CASH), "model_bytes", "invalid_type")


@pytest.mark.parametrize("receipt,error", [({"event_id": ""}, ValueError),
    ({"raw_ref": 7}, TypeError), ({"received_at": "2026-09-07"}, TypeError),
    ({"received_at": datetime(2026, 9, 7)}, ValueError)])
def test_trusted_receipt_misuse_raises_at_existing_boundary(receipt, error):
    with pytest.raises(error):
        inspect(CASH, **receipt)


@pytest.mark.parametrize("raw,path", [(json.loads(CASH), "$"), (FIXED, "$"),
    (FIXED["call"], "call"), (FIXED["put"], "put")])
def test_every_closed_model_shape_rejects_each_omission_and_extensions(raw, path):
    for key in raw:
        changed = {k: v for k, v in raw.items() if k != key}
        payload = changed if path == "$" else FIXED | {path: changed}
        result = inspect(encoded(payload))
        assert result.value is None and result.rejection.code == "missing"
        assert result.rejection.field == (key if path == "$" else f"{path}.{key}")
    changed = raw | {"unknown": "secret"}
    rejected(encoded(changed if path == "$" else FIXED | {path: changed}), path, "unknown_fields")


def test_hostile_external_objects_and_cycles_are_rejected_without_inspection():
    class Hostile:
        """This class represents external objects whose hooks must never run."""
        def __repr__(self):
            """Fail if inspection renders an untrusted object."""
            raise AssertionError("must not render external objects")
        def __eq__(self, other):
            """Fail if inspection compares an untrusted object."""
            raise AssertionError("must not compare external objects")
    rejected(Hostile(), "model_bytes", "invalid_type")
    cycle = {}
    cycle["cycle"] = cycle
    rejected(cycle, "model_bytes", "invalid_type")
    rejected(encoded(FIXED | {"format_id": "x" * 1025}), "format_id", "resource_limit")
    rejected(encoded(FIXED | {"format_id": "\ud800"}), "format_id", "invalid_value")


def test_decimal_allocation_failure_is_safe_and_process_control_is_not_swallowed(monkeypatch):
    from options_lab import bundle_inputs
    def exhausted(*args):
        """Simulate the bounded decimal conversion exhausting allocation."""
        raise MemoryError("secret")
    with monkeypatch.context() as patch:
        patch.setattr(bundle_inputs, "_parse_decimal", exhausted)
        rejected(encoded(FIXED), "call.mean_attempt_return", "resource_limit")
    def interrupted(*args):
        """Simulate process control while the existing decoder owns parsing."""
        raise KeyboardInterrupt
    monkeypatch.setattr(bundle_inputs, "_decode_json", interrupted)
    with pytest.raises(KeyboardInterrupt):
        inspect(CASH)
