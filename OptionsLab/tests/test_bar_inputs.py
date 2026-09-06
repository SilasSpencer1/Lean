from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, Inexact, InvalidOperation, localcontext
import hashlib
import json
import sys

import pytest

import options_lab.bar_inputs as api
from options_lab.bars import UnderlyingBar
from options_lab.observations import ObservationMeta


UTC = timezone.utc
START = datetime(2026, 9, 4, 13, 30, tzinfo=UTC)
END = START + timedelta(minutes=1)


def meta(**changes: object) -> ObservationMeta:
    """Return synthetic-bar-meta-v1 evidence without source admission."""
    values = {
        "source": "synthetic-bars",
        "provider_record_id": "SPY-2026-09-04T09:30-rev-1",
        "raw_ref": "synthetic://bars/SPY/2026-09-04/09:30/rev-1",
        "feed_class": "delayed",
        "fidelity": "synthetic",
        "kind": "interval",
        "event_at": None,
        "available_at": END,
        "received_at": END + timedelta(seconds=2),
        "availability_basis": "assumed",
        "availability_evidence_ref": "synthetic-bar-clock-v1",
        "interval_start": START,
        "interval_end": END,
        "is_fill_forward": False,
        "quality_flags": (),
    }
    values.update(changes)
    return ObservationMeta(**values)


def valid_raw(**changes: object) -> dict[str, object]:
    """Return one complete external bar body."""
    raw: dict[str, object] = {
        "symbol": "SPY",
        "close_price": "100.2500",
        "volume": "12.50",
        "vwap_numerator": "1250.000",
        "vwap_denominator": "12.5",
        "price_basis": "raw",
        "volume_definition_id": "synthetic-sip-eligible-shares-v1",
        "vwap_definition_id": "synthetic-sip-exact-vwap-v1",
        "revision_id": "rev-1",
        "supersedes_revision_id": None,
    }
    raw.update(changes)
    return raw


def normalize(raw: object, **changes: object) -> api.BarValidation:
    """Normalize one body with trusted synthetic ingestion evidence."""
    inputs = {"meta": meta(), "event_id": "event-1", "receive_sequence": 7}
    inputs.update(changes)
    return api.normalize_underlying_bar(raw, **inputs)


def bar(**changes: object) -> UnderlyingBar:
    """Return one normalized bar with optional trusted replacements."""
    observed = normalize(valid_raw()).value
    assert observed is not None
    return replace(observed, **changes)


def assert_failure(raw: object, field: str, code: str) -> api.BarInputRejection:
    """Assert and return one exact safe normalization rejection."""
    result = normalize(raw)
    assert result.value is None and result.rejection is not None
    assert (result.rejection.field, result.rejection.code) == (field, code)
    return result.rejection


def test_complete_body_normalizes_every_retained_bar_fact() -> None:
    result = normalize(valid_raw())

    assert type(result) is api.BarValidation
    assert result.rejection is None and type(result.value) is UnderlyingBar
    observed = result.value
    assert observed.meta == meta() and observed.receive_sequence == 7
    assert observed.symbol == "SPY"
    assert observed.close_price == Decimal("100.2500")
    assert observed.volume == Decimal("12.50")
    assert observed.vwap_numerator == Decimal("1250.000")
    assert observed.vwap_denominator == Decimal("12.5")
    assert observed.price_basis == "raw"
    assert observed.volume_definition_id == "synthetic-sip-eligible-shares-v1"
    assert observed.vwap_definition_id == "synthetic-sip-exact-vwap-v1"
    assert observed.revision_id == "rev-1"
    assert observed.supersedes_revision_id is None


def test_all_nullable_body_facts_preserve_explicit_missing_values() -> None:
    nullable = (
        "close_price", "volume", "vwap_numerator", "vwap_denominator",
        "volume_definition_id", "vwap_definition_id", "supersedes_revision_id",
    )
    observed = normalize(valid_raw(**dict.fromkeys(nullable))).value
    assert observed is not None
    assert tuple(getattr(observed, field) for field in nullable) == (None,) * 7


class DictSubclass(dict):
    pass


class StringSubclass(str):
    pass


class IntSubclass(int):
    pass


class MetaSubclass(ObservationMeta):
    pass


@pytest.mark.parametrize("raw", [None, [], "bar", object(), DictSubclass()])
def test_root_requires_an_exact_dictionary(raw) -> None:
    assert_failure(raw, "$", "expected_exact_dict")


def test_unknown_fields_precede_missing_without_leaking_provider_values() -> None:
    raw = valid_raw(secret_provider_key="classified")
    del raw["symbol"]
    rejection = assert_failure(raw, "$", "unknown_fields")
    assert "secret_provider_key" not in repr(rejection)
    assert "classified" not in repr(rejection)


@pytest.mark.parametrize("field", tuple(valid_raw()))
def test_every_body_field_is_mandatory_even_when_nullable(field) -> None:
    raw = valid_raw()
    del raw[field]
    assert_failure(raw, field, "missing")


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("symbol", "", "invalid_value"),
        ("symbol", 1, "invalid_type"),
        ("symbol", StringSubclass("SPY"), "invalid_type"),
        ("close_price", "-0.01", "invalid_value"),
        ("volume", "-1", "invalid_value"),
        ("vwap_numerator", "NaN", "invalid_decimal"),
        ("vwap_denominator", "Infinity", "invalid_decimal"),
        ("close_price", "1e2", "invalid_decimal"),
        ("volume", " 1", "invalid_decimal"),
        ("vwap_numerator", Decimal("1"), "invalid_type"),
        ("vwap_denominator", True, "invalid_type"),
        ("price_basis", "provider-adjusted", "invalid_value"),
        ("price_basis", None, "invalid_type"),
        ("volume_definition_id", "", "invalid_value"),
        ("vwap_definition_id", 4, "invalid_type"),
        ("revision_id", "", "invalid_value"),
        ("revision_id", None, "invalid_type"),
        ("supersedes_revision_id", "", "invalid_value"),
        ("supersedes_revision_id", 1, "invalid_type"),
    ],
)
def test_malformed_external_values_return_fixed_safe_codes(field, value, code) -> None:
    assert_failure(valid_raw(**{field: value}), field, code)


@pytest.mark.parametrize("basis", ["raw", "split_adjusted", "total_return_adjusted", "unknown"])
def test_all_declared_price_bases_remain_representable(basis) -> None:
    observed = normalize(valid_raw(price_basis=basis)).value
    assert observed is not None and observed.price_basis == basis


@pytest.mark.parametrize("value", ["+1.", ".5", "-0.00", "123456789.123456789"])
def test_amounts_preserve_exact_precision_and_decimal_context(value) -> None:
    with localcontext() as context:
        context.prec = 3
        context.flags[Inexact] = True
        context.traps[InvalidOperation] = True
        before = (context.prec, context.flags.copy(), context.traps.copy())
        result = normalize(valid_raw(close_price=value))
        after = (context.prec, context.flags.copy(), context.traps.copy())
    assert result.value is not None
    assert result.value.close_price.as_tuple() == Decimal(value).as_tuple()
    assert after == before


def test_source_dictionary_mutation_cannot_change_normalized_bar() -> None:
    raw = valid_raw()
    observed = normalize(raw).value
    raw.update(symbol="AAPL", close_price="2", revision_id="rev-9")
    assert observed is not None
    assert (observed.symbol, observed.close_price, observed.revision_id) == (
        "SPY", Decimal("100.2500"), "rev-1",
    )


class CollidingKey:
    def __init__(self) -> None:
        self.callbacks: list[str] = []

    def __hash__(self) -> int:
        return hash("symbol")

    def __eq__(self, other: object) -> bool:
        self.callbacks.append("eq")
        raise AssertionError("must not compare hostile keys")

    def __repr__(self) -> str:
        self.callbacks.append("repr")
        raise AssertionError("must not represent hostile keys")


class HostileScalar:
    def __repr__(self) -> str:
        raise AssertionError("must not represent hostile scalars")

    def __eq__(self, other: object) -> bool:
        raise AssertionError("must not compare hostile scalars")


def test_hostile_keys_and_scalars_never_invoke_callbacks() -> None:
    key = CollidingKey()
    assert_failure({key: None}, "$", "unknown_fields")
    assert key.callbacks == []
    for field in ("symbol", "close_price", "price_basis", "revision_id"):
        assert_failure(valid_raw(**{field: HostileScalar()}), field, "invalid_type")


def test_first_parse_failure_follows_declared_field_order() -> None:
    good = valid_raw()
    for index, field in enumerate(good):
        raw = {
            name: value if position < index else HostileScalar()
            for position, (name, value) in enumerate(good.items())
        }
        assert_failure(raw, field, "invalid_type")


class ExplodingDict(dict):
    def __iter__(self):
        raise AssertionError("raw must not be inspected")


@pytest.mark.parametrize(
    ("changes", "error"),
    [
        ({"meta": object()}, TypeError),
        ({"meta": MetaSubclass(**meta().__dict__)}, TypeError),
        ({"event_id": 7}, TypeError),
        ({"event_id": StringSubclass("e")}, TypeError),
        ({"event_id": ""}, ValueError),
        ({"receive_sequence": True}, TypeError),
        ({"receive_sequence": IntSubclass(1)}, TypeError),
        ({"receive_sequence": -1}, ValueError),
    ],
)
def test_trusted_envelope_is_validated_before_raw_inspection(changes, error) -> None:
    inputs = {"meta": meta(), "event_id": "event-1", "receive_sequence": 7}
    inputs.update(changes)
    with pytest.raises(error):
        api.normalize_underlying_bar(ExplodingDict(), **inputs)


def test_provider_body_cannot_override_trusted_envelope_facts() -> None:
    for field in ("meta", "event_id", "receive_sequence"):
        assert_failure(valid_raw(**{field: "provider-value"}), "$", "unknown_fields")


def test_rejection_evidence_is_fixed_safe_and_immutable() -> None:
    rejection = assert_failure([], "$", "expected_exact_dict")
    assert (rejection.event_id, rejection.received_at, rejection.raw_ref) == (
        "event-1", meta().received_at, meta().raw_ref,
    )
    assert rejection.stage == "underlying_bar_normalization"
    assert rejection.reasons == ("expected_exact_dict",)
    with pytest.raises(FrozenInstanceError):
        rejection.code = "unknown_fields"


@pytest.mark.parametrize(
    ("field", "code"),
    [
        ("secret", "invalid_type"),
        ("$", "missing"),
        ("symbol", "invalid_decimal"),
        ("close_price", "invalid_timestamp"),
        ("price_basis", "invalid_decimal"),
        ("revision_id", "invalid_decimal"),
    ],
)
def test_rejection_rejects_incompatible_field_code(field, code) -> None:
    with pytest.raises(ValueError):
        api.BarInputRejection("e", END, "r", field, code)


def test_validation_requires_exactly_one_exact_concrete_outcome() -> None:
    value = normalize(valid_raw()).value
    rejection = assert_failure([], "$", "expected_exact_dict")
    for inputs, error in [
        ({}, ValueError),
        ({"value": value, "rejection": rejection}, ValueError),
        ({"value": "bad"}, TypeError),
        ({"rejection": "bad"}, TypeError),
    ]:
        with pytest.raises(error):
            api.BarValidation(**inputs)


def test_identity_hashes_explicit_allowlisted_full_and_economic_snapshots() -> None:
    observed = bar()
    identity = api.identify_underlying_bar(observed)
    full = {
        "record_kind": "options_lab.underlying_bar",
        "bar_content_schema_version": 1,
        "symbol": "SPY",
        "metadata": {
            "source": "synthetic-bars",
            "provider_record_id": "SPY-2026-09-04T09:30-rev-1",
            "raw_ref": "synthetic://bars/SPY/2026-09-04/09:30/rev-1",
            "feed_class": "delayed",
            "fidelity": "synthetic",
            "kind": "interval",
            "event_at": None,
            "available_at": "2026-09-04T13:31:00+00:00",
            "received_at": "2026-09-04T13:31:02+00:00",
            "availability_basis": "assumed",
            "availability_evidence_ref": "synthetic-bar-clock-v1",
            "interval_start": "2026-09-04T13:30:00+00:00",
            "interval_end": "2026-09-04T13:31:00+00:00",
            "is_fill_forward": False,
            "quality_flags": [],
        },
        "close_price": "100.25",
        "volume": "12.5",
        "vwap_numerator": "1250",
        "vwap_denominator": "12.5",
        "price_basis": "raw",
        "volume_definition_id": "synthetic-sip-eligible-shares-v1",
        "vwap_definition_id": "synthetic-sip-exact-vwap-v1",
        "revision_id": "rev-1",
        "supersedes_revision_id": None,
        "receive_sequence": {"encoding": "base10", "value": "7"},
    }
    economic = {**full, "metadata": dict(full["metadata"])}
    del economic["metadata"]["raw_ref"]
    del economic["metadata"]["received_at"]
    del economic["receive_sequence"]

    def digest(snapshot: dict[str, object]) -> str:
        payload = json.dumps(
            snapshot, sort_keys=True, separators=(",", ":"),
            ensure_ascii=True, allow_nan=False,
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    assert identity.bar is observed
    assert identity.full_reasons == identity.economic_reasons == ()
    assert identity.full_content_hash == digest(full)
    assert identity.economic_content_hash == digest(economic)


def test_identity_is_context_independent_and_canonicalizes_semantic_equivalents() -> None:
    first = bar()
    equivalent = bar(
        close_price=Decimal("100.25000"),
        volume=Decimal("12.5000"),
        vwap_numerator=Decimal("1250.0"),
        meta=meta(
            available_at=datetime.fromisoformat("2026-09-04T09:31:00-04:00"),
            received_at=datetime.fromisoformat("2026-09-04T09:31:02-04:00"),
            interval_start=datetime.fromisoformat("2026-09-04T09:30:00-04:00"),
            interval_end=datetime.fromisoformat("2026-09-04T09:31:00-04:00"),
        ),
    )
    with localcontext() as context:
        context.prec = 2
        context.flags[Inexact] = True
        context.traps[InvalidOperation] = True
        before = (context.prec, context.flags.copy(), context.traps.copy())
        identities = [api.identify_underlying_bar(item) for item in (first, equivalent)]
        after = (context.prec, context.flags.copy(), context.traps.copy())
    assert identities[0].full_content_hash == identities[1].full_content_hash
    assert identities[0].economic_content_hash == identities[1].economic_content_hash
    assert after == before


@pytest.mark.parametrize(
    "changes",
    [
        {"symbol": "AAPL"},
        {"meta": meta(source="other-source")},
        {"meta": meta(provider_record_id="other-record")},
        {"meta": meta(event_at=START)},
        {"meta": meta(available_at=END + timedelta(seconds=1))},
        {"meta": meta(availability_basis="measured")},
        {"meta": meta(feed_class="realtime")},
        {"meta": meta(fidelity="genuine")},
        {"meta": meta(kind="quote")},
        {"meta": meta(interval_start=START + timedelta(seconds=1))},
        {"meta": meta(interval_end=END + timedelta(seconds=1))},
        {"meta": meta(is_fill_forward=True)},
        {"meta": meta(quality_flags=("late",))},
        {"close_price": Decimal("101")},
        {"volume": None},
        {"vwap_numerator": Decimal("1251")},
        {"vwap_denominator": Decimal("13")},
        {"price_basis": "split_adjusted"},
        {"volume_definition_id": "other-volume"},
        {"vwap_definition_id": "other-vwap"},
        {"revision_id": "rev-2"},
        {"supersedes_revision_id": "rev-0"},
    ],
)
def test_economic_hash_binds_every_economic_and_provenance_group(changes) -> None:
    original = api.identify_underlying_bar(bar())
    changed = api.identify_underlying_bar(bar(**changes))
    assert changed.economic_content_hash != original.economic_content_hash
    assert changed.full_content_hash != original.full_content_hash


@pytest.mark.parametrize(
    "changes",
    [
        {"meta": meta(raw_ref="synthetic://other-receipt")},
        {"meta": meta(received_at=END + timedelta(seconds=3))},
        {"receive_sequence": 8},
    ],
)
def test_economic_hash_excludes_only_ingestion_receipt_facts(changes) -> None:
    original = api.identify_underlying_bar(bar())
    changed = api.identify_underlying_bar(bar(**changes))
    assert changed.economic_content_hash == original.economic_content_hash
    assert changed.full_content_hash != original.full_content_hash


def test_missing_zero_and_quality_order_remain_distinct_content() -> None:
    missing = api.identify_underlying_bar(bar(volume=None))
    zero = api.identify_underlying_bar(bar(volume=Decimal("-0E+999999999")))
    ordered = api.identify_underlying_bar(bar(meta=meta(quality_flags=("a", "b"))))
    reversed_flags = api.identify_underlying_bar(bar(meta=meta(quality_flags=("b", "a"))))
    assert missing.economic_content_hash != zero.economic_content_hash
    assert zero.economic_reasons == ()
    assert ordered.economic_content_hash != reversed_flags.economic_content_hash


def test_decimal_representation_limit_returns_owned_evidence_without_allocation() -> None:
    identity = api.identify_underlying_bar(bar(close_price=Decimal("1E+1000")))
    assert identity.full_content_hash is identity.economic_content_hash is None
    assert identity.full_reasons == identity.economic_reasons == (
        "decimal_representation_unsupported",
    )


def test_extreme_trailing_zero_spelling_matches_compact_decimal_identity() -> None:
    compact = api.identify_underlying_bar(bar(close_price=Decimal("1")))
    padded = api.identify_underlying_bar(
        bar(close_price=Decimal("1." + ("0" * 5000)))
    )
    assert padded.full_reasons == padded.economic_reasons == ()
    assert padded.full_content_hash == compact.full_content_hash
    assert padded.economic_content_hash == compact.economic_content_hash


def test_oversized_sequence_blocks_only_full_identity() -> None:
    identity = api.identify_underlying_bar(bar(receive_sequence=10 ** 5000))
    assert identity.full_content_hash is None
    assert identity.full_reasons == ("integer_representation_unsupported",)
    assert identity.economic_content_hash is not None
    assert identity.economic_reasons == ()


def test_sequence_identity_is_independent_of_interpreter_digit_limit() -> None:
    observed = bar(receive_sequence=10 ** 699)
    expected = api.identify_underlying_bar(observed)
    original_limit = sys.get_int_max_str_digits()
    try:
        sys.set_int_max_str_digits(640)
        limited = api.identify_underlying_bar(observed)
    finally:
        sys.set_int_max_str_digits(original_limit)
    assert limited.full_reasons == ()
    assert limited.full_content_hash == expected.full_content_hash


def test_full_identity_retains_all_independent_representation_limits() -> None:
    identity = api.identify_underlying_bar(
        bar(close_price=Decimal("1E+1000"), receive_sequence=10 ** 5000)
    )
    assert identity.economic_reasons == ("decimal_representation_unsupported",)
    assert identity.full_reasons == (
        "decimal_representation_unsupported", "integer_representation_unsupported",
    )
    assert identity.full_content_hash is identity.economic_content_hash is None


def test_identity_requires_an_exact_bar_and_is_immutable() -> None:
    class BarSubclass(UnderlyingBar):
        pass

    with pytest.raises(TypeError):
        api.identify_underlying_bar(object())
    with pytest.raises(TypeError):
        api.identify_underlying_bar(BarSubclass(**bar().__dict__))
    identity = api.identify_underlying_bar(bar())
    with pytest.raises(FrozenInstanceError):
        identity.full_content_hash = "changed"
