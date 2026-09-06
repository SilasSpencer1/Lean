"""Exercise registered fixture tick evidence and exact zero-origin alignment."""

from copy import deepcopy
from dataclasses import FrozenInstanceError, fields, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
from inspect import signature
import json
from pathlib import Path
from random import Random

import pytest

import options_lab.admission as admission
import options_lab.ticks as api
import options_lab as public
import build_tick_fixture as producer


UTC = timezone.utc
AT = datetime(2026, 9, 5, 14, 29, 58, tzinfo=UTC)
FIXTURE_ID = "p08c-tick-evidence-v1"
FIXTURE = Path(__file__).parent / "fixtures" / f"{FIXTURE_ID}.json"
OLD_FIXTURE = FIXTURE.with_name("p08a-greek-ready-v1.json")


@pytest.fixture(scope="module")
def admitted():
    result = admission.verify_fixture_bundle(
        FIXTURE_ID, FIXTURE.read_bytes(), event_id="verify-ticks",
        raw_ref="fixture://ticks", received_at=AT,
    )
    assert result.value is not None and result.rejection is None
    return result.value


def rule(admitted, record_id="tick-good"):
    member = next(member for member in admitted.members if member.record_id == record_id)
    envelope = member.decode_envelope()
    result = api.normalize_tick_rule(
        member.decode_raw_body(), event_id=envelope["event_id"],
        raw_ref=envelope["raw_ref"],
        received_at=datetime.fromisoformat(envelope["simulated_received_at"]),
    )
    assert result.value is not None and result.rejection is None
    return result.value


def assess(admitted, record_id="tick-good", price=Decimal("5.10"), at=AT + timedelta(seconds=2)):
    value = rule(admitted, record_id)
    return api.assess_order_tick(
        value, manifest=admitted, contract=value.contract, price=price, decision_at=at,
    )


def test_registered_rule_aligns_and_off_grid_price_rejects_without_rounding(admitted):
    good = assess(admitted)
    bad = assess(admitted, price=Decimal("5.105"))
    assert good.tick_aligned and good.reasons == ()
    assert not bad.tick_aligned and "price_off_grid" in bad.reasons
    assert bad.price == Decimal("5.105")
    assert good.matched_member is not None and good.matched_member.record_id == "tick-good"
    assert good.manifest.origin == "synthetic" and not good.manifest.operational_allowed
    assert (good.manifest.fidelity_tier, good.manifest.permitted_use, good.manifest.economic_allowed) == (0, "core_fixture", False)
    assert not hasattr(good, "order_authorized")


@pytest.mark.parametrize(("record_id", "reason"), (
    ("tick-missing-increment", "increment_missing"),
    ("tick-zero-increment", "increment_not_positive"),
    ("tick-negative-increment", "increment_not_positive"),
    ("tick-wrong-units", "units_unsupported"),
    ("tick-wrong-version", "definition_version_unregistered"),
    ("tick-unknown-end", "effective_until_missing"),
    ("tick-negative-band", "price_from_negative"),
    ("tick-reversed-band", "price_band_invalid"),
))
def test_registered_adverse_rules_preserve_claims_and_fail_semantics(admitted, record_id, reason):
    result = assess(admitted, record_id)
    assert result.matched_member is not None
    assert reason in result.reasons
    assert not result.tick_aligned


def test_band_and_effective_intervals_are_half_open_and_null_upper_is_unbounded(admitted):
    assert assess(admitted, price=Decimal("5.00")).tick_aligned
    assert "price_outside_band" in assess(admitted, price=Decimal("10.00")).reasons
    assert assess(admitted, "tick-unbounded", price=Decimal("100.00")).tick_aligned
    assert assess(admitted, at=AT + timedelta(seconds=1)).tick_aligned
    assert "effective_not_applicable" in assess(admitted, at=AT).reasons
    assert "effective_not_applicable" in assess(admitted, at=AT + timedelta(seconds=10)).reasons
    assert "available_after_decision" in assess(admitted, "tick-late-available").reasons


def test_grid_is_zero_origin_and_exact_under_hostile_decimal_context(admitted):
    assert "price_off_grid" in assess(admitted, "tick-non-grid-lower", Decimal("5.005")).reasons
    with localcontext() as context:
        context.prec, context.Emin, context.Emax = 1, -1, 1
        for signal in context.traps:
            context.traps[signal] = True
        context.clear_flags()
        assert assess(admitted, price=Decimal("5.10")).tick_aligned
        assert not assess(admitted, price=Decimal("5.105")).tick_aligned
        assert not any(context.flags.values())


def test_raw_total_shape_and_public_api(admitted):
    body = next(m.decode_raw_body() for m in admitted.members if m.record_id == "tick-good")
    del body["increment"]
    result = api.normalize_tick_rule(body, event_id="raw", raw_ref="raw://tick", received_at=AT)
    assert result.value is None and result.rejection is not None
    assert (result.rejection.field, result.rejection.code) == ("increment", "missing")
    assert result.rejection.stage == "tick_normalization"
    assert tuple(signature(api.normalize_tick_rule).parameters) == ("raw", "event_id", "raw_ref", "received_at")
    assert tuple(signature(api.assess_order_tick).parameters) == ("rule", "manifest", "contract", "price", "decision_at")
    assert tuple(field.name for field in fields(api.TickRule)) == (
        "contract", "increment", "price_from", "price_until", "effective_from",
        "effective_until", "available_at", "source", "provider_record_id",
        "source_version", "raw_ref", "units", "rule_id",
    )
    for name in ("TickRule", "TickInputRejection", "TickValidation", "TickAssessment",
                 "normalize_tick_rule", "assess_order_tick"):
        assert getattr(public, name) is getattr(api, name)


def test_trusted_constructor_rejects_missing_mandatory_price_from(admitted):
    value = rule(admitted)
    with pytest.raises(TypeError):
        api.TickRule(value.contract, value.increment, None, value.price_until,
                     value.effective_from, value.effective_until, value.available_at,
                     value.source, value.provider_record_id, value.source_version,
                     value.raw_ref, value.units, value.rule_id)


def test_actual_member_binding_rejects_absence_other_root_and_ambiguity(admitted):
    value = rule(admitted)
    changed = replace(value, raw_ref="caller://different")
    assert "rule_member_missing" in api.assess_order_tick(
        changed, manifest=admitted, contract=value.contract, price=Decimal("5.10"),
        decision_at=AT + timedelta(seconds=2),
    ).reasons
    old = admission.verify_fixture_bundle(
        "p08a-greek-ready-v1", OLD_FIXTURE.read_bytes(), event_id="old",
        raw_ref="fixture://old", received_at=AT,
    ).value
    assert old is not None
    assert "rule_member_missing" in api.assess_order_tick(
        value, manifest=old, contract=value.contract, price=Decimal("5.10"),
        decision_at=AT + timedelta(seconds=2),
    ).reasons
    ambiguous = assess(admitted, "tick-ambiguous")
    assert ambiguous.matched_member is None and "rule_member_ambiguous" in ambiguous.reasons


@pytest.mark.parametrize(("record_id", "reason"), (
    ("tick-wrong-source", "rule_profile_mismatch"),
    ("tick-unknown-definition", "definition_not_registered"),
    ("tick-unknown-start", "effective_from_missing"),
    ("tick-reversed-effective", "effective_interval_invalid"),
    ("tick-unknown-available", "available_at_missing"),
))
def test_registered_definition_profile_and_time_adversity_is_not_repaired(admitted, record_id, reason):
    result = assess(admitted, record_id)
    assert result.matched_member is not None
    assert reason in result.reasons and not result.tick_aligned


def test_malformed_unrelated_member_cannot_mask_good_member_and_missing_rule_retains_price(admitted):
    good = assess(admitted)
    missing = api.assess_order_tick(
        None, manifest=admitted, contract=good.contract, price=Decimal("0"),
        decision_at=AT + timedelta(seconds=2),
    )
    assert good.tick_aligned and good.matched_member is not None
    assert missing.reasons == ("rule_missing", "price_not_positive")


def test_decimal_bounds_preserve_exact_supported_and_unsupported_values(admitted):
    assert assess(admitted, price=Decimal("5.1000")).tick_aligned
    supported = assess(admitted, price=Decimal("1e999"))
    unsupported = assess(admitted, price=Decimal("1e1000"))
    assert "decimal_representation_unsupported" not in supported.reasons
    assert "decimal_representation_unsupported" in unsupported.reasons
    assert unsupported.price == Decimal("1e1000")
    extreme_band = assess(admitted, "tick-extreme-band")
    assert "decimal_representation_unsupported" in extreme_band.reasons
    assert extreme_band.rule.price_from == Decimal("1" + ("0" * 1000))


def test_every_raw_root_and_contract_key_has_safe_missing_diagnostic(admitted):
    member = next(item for item in admitted.members if item.record_id == "tick-good")
    body = member.decode_raw_body()
    for key in tuple(body):
        candidate = deepcopy(body)
        del candidate[key]
        result = api.normalize_tick_rule(candidate, event_id="raw", raw_ref="raw://tick", received_at=AT)
        assert result.rejection is not None
        assert (result.rejection.field, result.rejection.code) == (key, "missing")
    for key in tuple(body["contract"]):
        candidate = deepcopy(body)
        del candidate["contract"][key]
        result = api.normalize_tick_rule(candidate, event_id="raw", raw_ref="raw://tick", received_at=AT)
        assert result.rejection is not None
        assert (result.rejection.field, result.rejection.code) == (f"contract.{key}", "missing")


def test_raw_rejections_have_shape_precedence_and_never_echo_hostile_values(admitted):
    body = next(item.decode_raw_body() for item in admitted.members if item.record_id == "tick-good")
    candidate = deepcopy(body)
    candidate["unknown"] = True
    del candidate["source"]
    result = api.normalize_tick_rule(candidate, event_id="raw", raw_ref="raw://tick", received_at=AT)
    assert (result.rejection.field, result.rejection.code) == ("$", "unknown_fields")
    candidate = deepcopy(body)
    candidate["contract"]["unknown"] = True
    candidate["increment"] = False
    result = api.normalize_tick_rule(candidate, event_id="raw", raw_ref="raw://tick", received_at=AT)
    assert (result.rejection.field, result.rejection.code) == ("contract", "unknown_fields")
    for raw in (None, [], {1: "secret"}, {"secret": "untrusted"}):
        result = api.normalize_tick_rule(raw, event_id="raw", raw_ref="raw://tick", received_at=AT)
        assert result.value is None and result.rejection is not None
        assert "secret" not in repr(result.rejection)


@pytest.mark.parametrize(("path", "value", "code"), (
    (("contract",), False, "expected_exact_dict"),
    (("increment",), False, "invalid_type"), (("price_from",), False, "invalid_type"),
    (("price_until",), False, "invalid_type"), (("effective_from",), False, "invalid_type"),
    (("effective_until",), False, "invalid_type"), (("available_at",), False, "invalid_type"),
    (("source",), False, "invalid_type"), (("provider_record_id",), False, "invalid_type"),
    (("source_version",), False, "invalid_type"), (("units",), False, "invalid_type"),
    (("rule_id",), False, "invalid_type"), (("contract", "underlying"), False, "invalid_type"),
    (("contract", "expiry"), False, "invalid_type"), (("contract", "right"), False, "invalid_type"),
    (("contract", "strike"), False, "invalid_type"), (("contract", "multiplier"), False, "invalid_type"),
    (("contract", "deliverable_id"), False, "invalid_type"),
))
def test_every_safe_raw_path_rejects_wrong_exact_scalar_type(admitted, path, value, code):
    body = next(item.decode_raw_body() for item in admitted.members if item.record_id == "tick-good")
    target = body
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    result = api.normalize_tick_rule(body, event_id="raw", raw_ref="raw://tick", received_at=AT)
    assert result.value is None and result.rejection is not None
    assert (result.rejection.field, result.rejection.code) == (".".join(path), code)


def test_every_rule_field_is_bound_and_target_contract_is_independent(admitted):
    value = rule(admitted)
    changed_contract = replace(value.contract, multiplier=101)
    changes = {
        "contract": changed_contract, "increment": Decimal("0.02"), "price_from": Decimal("5.01"),
        "price_until": Decimal("9.99"), "effective_from": value.effective_from + timedelta(microseconds=1),
        "effective_until": value.effective_until - timedelta(microseconds=1),
        "available_at": value.available_at + timedelta(microseconds=1), "source": "other",
        "provider_record_id": "other", "source_version": "2", "raw_ref": "other://raw",
        "units": "other", "rule_id": "other",
    }
    for name, changed in changes.items():
        result = api.assess_order_tick(replace(value, **{name: changed}), manifest=admitted,
            contract=value.contract, price=Decimal("5.10"), decision_at=AT + timedelta(seconds=2))
        assert result.matched_member is None and result.reasons[0] == "rule_member_missing"
    target = api.assess_order_tick(value, manifest=admitted, contract=changed_contract,
        price=Decimal("5.10"), decision_at=AT + timedelta(seconds=2))
    assert target.matched_member is not None and target.reasons == ("contract_mismatch",)


def test_trusted_result_constructors_are_exact_frozen_and_derived(admitted):
    value = rule(admitted)
    rejection = api.TickInputRejection("e", AT, "raw", "increment", "missing")
    with pytest.raises(ValueError): api.TickValidation()
    with pytest.raises(ValueError): api.TickValidation(value=value, rejection=rejection)
    with pytest.raises(TypeError): api.TickValidation(value=object())
    with pytest.raises(TypeError): api.TickValidation(rejection=object())
    valid = api.TickValidation(value=value)
    rejected = api.TickValidation(rejection=rejection)
    assert valid.value is value and valid.rejection is None
    assert rejected.rejection is rejection and rejected.value is None
    for record, field, changed in ((value, "source", "other"), (rejection, "field", "source"),
                                   (valid, "value", None), (assess(admitted), "price", Decimal("6"))):
        with pytest.raises(FrozenInstanceError):
            setattr(record, field, changed)
    for field, supplied in (("reasons", ()), ("matched_member", admitted.members[0]), ("tick_aligned", True)):
        with pytest.raises(TypeError):
            api.TickAssessment(value, admitted, value.contract, Decimal("5"), AT, **{field: supplied})


@pytest.mark.parametrize(("path", "value", "expected"), (
    (("increment",), "NaN", ("increment", "invalid_decimal")),
    (("contract", "expiry"), "2026-99-99", ("contract.expiry", "invalid_date")),
    (("effective_from",), "not-a-time", ("effective_from", "invalid_timestamp")),
    (("source",), "", ("source", "invalid_value")),
    (("contract", "right"), "other", ("contract.right", "invalid_value")),
))
def test_raw_malformed_scalar_families_and_trusted_precedence(admitted, path, value, expected):
    body = next(item.decode_raw_body() for item in admitted.members if item.record_id == "tick-good")
    target = body
    for key in path[:-1]: target = target[key]
    target[path[-1]] = value
    result = api.normalize_tick_rule(body, event_id="raw", raw_ref="raw://tick", received_at=AT)
    assert result.rejection is not None and (result.rejection.field, result.rejection.code) == expected
    with pytest.raises(TypeError): api.normalize_tick_rule(body, event_id=1, raw_ref="raw", received_at=AT)


def test_price_and_time_microsecond_boundaries_are_exact(admitted):
    for text, reasons in (("-1", ("price_not_positive",)), ("0", ("price_not_positive",)),
                          ("-0", ("price_not_positive",)), ("4.99", ("price_outside_band",)),
                          ("5.00", ()), ("9.99", ()), ("10.00", ("price_outside_band",)),
                          ("10.01", ("price_outside_band",))):
        price = Decimal(text)
        result = assess(admitted, price=price)
        assert result.reasons == reasons and result.tick_aligned is (not reasons)
        assert result.matched_member.record_id == "tick-good" and result.price is price
        assert result.price.as_tuple() == price.as_tuple()
    for offset, reasons in ((999999, ("effective_not_applicable",)), (1000000, ()),
                            (9999999, ()), (10000000, ("effective_not_applicable",))):
        result = assess(admitted, at=AT + timedelta(microseconds=offset))
        assert result.reasons == reasons and result.tick_aligned is (not reasons)
        assert result.matched_member.record_id == "tick-good"
    for offset, reasons in ((2000000, ()), (1999999, ("available_after_decision",))):
        result = assess(admitted, "tick-available-boundary", at=AT + timedelta(microseconds=offset))
        assert result.reasons == reasons and result.tick_aligned is (not reasons)
        assert result.matched_member.record_id == "tick-available-boundary"
        assert result.rule.available_at == AT + timedelta(seconds=2)


def _integer_grid_oracle(price, increment):
    """Return zero-origin alignment with independent Decimal tuple scaling.

    :param    price: Finite test price with a bounded coefficient.
    :param    increment: Positive finite test increment.
    :returns: Whether the scaled integer price divides exactly by the increment.
    """
    def coefficient(value):
        """Extract the signed coefficient without Decimal arithmetic.

        :param    value: Finite bounded test Decimal.
        :returns: Signed integer coefficient and base-ten exponent.
        """
        sign, digits, exponent = value.as_tuple()
        return (-1 if sign else 1) * int("".join(map(str, digits)) or "0"), exponent
    price_coefficient, price_exponent = coefficient(price)
    tick_coefficient, tick_exponent = coefficient(increment)
    exponent = min(price_exponent, tick_exponent)
    return (price_coefficient * 10 ** (price_exponent - exponent)) % (
        tick_coefficient * 10 ** (tick_exponent - exponent)
    ) == 0


@pytest.mark.parametrize(("price", "increment"), (
    (Decimal("5.10"), Decimal("0.01")), (Decimal("5.105"), Decimal("0.01")),
    (Decimal(".004"), Decimal(".002")), (Decimal("1e999"), Decimal("1e-1000")),
    (Decimal("1.00000000000000000000000000000000001"), Decimal(".01")),
))
def test_exact_grid_matches_independent_integer_oracle_under_hostile_context(price, increment):
    expected = _integer_grid_oracle(price, increment)
    with localcontext() as context:
        context.prec, context.Emin, context.Emax = 1, -1, 1
        for signal in context.traps: context.traps[signal] = True
        context.clear_flags()
        assert api._is_aligned(price, increment) is expected
        assert not any(context.flags.values())


@pytest.mark.parametrize(("record_id", "field", "relation", "reasons"), (
    ("tick-reversed-band", "price", "reversed", ("price_band_invalid",)),
    ("tick-equal-band", "price", "equal", ("price_band_invalid",)),
    ("tick-reversed-effective", "effective", "reversed", ("effective_interval_invalid",)),
    ("tick-equal-effective", "effective", "equal", ("effective_interval_invalid",)),
))
def test_registered_equal_and_strictly_reversed_ranges(admitted, record_id, field, relation, reasons):
    """Reject actual equal/reversed bands and intervals after successful binding."""
    result = assess(admitted, record_id)
    start, end = getattr(result.rule, f"{field}_from"), getattr(result.rule, f"{field}_until")
    assert (end < start) if relation == "reversed" else (end == start)
    assert result.matched_member.record_id == record_id
    assert result.reasons == reasons and not result.tick_aligned


def test_rejection_constructor_closes_every_field_code_pair():
    """Check 180 pairs against the independently specified diagnostic vocabulary."""
    groups = (
        (("$",), ("expected_exact_dict", "unknown_fields")),
        (("contract",), ("missing", "expected_exact_dict", "unknown_fields")),
        (("increment", "price_from", "price_until", "contract.strike"),
         ("missing", "invalid_type", "invalid_decimal")),
        (("effective_from", "effective_until", "available_at"),
         ("missing", "invalid_type", "invalid_timestamp")),
        (("contract.expiry",), ("missing", "invalid_type", "invalid_date")),
        (("contract.multiplier",), ("missing", "invalid_type")),
        (("source", "provider_record_id", "source_version", "units", "rule_id",
          "contract.underlying", "contract.right", "contract.deliverable_id"),
         ("missing", "invalid_type", "invalid_value")),
        (("untrusted.path",), ()),
    )
    codes = ("expected_exact_dict", "unknown_fields", "missing", "invalid_type",
             "invalid_value", "invalid_date", "invalid_timestamp", "invalid_decimal", "untrusted-code")
    for paths, allowed in groups:
        for path in paths:
            for code in codes:
                if code in allowed:
                    result = api.TickInputRejection("e", AT, "raw", path, code)
                    assert (result.field, result.code, result.stage, result.reasons) == (
                        path, code, "tick_normalization", (code,))
                else:
                    with pytest.raises(ValueError):
                        api.TickInputRejection("e", AT, "raw", path, code)


class StringSubclass(str):
    """This class represents a non-exact trusted string."""


class DecimalSubclass(Decimal):
    """This class represents a non-exact trusted decimal."""


class DatetimeSubclass(datetime):
    """This class represents a non-exact trusted timestamp."""


def test_trusted_decimal_time_and_string_fields_reject_misuse(admitted):
    """Reject malformed exact scalars on every trusted rule/result input."""
    value, assessment = rule(admitted), assess(admitted)
    rejection = api.TickInputRejection("e", AT, "raw", "source", "missing")
    for record, names in ((value, ("increment", "price_from", "price_until")),
                          (assessment, ("price",))):
        for name in names:
            for bad, error in ((True, TypeError), (1, TypeError), ("1", TypeError),
                               (DecimalSubclass("1"), TypeError), (Decimal("NaN"), ValueError),
                               (Decimal("sNaN"), ValueError), (Decimal("Infinity"), ValueError),
                               (Decimal("-Infinity"), ValueError)):
                with pytest.raises(error):
                    replace(record, **{name: bad})
    for record, names in ((value, ("effective_from", "effective_until", "available_at")),
                          (assessment, ("decision_at",)), (rejection, ("received_at",))):
        for name in names:
            for bad, error in ((True, TypeError), (AT.isoformat(), TypeError),
                               (DatetimeSubclass(2026, 9, 5, tzinfo=UTC), TypeError),
                               (AT.replace(tzinfo=None), ValueError)):
                with pytest.raises(error):
                    replace(record, **{name: bad})
            shifted = AT.astimezone(timezone(timedelta(hours=5)))
            normalized = getattr(replace(record, **{name: shifted}), name)
            assert normalized == AT and normalized.tzinfo is UTC
    for record, names in ((value, ("source", "provider_record_id", "source_version", "raw_ref", "units", "rule_id")),
                          (rejection, ("event_id", "raw_ref", "field", "code"))):
        for name in names:
            for bad, error in ((False, TypeError), (StringSubclass(getattr(record, name)), TypeError), ("", ValueError)):
                with pytest.raises(error):
                    replace(record, **{name: bad})


def test_trusted_record_subclasses_and_wrong_concrete_inputs_are_rejected(admitted):
    """Prevent inherited or forged records from crossing exact-type boundaries."""
    value = rule(admitted)
    rejection = api.TickInputRejection("e", AT, "raw", "source", "missing")
    for record, consumers in (
        (value, (lambda bad: api.TickValidation(value=bad), lambda bad: replace(assess(admitted), rule=bad))),
        (rejection, (lambda bad: api.TickValidation(rejection=bad),)),
        (value.contract, (lambda bad: replace(value, contract=bad), lambda bad: replace(assess(admitted), contract=bad))),
        (admitted, (lambda bad: replace(assess(admitted), manifest=bad),)),
    ):
        subclass = type("RecordSubclass", (type(record),), {"__doc__": "This class represents a non-exact record."})
        for bad in (object(), object.__new__(subclass)):
            for consume in consumers:
                with pytest.raises(TypeError):
                    consume(bad)


class HostileObject:
    """This class represents caller data whose inspection executes hostile hooks."""

    def fail(self, *args):
        """Fail if a parser invokes caller code.

        :param    args: Ignored hook operands.
        :raises   AssertionError: On any attempted inspection.
        """
        raise AssertionError("hostile-secret must never be inspected")

    __repr__ = __str__ = __eq__ = __bool__ = __iter__ = fail


class HostileKey(HostileObject):
    """This class represents a key armed after insertion into a real dict."""

    armed = False

    def __hash__(self):
        """Permit setup hashing only.

        :returns: A stable setup hash.
        :raises   AssertionError: If the parser hashes the armed key.
        """
        if self.armed:
            self.fail()
        return 1


class HostileString(str, HostileKey):
    """This class represents a string subclass with hostile key hooks."""

    __hash__ = HostileKey.__hash__
    __eq__ = HostileObject.fail
    __repr__ = __str__ = HostileObject.fail


class HostileDict(dict):
    """This class represents an untrusted mapping subclass with hostile traversal."""

    __iter__ = __getitem__ = keys = HostileObject.fail


def test_raw_hostile_objects_keys_and_mapping_subclasses_are_never_inspected(admitted):
    """Reject hostile roots, both key depths, and every scalar with safe evidence."""
    body = next(item.decode_raw_body() for item in admitted.members if item.record_id == "tick-good")
    cases = [(HostileObject(), "$", "expected_exact_dict"), (HostileDict(body), "$", "expected_exact_dict")]
    for key in (HostileKey(), HostileString("source")):
        for nested in (False, True):
            key.armed = False
            bad = {key: "hostile-secret"}
            key.armed = True
            candidate = deepcopy(body)
            if nested:
                candidate["contract"] = bad
            else:
                candidate = bad
            cases.append((candidate, "contract" if nested else "$", "unknown_fields"))
    for path in ("contract", "increment", "price_from", "price_until", "effective_from", "effective_until",
                 "available_at", "source", "provider_record_id", "source_version", "units", "rule_id",
                 "contract.underlying", "contract.expiry", "contract.right", "contract.strike",
                 "contract.multiplier", "contract.deliverable_id"):
        candidate = deepcopy(body)
        parts = path.split(".")
        target = candidate["contract"] if len(parts) == 2 else candidate
        target[parts[-1]] = HostileObject()
        cases.append((candidate, path, "expected_exact_dict" if path == "contract" else "invalid_type"))
    candidate = deepcopy(body)
    candidate["contract"] = HostileDict(candidate["contract"])
    cases.append((candidate, "contract", "expected_exact_dict"))
    for candidate, path, code in cases:
        result = api.normalize_tick_rule(candidate, event_id="raw", raw_ref="raw://tick", received_at=AT)
        assert result.value is None and (result.rejection.field, result.rejection.code) == (path, code)
        assert "hostile-secret" not in repr(result)


def test_raw_precedence_and_all_trusted_envelope_fields(admitted):
    """Freeze root-first/declaration order and validate the envelope before raw data."""
    body = next(item.decode_raw_body() for item in admitted.members if item.record_id == "tick-good")
    missing = deepcopy(body)
    del missing["source"]
    missing["contract"]["unknown"] = True
    scalar = deepcopy(body)
    scalar["contract"]["strike"] = "NaN"
    scalar["increment"] = False
    for raw, expected in ((missing, ("source", "missing")), (scalar, ("contract.strike", "invalid_decimal"))):
        result = api.normalize_tick_rule(raw, event_id="raw", raw_ref="raw://tick", received_at=AT)
        assert (result.rejection.field, result.rejection.code) == expected
    envelope = {"event_id": "raw", "raw_ref": "raw://tick", "received_at": AT}
    for field, bad, error in (("event_id", False, TypeError), ("event_id", "", ValueError),
                              ("event_id", StringSubclass("raw"), TypeError),
                              ("raw_ref", False, TypeError), ("raw_ref", "", ValueError),
                              ("raw_ref", StringSubclass("raw"), TypeError),
                              ("received_at", "time", TypeError),
                              ("received_at", AT.replace(tzinfo=None), ValueError),
                              ("received_at", DatetimeSubclass(2026, 9, 5, tzinfo=UTC), TypeError)):
        with pytest.raises(error):
            api.normalize_tick_rule(HostileObject(), **{**envelope, field: bad})


@pytest.mark.parametrize(("path", "bad", "code"), (
    *((path, value, "invalid_decimal") for path in ("increment", "price_from", "price_until", "contract.strike")
      for value in ("1e2", " 1", "١", "Infinity")),
    *((path, value, "invalid_timestamp") for path in ("effective_from", "effective_until", "available_at")
      for value in ("2026-09-05T14:30:00", "2026-99-99T14:30:00+00:00")),
    *((path, "", "invalid_value") for path in ("provider_record_id", "source_version", "units", "rule_id",
                                               "contract.underlying", "contract.deliverable_id")),
))
def test_raw_malformed_values_emit_the_owned_code_for_each_family(admitted, path, bad, code):
    """Reject malformed fixed-point strings, timestamps and nonempty identities."""
    body = next(item.decode_raw_body() for item in admitted.members if item.record_id == "tick-good")
    parts = path.split(".")
    target = body["contract"] if len(parts) == 2 else body
    target[parts[-1]] = bad
    result = api.normalize_tick_rule(body, event_id="raw", raw_ref="raw://tick", received_at=AT)
    assert result.value is None and (result.rejection.field, result.rejection.code) == (path, code)


def test_public_extreme_arithmetic_preserves_values_under_hostile_context(admitted):
    """Assess bound extreme inputs without context arithmetic or unbounded expansion."""
    cases = (("tick-tiny-grid", "1e-1000", ()), ("tick-tiny-grid", "1e-1001", ("decimal_representation_unsupported",)),
             ("tick-tiny-grid", "1e999", ()), ("tick-tiny-grid", "1e1000", ("decimal_representation_unsupported",)),
             ("tick-good", "5.1" + "0" * 10000, ()),
             ("tick-good", "5.10000000000000000000000000000000001", ("price_off_grid",)))
    for record_id, text, reasons in cases:
        price = Decimal(text)
        with localcontext() as context:
            context.prec, context.Emin, context.Emax = 1, -1, 1
            for signal in context.traps:
                context.traps[signal] = True
            context.clear_flags()
            result = assess(admitted, record_id, price)
            assert result.reasons == reasons and result.tick_aligned is (not reasons)
            assert result.matched_member.record_id == record_id and result.price is price
            assert result.price.as_tuple() == price.as_tuple()
            assert not any(context.flags.values())


def test_varied_alignment_matches_independent_integer_oracle():
    """Cross-check 500 deterministic varied signed coefficients and scales."""
    random = Random(809)
    for _ in range(500):
        price = Decimal((random.randrange(2), tuple(map(int, str(random.randrange(1, 10**12)))), random.randrange(-30, 31)))
        increment = Decimal((0, tuple(map(int, str(random.randrange(1, 100)))), random.randrange(-30, 31)))
        expected = _integer_grid_oracle(price, increment)
        with localcontext() as context:
            context.prec, context.Emin, context.Emax = 1, -1, 1
            for signal in context.traps:
                context.traps[signal] = True
            context.clear_flags()
            assert api._is_aligned(price, increment) is expected
            assert not any(context.flags.values())


@pytest.mark.parametrize("wrong_malformed_code", (False, True))
def test_producer_normalizes_actual_envelopes_before_hashing(monkeypatch, wrong_malformed_code):
    """Require exact source/missing normalization before any malformed-body hashing."""
    normalize, member = producer.normalize_tick_rule, producer._member
    seen, hashed = [], []

    def checked_normalize(raw, **envelope):
        """Observe the real owner and optionally inject a wrong malformed diagnostic.

        :param    raw: Actual producer body before hashing.
        :param    envelope: Actual trusted event, locator and receipt fields.
        :returns: Real normalization result or the deliberately incorrect code.
        """
        result = normalize(raw, **envelope)
        seen.append((deepcopy(raw), envelope))
        if wrong_malformed_code and "source" not in raw:
            return api.TickValidation(rejection=api.TickInputRejection(
                envelope["event_id"], envelope["received_at"], envelope["raw_ref"], "source", "invalid_value"))
        return result

    def checked_member(record_id, kind, profile_id, raw, envelope):
        """Require normalization of this actual body/envelope before real hashing.

        :param    record_id: Actual fixture member identity.
        :param    kind: Member evidence kind.
        :param    profile_id: Declared source profile identity.
        :param    raw: Actual body passed to the real hashing owner.
        :param    envelope: Actual simulated ingestion envelope.
        :returns: The real member with its computed body hash.
        :raises   AssertionError: If the producer bypasses the normalization gate.
        """
        trusted = {"event_id": envelope["event_id"], "raw_ref": envelope["raw_ref"],
                   "received_at": datetime.fromisoformat(envelope["simulated_received_at"])}
        assert (raw, trusted) in seen, "body/envelope must normalize before raw hashing"
        assert not (wrong_malformed_code and "source" not in raw), "wrong diagnostic must fail before hashing"
        hashed.append(deepcopy(raw))
        return member(record_id, kind, profile_id, raw, envelope)

    monkeypatch.setattr(producer, "normalize_tick_rule", checked_normalize)
    monkeypatch.setattr(producer, "_member", checked_member)
    if wrong_malformed_code:
        with pytest.raises(RuntimeError):
            producer.build_fixture()
        assert all("source" in raw for raw in hashed)
    else:
        payload = json.loads(producer.build_fixture())
        malformed = next(item for item in payload["members"] if item["record_id"] == "tick-malformed-unrelated")
        envelope = malformed["envelope"]
        result = normalize(malformed["raw_body"], event_id=envelope["event_id"], raw_ref=envelope["raw_ref"],
                           received_at=datetime.fromisoformat(envelope["simulated_received_at"]))
        assert result.value is None and (result.rejection.field, result.rejection.code) == ("source", "missing")
        assert any("source" not in raw for raw in hashed)
