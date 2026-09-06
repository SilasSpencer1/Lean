from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, Inexact, InvalidOperation, localcontext
import hashlib
import json
import sys

import pytest

import options_lab.greeks as api
from options_lab.contracts import ContractId


UTC = timezone.utc
AS_OF = datetime(2026, 9, 5, 14, 29, 58, tzinfo=UTC)


def contract(**changes: object) -> ContractId:
    values = {
        "underlying": "SPY",
        "expiry": date(2026, 9, 18),
        "right": "call",
        "strike": Decimal("650"),
        "multiplier": 100,
        "deliverable_id": "standard-spy-100",
    }
    values.update(changes)
    return ContractId(**values)


def inputs(**changes: object) -> api.GreekInputs:
    values = {
        "option_quote_hash": "1" * 64,
        "underlying_quote_hash": "2" * 64,
        "rate": Decimal("-0.01"),
        "dividend_yield": Decimal("0.0125"),
        "rate_unit": "continuous_annual_fraction",
        "dividend_unit": "continuous_annual_fraction",
        "assumptions_id": "fixture-flat-rates-v1",
    }
    values.update(changes)
    return api.GreekInputs(**values)


def digest(snapshot: dict[str, object]) -> str:
    payload = json.dumps(
        snapshot,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def test_fixture_method_and_input_hash_match_independent_json_sha_goldens() -> None:
    method_snapshot = {
        "record_kind": "options_lab.greek_method_spec",
        "greek_method_schema_version": 1,
        "method_id": "fixture-greek-values",
        "method_version": "1",
        "assumptions_id": "fixture-flat-rates-v1",
        "delta_unit": "signed_option_price_per_underlying_price",
        "iv_unit": "annualized_volatility_fraction",
        "rate_unit": "continuous_annual_fraction",
        "dividend_unit": "continuous_annual_fraction",
        "option_price_basis": "option_midpoint",
        "underlying_price_basis": "underlying_midpoint",
        "max_age": {"value": 5000000, "unit": "microseconds"},
    }
    method_hash = digest(method_snapshot)
    input_snapshot = {
        "record_kind": "options_lab.greek_inputs",
        "greek_input_schema_version": 1,
        "contract": {
            "underlying": "SPY",
            "expiry": "2026-09-18",
            "right": "call",
            "strike": "650",
            "multiplier": {"encoding": "base10", "value": "100"},
            "deliverable_id": "standard-spy-100",
        },
        "as_of": "2026-09-05T14:29:58+00:00",
        "option_quote_hash": "1" * 64,
        "underlying_quote_hash": "2" * 64,
        "rate": "-0.01",
        "dividend_yield": "0.0125",
        "rate_unit": "continuous_annual_fraction",
        "dividend_unit": "continuous_annual_fraction",
        "assumptions_id": "fixture-flat-rates-v1",
        "method_spec_hash": method_hash,
    }

    assert method_hash == "747a21a53999afb37334e09c32fda75b5bd367ce067b19610ba83428bff944e9"
    assert digest(input_snapshot) == "aaab8c19a05c3db424a3aac651522af7073a6a59326ae78e0c14e26dcedae193"
    assert api.FIXTURE_GREEK_METHOD.method_spec_hash == method_hash
    assert api.greek_input_hash(
        inputs(), contract=contract(), method=api.FIXTURE_GREEK_METHOD, as_of=AS_OF
    ) == digest(input_snapshot)


@pytest.mark.parametrize(
    "changes",
    [
        {"method_id": "other"},
        {"method_version": "2"},
        {"assumptions_id": "other"},
        {"delta_unit": "other"},
        {"iv_unit": "other"},
        {"rate_unit": "other"},
        {"dividend_unit": "other"},
        {"option_price_basis": "other"},
        {"underlying_price_basis": "other"},
        {"max_age": timedelta(seconds=4)},
    ],
)
def test_method_hash_binds_every_declared_semantic(changes) -> None:
    changed = replace(api.FIXTURE_GREEK_METHOD, **changes)
    assert changed.method_spec_hash != api.FIXTURE_GREEK_METHOD.method_spec_hash


@pytest.mark.parametrize(
    ("value_inputs", "value_contract", "as_of", "method"),
    [
        (inputs(option_quote_hash="3" * 64), contract(), AS_OF, api.FIXTURE_GREEK_METHOD),
        (inputs(underlying_quote_hash="3" * 64), contract(), AS_OF, api.FIXTURE_GREEK_METHOD),
        (inputs(rate=Decimal("-0.02")), contract(), AS_OF, api.FIXTURE_GREEK_METHOD),
        (inputs(dividend_yield=Decimal("0.02")), contract(), AS_OF, api.FIXTURE_GREEK_METHOD),
        (inputs(rate_unit="other"), contract(), AS_OF, api.FIXTURE_GREEK_METHOD),
        (inputs(dividend_unit="other"), contract(), AS_OF, api.FIXTURE_GREEK_METHOD),
        (inputs(assumptions_id="other"), contract(), AS_OF, api.FIXTURE_GREEK_METHOD),
        (inputs(), contract(underlying="QQQ"), AS_OF, api.FIXTURE_GREEK_METHOD),
        (inputs(), contract(expiry=date(2026, 9, 19)), AS_OF, api.FIXTURE_GREEK_METHOD),
        (inputs(), contract(right="put"), AS_OF, api.FIXTURE_GREEK_METHOD),
        (inputs(), contract(strike=Decimal("651")), AS_OF, api.FIXTURE_GREEK_METHOD),
        (inputs(), contract(multiplier=10), AS_OF, api.FIXTURE_GREEK_METHOD),
        (inputs(), contract(deliverable_id="adjusted"), AS_OF, api.FIXTURE_GREEK_METHOD),
        (inputs(), contract(), AS_OF + timedelta(microseconds=1), api.FIXTURE_GREEK_METHOD),
        (
            inputs(),
            contract(),
            AS_OF,
            replace(api.FIXTURE_GREEK_METHOD, method_version="2"),
        ),
    ],
)
def test_input_hash_binds_every_contract_dependency_assumption_time_and_method(
    value_inputs, value_contract, as_of, method
) -> None:
    original = api.greek_input_hash(
        inputs(), contract=contract(), method=api.FIXTURE_GREEK_METHOD, as_of=AS_OF
    )
    assert api.greek_input_hash(
        value_inputs, contract=value_contract, method=method, as_of=as_of
    ) != original


def test_signed_missing_and_zero_assumptions_remain_distinct_exact_facts() -> None:
    missing = inputs(rate=None, dividend_yield=None)
    zero = inputs(rate=Decimal("-0"), dividend_yield=Decimal("0"))
    adverse = inputs(rate=Decimal("-1.25"), dividend_yield=Decimal("-0.50"))

    assert missing.rate is None and missing.dividend_yield is None
    assert zero.rate == 0 and zero.dividend_yield == 0
    assert adverse.rate == Decimal("-1.25")
    assert api.greek_input_hash(
        missing, contract=contract(), method=api.FIXTURE_GREEK_METHOD, as_of=AS_OF
    ) != api.greek_input_hash(
        zero, contract=contract(), method=api.FIXTURE_GREEK_METHOD, as_of=AS_OF
    )


def test_decimal_spellings_and_utc_offsets_have_the_same_identity() -> None:
    verbose = inputs(
        rate=Decimal("-0.010" + "0" * 5000),
        dividend_yield=Decimal("0.01250" + "0" * 5000),
    )
    eastern = datetime.fromisoformat("2026-09-05T10:29:58-04:00")
    assert api.greek_input_hash(
        verbose,
        contract=contract(strike=Decimal("650." + "0" * 5000)),
        method=api.FIXTURE_GREEK_METHOD,
        as_of=eastern,
    ) == api.greek_input_hash(
        inputs(), contract=contract(), method=api.FIXTURE_GREEK_METHOD, as_of=AS_OF
    )


@pytest.mark.parametrize(
    ("factory", "changes", "error"),
    [
        (inputs, {"option_quote_hash": True}, TypeError),
        (inputs, {"underlying_quote_hash": "A" * 64}, ValueError),
        (inputs, {"rate": 0}, TypeError),
        (inputs, {"dividend_yield": Decimal("NaN")}, ValueError),
        (inputs, {"rate_unit": ""}, ValueError),
        (api.GreekMethodSpec, {"method_id": 1, "method_version": "1", "assumptions_id": "a"}, TypeError),
        (api.GreekMethodSpec, {"method_id": "m", "method_version": "", "assumptions_id": "a"}, ValueError),
        (api.GreekMethodSpec, {"method_id": "m", "method_version": "1", "assumptions_id": "a", "max_age": 5}, TypeError),
        (api.GreekMethodSpec, {"method_id": "m", "method_version": "1", "assumptions_id": "a", "max_age": timedelta(0)}, ValueError),
        (api.GreekMethodSpec, {"method_id": "m", "method_version": "1", "assumptions_id": "a", "max_age": timedelta(seconds=5, microseconds=1)}, ValueError),
    ],
)
def test_frozen_constructors_reject_inexact_or_malformed_values(factory, changes, error) -> None:
    with pytest.raises(error):
        factory(**changes)


def test_records_are_frozen_and_method_hash_cannot_be_supplied() -> None:
    value_inputs = inputs()
    with pytest.raises(FrozenInstanceError):
        value_inputs.rate = Decimal("0")
    with pytest.raises(FrozenInstanceError):
        api.FIXTURE_GREEK_METHOD.max_age = timedelta(seconds=1)
    with pytest.raises(TypeError):
        api.GreekMethodSpec(
            "m", "1", "a", method_spec_hash="0" * 64
        )


@pytest.mark.parametrize(
    ("value_inputs", "value_contract"),
    [
        (inputs(rate=Decimal("1E+1000")), contract()),
        (inputs(dividend_yield=Decimal("-1E-1001")), contract()),
        (inputs(), contract(strike=Decimal("1E+1000"))),
        (inputs(), contract(multiplier=10**1000)),
        (inputs(), contract(multiplier=-(10**1000))),
    ],
)
def test_bounded_scalar_representations_raise_one_controlled_error(
    value_inputs, value_contract
) -> None:
    with pytest.raises(
        ValueError, match="Greek input identity exceeds representation limits"
    ):
        api.greek_input_hash(
            value_inputs,
            contract=value_contract,
            method=api.FIXTURE_GREEK_METHOD,
            as_of=AS_OF,
        )


def test_allowed_integer_boundary_ignores_process_string_limit() -> None:
    boundary = 10**999
    value_contract = contract(multiplier=-boundary)
    expected = api.greek_input_hash(
        inputs(), contract=value_contract, method=api.FIXTURE_GREEK_METHOD, as_of=AS_OF
    )
    original_limit = sys.get_int_max_str_digits()
    try:
        sys.set_int_max_str_digits(640)
        limited = api.greek_input_hash(
            inputs(),
            contract=value_contract,
            method=api.FIXTURE_GREEK_METHOD,
            as_of=AS_OF,
        )
    finally:
        sys.set_int_max_str_digits(original_limit)
    assert limited == expected


def test_identity_does_not_mutate_the_ambient_decimal_context() -> None:
    with localcontext() as context:
        context.prec = 2
        context.flags[Inexact] = True
        context.traps[InvalidOperation] = True
        before = (context.prec, context.flags.copy(), context.traps.copy())
        result = api.greek_input_hash(
            inputs(), contract=contract(), method=api.FIXTURE_GREEK_METHOD, as_of=AS_OF
        )
        after = (context.prec, context.flags.copy(), context.traps.copy())
    assert len(result) == 64
    assert after == before


@pytest.mark.parametrize(
    ("changes", "error", "message"),
    [
        ({"inputs": object()}, TypeError, "inputs must be a GreekInputs"),
        ({"contract": "SPY"}, TypeError, "contract must be a ContractId"),
        ({"method": object()}, TypeError, "method must be a GreekMethodSpec"),
        ({"as_of": datetime(2026, 9, 5)}, ValueError, "as_of must be timezone-aware"),
    ],
)
def test_identity_function_rejects_nonexact_trusted_arguments(changes, error, message) -> None:
    values = {
        "inputs": inputs(),
        "contract": contract(),
        "method": api.FIXTURE_GREEK_METHOD,
        "as_of": AS_OF,
    }
    values.update(changes)
    with pytest.raises(error, match=message):
        api.greek_input_hash(**values)
