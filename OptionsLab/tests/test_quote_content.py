from dataclasses import FrozenInstanceError
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, Inexact, InvalidOperation, localcontext
import hashlib
from inspect import signature
import json
import sys

import pytest

import options_lab.quote_content as api
from options_lab.contracts import ContractId
from options_lab.observations import ObservationMeta
from options_lab.quotes import QuoteObservation
from options_lab.underlying import UnderlyingQuote


UTC = timezone.utc
EVENT = datetime(2026, 9, 5, 14, 29, 58, tzinfo=UTC)
AVAILABLE = EVENT + timedelta(seconds=1)
RECEIVED = EVENT + timedelta(seconds=2)


def contract(**changes: object) -> ContractId:
    values = {
        "underlying": "SPY",
        "expiry": date(2026, 9, 18),
        "right": "call",
        "strike": Decimal("650.00"),
        "multiplier": 100,
        "deliverable_id": "standard-spy-100",
    }
    values.update(changes)
    return ContractId(**values)


def meta(**changes: object) -> ObservationMeta:
    values = {
        "source": "synthetic-sip-fixture",
        "provider_record_id": "option-42",
        "raw_ref": "raw://option/42",
        "feed_class": "realtime",
        "fidelity": "genuine",
        "kind": "quote",
        "event_at": EVENT,
        "available_at": AVAILABLE,
        "received_at": RECEIVED,
        "availability_basis": "measured",
        "availability_evidence_ref": "synthetic://capture/option/42",
        "interval_start": None,
        "interval_end": None,
        "is_fill_forward": False,
        "quality_flags": ("firm", "odd_lot_excluded"),
    }
    values.update(changes)
    return ObservationMeta(**values)


def option_quote(**changes: object) -> QuoteObservation:
    values = {
        "contract": contract(),
        "meta": meta(),
        "bid": Decimal("5.00"),
        "ask": Decimal("5.10"),
        "bid_at": EVENT,
        "ask_at": EVENT + timedelta(microseconds=1),
        "bid_size": 7,
        "ask_size": 11,
    }
    values.update(changes)
    return QuoteObservation(**values)


def underlying_meta(**changes: object) -> ObservationMeta:
    values = {
        "provider_record_id": "underlying-42",
        "raw_ref": "raw://underlying/42",
        "availability_evidence_ref": "synthetic://capture/underlying/42",
    }
    values.update(changes)
    return meta(**values)


def underlying_quote(**changes: object) -> UnderlyingQuote:
    values = {
        "symbol": "SPY",
        "meta": underlying_meta(),
        "bid": Decimal("650.00"),
        "ask": Decimal("650.02"),
        "bid_at": EVENT,
        "ask_at": EVENT + timedelta(microseconds=1),
        "bid_size": 700,
        "ask_size": 1100,
    }
    values.update(changes)
    return UnderlyingQuote(**values)


def digest(snapshot: dict[str, object]) -> str:
    payload = json.dumps(
        snapshot,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def metadata_snapshot(*, underlying: bool = False) -> dict[str, object]:
    kind = "underlying" if underlying else "option"
    return {
        "source": "synthetic-sip-fixture",
        "provider_record_id": f"{kind}-42",
        "feed_class": "realtime",
        "fidelity": "genuine",
        "kind": "quote",
        "event_at": "2026-09-05T14:29:58+00:00",
        "available_at": "2026-09-05T14:29:59+00:00",
        "availability_basis": "measured",
        "availability_evidence_ref": f"synthetic://capture/{kind}/42",
        "interval_start": None,
        "interval_end": None,
        "is_fill_forward": False,
        "quality_flags": ["firm", "odd_lot_excluded"],
    }


def test_option_identity_matches_independent_explicit_json_sha_golden() -> None:
    observed = option_quote()
    snapshot = {
        "record_kind": "options_lab.option_quote",
        "quote_content_schema_version": 1,
        "contract": {
            "underlying": "SPY",
            "expiry": "2026-09-18",
            "right": "call",
            "strike": "650",
            "multiplier": {"encoding": "base10", "value": "100"},
            "deliverable_id": "standard-spy-100",
        },
        "metadata": metadata_snapshot(),
        "bid": "5",
        "ask": "5.1",
        "bid_at": "2026-09-05T14:29:58+00:00",
        "ask_at": "2026-09-05T14:29:58.000001+00:00",
        "bid_size": {"encoding": "base10", "value": "7"},
        "ask_size": {"encoding": "base10", "value": "11"},
        "price_unit": "option_premium_USD_per_share",
        "size_unit": "contracts",
    }

    identity = api.identify_quote_content(observed)

    assert digest(snapshot) == "1729efdbd79f75d344e531df0106dc85aa11f8887f2ba2d0709dc233828305f7"
    assert identity.quote is observed
    assert identity.reasons == ()
    assert identity.content_hash == digest(snapshot)


def test_underlying_identity_matches_independent_explicit_json_sha_golden() -> None:
    observed = underlying_quote()
    snapshot = {
        "record_kind": "options_lab.underlying_quote",
        "quote_content_schema_version": 1,
        "symbol": "SPY",
        "metadata": metadata_snapshot(underlying=True),
        "bid": "650",
        "ask": "650.02",
        "bid_at": "2026-09-05T14:29:58+00:00",
        "ask_at": "2026-09-05T14:29:58.000001+00:00",
        "bid_size": {"encoding": "base10", "value": "700"},
        "ask_size": {"encoding": "base10", "value": "1100"},
        "price_unit": "USD_per_share",
        "size_unit": "shares",
    }

    identity = api.identify_quote_content(observed)

    assert digest(snapshot) == "885b937778187b37a4d5c1b3d47a97ccb04b9d6493c785767d2cef8b3031fff7"
    assert identity.quote is observed
    assert identity.reasons == ()
    assert identity.content_hash == digest(snapshot)


_META_CHANGES = (
    {"source": "other-source"},
    {"provider_record_id": "new-record-same-price"},
    {"feed_class": "indicative"},
    {"fidelity": "synthetic"},
    {"kind": "interval"},
    {"event_at": EVENT - timedelta(microseconds=1)},
    {"available_at": AVAILABLE + timedelta(microseconds=1)},
    {"availability_basis": "assumed"},
    {"availability_evidence_ref": "synthetic://other-proof"},
    {"interval_start": EVENT - timedelta(seconds=1)},
    {"interval_end": EVENT + timedelta(seconds=1)},
    {"is_fill_forward": True},
    {"quality_flags": ("odd_lot_excluded", "firm")},
)


@pytest.mark.parametrize(
    "changes",
    (
        {"contract": contract(underlying="QQQ")},
        {"contract": contract(expiry=date(2026, 9, 19))},
        {"contract": contract(right="put")},
        {"contract": contract(strike=Decimal("-650"))},
        {"contract": contract(multiplier=-100)},
        {"contract": contract(deliverable_id="adjusted-spy")},
        *({"meta": meta(**item)} for item in _META_CHANGES),
        {"bid": Decimal("4.99")},
        {"ask": Decimal("5.11")},
        {"bid_at": EVENT - timedelta(microseconds=1)},
        {"ask_at": EVENT + timedelta(microseconds=2)},
        {"bid_size": 8},
        {"ask_size": 12},
    ),
)
def test_option_identity_binds_every_contract_quote_and_non_ingestion_meta_field(
    changes,
) -> None:
    original = api.identify_quote_content(option_quote())
    changed = api.identify_quote_content(option_quote(**changes))
    assert changed.content_hash != original.content_hash


@pytest.mark.parametrize(
    "changes",
    (
        {"symbol": "QQQ"},
        *(
            {"meta": underlying_meta(**item)}
            for item in _META_CHANGES
        ),
        {"bid": Decimal("649.99")},
        {"ask": Decimal("650.03")},
        {"bid_at": EVENT - timedelta(microseconds=1)},
        {"ask_at": EVENT + timedelta(microseconds=2)},
        {"bid_size": 701},
        {"ask_size": 1101},
    ),
)
def test_underlying_identity_binds_every_quote_and_non_ingestion_meta_field(
    changes,
) -> None:
    original = api.identify_quote_content(underlying_quote())
    changed = api.identify_quote_content(underlying_quote(**changes))
    assert changed.content_hash != original.content_hash


@pytest.mark.parametrize("factory", (option_quote, underlying_quote))
@pytest.mark.parametrize(
    "meta_changes",
    (
        {"received_at": RECEIVED + timedelta(days=1)},
        {"raw_ref": "raw://same-content/different-receipt"},
    ),
)
def test_identity_excludes_exactly_receipt_time_and_raw_locator(
    factory, meta_changes
) -> None:
    observed = factory()
    changed = factory(meta=ObservationMeta(**{**observed.meta.__dict__, **meta_changes}))
    original_identity = api.identify_quote_content(observed)
    changed_identity = api.identify_quote_content(changed)
    assert changed_identity.quote is changed
    assert changed_identity.content_hash == original_identity.content_hash


def test_two_quote_kinds_have_distinct_domains_and_units() -> None:
    option = option_quote(
        bid=Decimal("650"),
        ask=Decimal("650.02"),
        bid_size=700,
        ask_size=1100,
        meta=underlying_quote().meta,
    )
    assert api.identify_quote_content(option).content_hash != api.identify_quote_content(
        underlying_quote()
    ).content_hash


def test_missing_zero_and_quality_flag_order_remain_distinct() -> None:
    missing = api.identify_quote_content(option_quote(bid=None))
    zero = api.identify_quote_content(option_quote(bid=Decimal("-0E+999999999")))
    ordered = api.identify_quote_content(option_quote())
    reversed_flags = api.identify_quote_content(
        option_quote(meta=meta(quality_flags=("odd_lot_excluded", "firm")))
    )
    assert missing.content_hash != zero.content_hash
    assert zero.reasons == ()
    assert ordered.content_hash != reversed_flags.content_hash


def test_signed_contract_facts_are_retained_in_a_supported_identity() -> None:
    observed = option_quote(
        contract=contract(strike=Decimal("-650.25"), multiplier=-100)
    )
    identity = api.identify_quote_content(observed)
    assert identity.quote is observed
    assert identity.content_hash is not None
    assert identity.reasons == ()


@pytest.mark.parametrize("factory", (option_quote, underlying_quote))
def test_identity_requires_one_exact_supported_quote_and_is_immutable(factory) -> None:
    observed = factory()
    subclass = type("QuoteSubclass", (type(observed),), {})

    with pytest.raises(TypeError, match="quote must be a QuoteObservation or UnderlyingQuote"):
        api.identify_quote_content(object())
    with pytest.raises(TypeError, match="quote must be a QuoteObservation or UnderlyingQuote"):
        api.identify_quote_content(subclass(**observed.__dict__))
    assert tuple(signature(api.QuoteContentIdentity).parameters) == ("quote",)

    identity = api.identify_quote_content(observed)
    for field_name, value in (
        ("quote", factory()),
        ("content_hash", "0" * 64),
        ("reasons", ("integer_representation_unsupported",)),
    ):
        with pytest.raises(FrozenInstanceError):
            setattr(identity, field_name, value)


@pytest.mark.parametrize(
    "observed",
    (
        option_quote(contract=contract(strike=Decimal("1E+1000"))),
        option_quote(bid=Decimal("1E+1000")),
        underlying_quote(ask=Decimal("1E-1001")),
    ),
)
def test_decimal_representation_limit_returns_owned_evidence(observed) -> None:
    identity = api.identify_quote_content(observed)
    assert identity.quote is observed
    assert identity.content_hash is None
    assert identity.reasons == ("decimal_representation_unsupported",)


@pytest.mark.parametrize("sign", (1, -1))
def test_multiplier_bounds_both_signs_before_base10_conversion(sign) -> None:
    observed = option_quote(contract=contract(multiplier=sign * 10**1000))
    identity = api.identify_quote_content(observed)
    assert identity.quote is observed
    assert identity.content_hash is None
    assert identity.reasons == ("integer_representation_unsupported",)


@pytest.mark.parametrize("side", ("bid_size", "ask_size"))
@pytest.mark.parametrize("factory", (option_quote, underlying_quote))
def test_quote_size_representation_is_bounded(factory, side) -> None:
    identity = api.identify_quote_content(factory(**{side: 10**1000}))
    assert identity.content_hash is None
    assert identity.reasons == ("integer_representation_unsupported",)


def test_identity_retains_all_independent_representation_reasons() -> None:
    observed = option_quote(
        contract=contract(strike=Decimal("1E+1000"), multiplier=-(10**1000)),
        ask_size=10**1000,
    )
    identity = api.identify_quote_content(observed)
    assert identity.content_hash is None
    assert identity.reasons == (
        "decimal_representation_unsupported",
        "integer_representation_unsupported",
    )


def test_extreme_trailing_zeros_and_utc_offsets_are_semantically_equivalent() -> None:
    compact = option_quote()
    equivalent = option_quote(
        contract=contract(strike=Decimal("650." + ("0" * 5000))),
        bid=Decimal("5." + ("0" * 5000)),
        ask=Decimal("5.1" + ("0" * 5000)),
        meta=meta(
            event_at=datetime.fromisoformat("2026-09-05T10:29:58-04:00"),
            available_at=datetime.fromisoformat("2026-09-05T10:29:59-04:00"),
            received_at=datetime.fromisoformat("2026-09-05T10:30:00-04:00"),
        ),
        bid_at=datetime.fromisoformat("2026-09-05T10:29:58-04:00"),
        ask_at=datetime.fromisoformat("2026-09-05T10:29:58.000001-04:00"),
    )
    compact_identity = api.identify_quote_content(compact)
    equivalent_identity = api.identify_quote_content(equivalent)
    assert equivalent_identity.reasons == ()
    assert equivalent_identity.content_hash == compact_identity.content_hash


def test_allowed_integer_boundary_is_independent_of_process_digit_limit() -> None:
    boundary = 10**999
    observed = option_quote(
        contract=contract(multiplier=-boundary),
        bid_size=boundary,
        ask_size=boundary,
    )
    expected = api.identify_quote_content(observed)
    original_limit = sys.get_int_max_str_digits()
    try:
        sys.set_int_max_str_digits(640)
        limited = api.identify_quote_content(observed)
    finally:
        sys.set_int_max_str_digits(original_limit)
    assert limited.reasons == ()
    assert limited.content_hash == expected.content_hash


@pytest.mark.parametrize("factory", (option_quote, underlying_quote))
def test_identity_does_not_mutate_the_ambient_decimal_context(factory) -> None:
    observed = factory()
    with localcontext() as context:
        context.prec = 2
        context.flags[Inexact] = True
        context.traps[InvalidOperation] = True
        before = (context.prec, context.flags.copy(), context.traps.copy())
        identity = api.identify_quote_content(observed)
        after = (context.prec, context.flags.copy(), context.traps.copy())
    assert identity.content_hash is not None
    assert identity.reasons == ()
    assert after == before
