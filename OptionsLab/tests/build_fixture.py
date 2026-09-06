"""Build the retained P08A synthetic fixture from current typed owners."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path

from options_lab._input_parsing import _parse_contract_id
from options_lab.greek_inputs import normalize_greek_observation
from options_lab.greeks import FIXTURE_GREEK_METHOD, GreekInputs, greek_input_hash
from options_lab.observations import normalize_observation_meta
from options_lab.quote_content import identify_quote_content
from options_lab.quote_inputs import normalize_quote_observation
from options_lab.underlying_inputs import normalize_underlying_quote


FIXTURE_ID = "p08a-greek-ready-v1"
SOURCE_REF = "OptionsLab/tests/build_fixture.py"
SIMULATED_AT = datetime(2026, 9, 5, 14, 29, 58, tzinfo=timezone.utc)


def _canonical(value: object) -> bytes:
    """Encode one JSON value with the fixture's canonical profile."""
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def _payload_bytes(value: object) -> bytes:
    """Encode a readable complete fixture payload."""
    return json.dumps(
        value, sort_keys=True, indent=2, ensure_ascii=True, allow_nan=False,
    ).encode("utf-8") + b"\n"


def _metadata(source: str, record_id: str) -> dict[str, object]:
    """Create one simulated quote metadata body."""
    published = SIMULATED_AT + timedelta(seconds=1)
    return {
        "source": source,
        "provider_record_id": record_id,
        "feed_class": "realtime",
        "fidelity": "genuine",
        "kind": "quote",
        "event_at": SIMULATED_AT.isoformat(),
        "available_at": published.isoformat(),
        "availability_basis": "measured",
        "availability_evidence_ref": f"synthetic://capture/{record_id}",
        "interval_start": None,
        "interval_end": None,
        "is_fill_forward": False,
        "quality_flags": [],
    }


def _profile(
    profile_id: str,
    kind: str,
    source: str,
    stream_id: str,
    units: dict[str, str],
) -> dict[str, object]:
    """Create one exact modeled source-profile object."""
    quote = kind != "greek_observation"
    return {
        "profile_id": profile_id,
        "kind": kind,
        "source": source,
        "stream_id": stream_id,
        "feed_class": "realtime" if quote else None,
        "fidelity": "genuine" if quote else None,
        "availability_basis": "measured",
        "units": units,
        "record_identity_rule": "new_provider_record_id_per_update",
    }


def _member(
    record_id: str,
    kind: str,
    profile_id: str,
    raw_body: dict[str, object],
    envelope: dict[str, object],
) -> dict[str, object]:
    """Create one six-field member with its canonical raw-body hash."""
    return {
        "record_id": record_id,
        "kind": kind,
        "profile_id": profile_id,
        "raw_hash": hashlib.sha256(_canonical(raw_body)).hexdigest(),
        "raw_body": raw_body,
        "envelope": envelope,
    }


def build_fixture() -> bytes:
    """
    Build one ready three-member fixture and capture its actual assembly time.

    :returns:      Canonical payload bytes ending in one newline.
    :raises ValueError:   If a current typed owner rejects the generated facts.
    :raises RuntimeError: If a current normalizer unexpectedly rejects the fixture.
    """
    simulated_received = SIMULATED_AT + timedelta(seconds=2)
    contract = {
        "underlying": "SPY",
        "expiry": "2026-09-18",
        "right": "call",
        "strike": "650",
        "multiplier": 100,
        "deliverable_id": "standard-spy-100",
    }
    option_body = {
        "bid": "5.00", "ask": "5.10",
        "bid_at": SIMULATED_AT.isoformat(), "ask_at": SIMULATED_AT.isoformat(),
        "bid_size": 7, "ask_size": 9,
    }
    underlying_body = {
        "symbol": "SPY", "bid": "650.00", "ask": "650.02",
        "bid_at": SIMULATED_AT.isoformat(), "ask_at": SIMULATED_AT.isoformat(),
        "bid_size": 700, "ask_size": 900,
    }
    option_metadata = _metadata("synthetic-sip-option", "option-provider-1")
    underlying_metadata = _metadata(
        "synthetic-sip-underlying", "underlying-provider-1"
    )
    typed_contract = _parse_contract_id(contract)
    option_meta = normalize_observation_meta(
        option_metadata, event_id="option-event-1", raw_ref="synthetic://option/1",
        received_at=simulated_received,
    )
    underlying_meta = normalize_observation_meta(
        underlying_metadata, event_id="underlying-event-1",
        raw_ref="synthetic://underlying/1", received_at=simulated_received,
    )
    if option_meta.value is None or underlying_meta.value is None:
        raise RuntimeError("generated quote metadata was rejected")
    option = normalize_quote_observation(
        option_body, contract=typed_contract, meta=option_meta.value,
        event_id="option-event-1",
    )
    underlying = normalize_underlying_quote(
        underlying_body, meta=underlying_meta.value,
        event_id="underlying-event-1",
    )
    if option.value is None or underlying.value is None:
        raise RuntimeError("generated quote body was rejected")
    option_hash = identify_quote_content(option.value).content_hash
    underlying_hash = identify_quote_content(underlying.value).content_hash
    if option_hash is None or underlying_hash is None:
        raise RuntimeError("generated quote identity was unavailable")
    greek_inputs = GreekInputs(
        option_hash, underlying_hash, Decimal("-0.01"), Decimal("0.0125"),
        FIXTURE_GREEK_METHOD.rate_unit, FIXTURE_GREEK_METHOD.dividend_unit,
        FIXTURE_GREEK_METHOD.assumptions_id,
    )
    greek_body = {
        "delta": "0.50",
        "iv": "0.20",
        "as_of": SIMULATED_AT.isoformat(),
        "available_at": (SIMULATED_AT + timedelta(seconds=1)).isoformat(),
        "delta_unit": FIXTURE_GREEK_METHOD.delta_unit,
        "iv_unit": FIXTURE_GREEK_METHOD.iv_unit,
        "method_id": FIXTURE_GREEK_METHOD.method_id,
        "method_version": FIXTURE_GREEK_METHOD.method_version,
        "inputs": {
            "option_quote_hash": option_hash,
            "underlying_quote_hash": underlying_hash,
            "rate": "-0.01",
            "dividend_yield": "0.0125",
            "rate_unit": FIXTURE_GREEK_METHOD.rate_unit,
            "dividend_unit": FIXTURE_GREEK_METHOD.dividend_unit,
            "assumptions_id": FIXTURE_GREEK_METHOD.assumptions_id,
        },
        "input_hash": greek_input_hash(
            greek_inputs, contract=typed_contract, method=FIXTURE_GREEK_METHOD,
            as_of=SIMULATED_AT,
        ),
        "source": "synthetic-greek-method",
        "provider_record_id": "greek-provider-1",
        "availability_basis": "measured",
    }
    common_envelope = {
        "simulated_received_at": simulated_received.isoformat(),
        "receive_sequence": 1,
        "supersedes_record_id": None,
    }
    members = [
        _member("option-member-1", "option_quote", "option-profile-1", option_body, {
            **common_envelope, "event_id": "option-event-1",
            "raw_ref": "synthetic://option/1", "stream_id": "option-stream-1",
            "contract": contract, "metadata": option_metadata,
        }),
        _member(
            "underlying-member-1", "underlying_quote", "underlying-profile-1",
            underlying_body, {
                **common_envelope, "event_id": "underlying-event-1",
                "raw_ref": "synthetic://underlying/1",
                "stream_id": "underlying-stream-1", "contract": None,
                "metadata": underlying_metadata,
            },
        ),
        _member("greek-member-1", "greek_observation", "greek-profile-1", greek_body, {
            **common_envelope, "event_id": "greek-event-1",
            "raw_ref": "synthetic://greek/1", "stream_id": "greek-stream-1",
            "contract": contract, "metadata": None,
        }),
    ]
    payload = {
        "schema_version": 1,
        "normalization_version": 1,
        "fixture_id": FIXTURE_ID,
        "generator_id": "optionslab-test-fixture-builder",
        "generator_version": "1",
        "assembled_at": datetime.now(timezone.utc).isoformat(),
        "generator_source_ref": SOURCE_REF,
        "origin": "synthetic",
        "permitted_use": "core_fixture",
        "modeled_source_profiles": [
            _profile("option-profile-1", "option_quote", "synthetic-sip-option",
                     "option-stream-1", {
                         "price": "option_premium_USD_per_share", "size": "contracts",
                     }),
            _profile("underlying-profile-1", "underlying_quote",
                     "synthetic-sip-underlying", "underlying-stream-1", {
                         "price": "USD_per_share", "size": "shares",
                     }),
            _profile("greek-profile-1", "greek_observation",
                     "synthetic-greek-method", "greek-stream-1", {
                         "delta": FIXTURE_GREEK_METHOD.delta_unit,
                         "iv": FIXTURE_GREEK_METHOD.iv_unit,
                         "rate": FIXTURE_GREEK_METHOD.rate_unit,
                         "dividend": FIXTURE_GREEK_METHOD.dividend_unit,
                     }),
        ],
        "definitions": {
            "greek_method": {
                "method_id": FIXTURE_GREEK_METHOD.method_id,
                "method_version": FIXTURE_GREEK_METHOD.method_version,
                "assumptions_id": FIXTURE_GREEK_METHOD.assumptions_id,
                "method_spec_hash": FIXTURE_GREEK_METHOD.method_spec_hash,
            },
            "coherence_protocol_ids": [],
            "tick_definition_ids": [],
        },
        "members": members,
    }
    greek = normalize_greek_observation(
        greek_body, contract=typed_contract, event_id="greek-event-1",
        raw_ref="synthetic://greek/1", received_at=simulated_received,
    )
    if greek.value is None:
        raise RuntimeError("generated Greek body was rejected")
    return _payload_bytes(payload)


if __name__ == "__main__":
    target = Path(__file__).parent / "fixtures" / f"{FIXTURE_ID}.json"
    target.parent.mkdir(exist_ok=True)
    payload = build_fixture()
    target.write_bytes(payload)
    print(f"{target} {hashlib.sha256(payload).hexdigest()}")
