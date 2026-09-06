"""Build independently registered tick-rule scenarios without quote dependencies."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

from build_fixture import _member, _payload_bytes
from options_lab.greeks import FIXTURE_GREEK_METHOD
from options_lab.ticks import normalize_tick_rule


FIXTURE_ID = "p08c-tick-evidence-v1"
AT = datetime(2026, 9, 5, 14, 29, 58, tzinfo=timezone.utc)


def build_fixture() -> bytes:
    """
    Build tick-only registered good and adverse evidence.

    :returns: A complete readable fixture payload.
    :raises RuntimeError: If an intended member violates its normalization outcome.
    """
    contract = {
        "underlying": "SPY", "expiry": "2026-09-18", "right": "call",
        "strike": "650", "multiplier": 100, "deliverable_id": "standard-spy-100",
    }
    body = {
        "contract": contract, "increment": "0.01", "price_from": "5.00",
        "price_until": "10.00", "effective_from": (AT + timedelta(seconds=1)).isoformat(),
        "effective_until": (AT + timedelta(seconds=10)).isoformat(),
        "available_at": AT.isoformat(), "source": "synthetic-tick-reference",
        "provider_record_id": "tick-provider-good", "source_version": "1",
        "units": "USD_per_share", "rule_id": "fixture-usd-premium-tick-v1",
    }
    changes = {
        "tick-good": {}, "tick-missing-increment": {"increment": None},
        "tick-zero-increment": {"increment": "0"}, "tick-negative-increment": {"increment": "-0.01"},
        "tick-wrong-units": {"units": "USD_per_contract"}, "tick-wrong-version": {"source_version": "2"},
        "tick-unknown-end": {"effective_until": None}, "tick-negative-band": {"price_from": "-1"},
        "tick-reversed-band": {"price_until": "4.99"}, "tick-unbounded": {"price_until": None},
        "tick-late-available": {"available_at": (AT + timedelta(seconds=3)).isoformat()},
        "tick-non-grid-lower": {"price_from": "5.005", "price_until": "10.005"},
        "tick-unknown-start": {"effective_from": None},
        "tick-reversed-effective": {"effective_until": AT.isoformat()},
        "tick-unknown-available": {"available_at": None},
        "tick-wrong-source": {"source": "other-tick-source"},
        "tick-unknown-definition": {"rule_id": "unknown-tick-definition"},
        "tick-ambiguous": {"provider_record_id": "tick-ambiguous-provider"},
        "tick-extreme-band": {"price_from": "1" + ("0" * 1000), "price_until": None},
        "tick-equal-band": {"price_until": "5.00"},
        "tick-equal-effective": {"effective_until": (AT + timedelta(seconds=1)).isoformat()},
        "tick-available-boundary": {"available_at": (AT + timedelta(seconds=2)).isoformat()},
        "tick-tiny-grid": {"increment": "0." + "0" * 999 + "1", "price_from": "0", "price_until": None},
    }
    members = []
    for index, (record_id, changed) in enumerate(changes.items(), 1):
        raw = deepcopy(body)
        raw.update(changed)
        raw["provider_record_id"] = f"tick-provider-{index}"
        envelope = {
            "event_id": f"tick-event-{index}", "raw_ref": f"synthetic://tick/{index}",
            "simulated_received_at": (AT + timedelta(seconds=4)).isoformat(),
            "stream_id": "tick-stream-1", "receive_sequence": index,
            "supersedes_record_id": None, "contract": None, "metadata": None,
        }
        normalized = normalize_tick_rule(
            raw, event_id=envelope["event_id"], raw_ref=envelope["raw_ref"],
            received_at=datetime.fromisoformat(envelope["simulated_received_at"]),
        )
        if normalized.value is None:
            raise RuntimeError(f"intended tick member {record_id} was rejected")
        members.append(_member(record_id, "tick_rule", "tick-profile-1", raw, envelope))
    ambiguous = deepcopy(next(member for member in members if member["record_id"] == "tick-ambiguous"))
    ambiguous["record_id"] = "tick-ambiguous-duplicate"
    ambiguous["envelope"]["event_id"] = "tick-ambiguous-duplicate-event"
    duplicate_result = normalize_tick_rule(
        ambiguous["raw_body"], event_id=ambiguous["envelope"]["event_id"],
        raw_ref=ambiguous["envelope"]["raw_ref"],
        received_at=datetime.fromisoformat(ambiguous["envelope"]["simulated_received_at"]),
    )
    if duplicate_result.value is None:
        raise RuntimeError("intended ambiguous tick member was rejected")
    members.append(_member(ambiguous["record_id"], "tick_rule", "tick-profile-1",
                           ambiguous["raw_body"], ambiguous["envelope"]))
    malformed = deepcopy(next(member for member in members if member["record_id"] == "tick-good"))
    malformed["record_id"] = "tick-malformed-unrelated"
    del malformed["raw_body"]["source"]
    malformed_result = normalize_tick_rule(
        malformed["raw_body"], event_id=malformed["envelope"]["event_id"],
        raw_ref=malformed["envelope"]["raw_ref"],
        received_at=datetime.fromisoformat(malformed["envelope"]["simulated_received_at"]),
    )
    rejection = malformed_result.rejection
    if rejection is None or (rejection.field, rejection.code) != ("source", "missing"):
        raise RuntimeError("intentional malformed tick member must reject source/missing")
    members.insert(0, _member(malformed["record_id"], "tick_rule", "tick-profile-1",
                              malformed["raw_body"], malformed["envelope"]))
    return _payload_bytes({
        "schema_version": 1, "normalization_version": 1, "fixture_id": FIXTURE_ID,
        "generator_id": "optionslab-tick-fixture-builder", "generator_version": "1",
        "assembled_at": datetime.now(timezone.utc).isoformat(), "generator_source_ref": "OptionsLab/tests/build_tick_fixture.py",
        "origin": "synthetic", "permitted_use": "core_fixture",
        "modeled_source_profiles": [{
            "profile_id": "tick-profile-1", "kind": "tick_rule", "source": "synthetic-tick-reference",
            "stream_id": "tick-stream-1", "feed_class": None, "fidelity": None,
            "availability_basis": "measured", "units": {"price": "USD_per_share"},
            "record_identity_rule": "new_provider_record_id_per_update",
        }],
        "definitions": {"greek_method": {
            "method_id": FIXTURE_GREEK_METHOD.method_id, "method_version": FIXTURE_GREEK_METHOD.method_version,
            "assumptions_id": FIXTURE_GREEK_METHOD.assumptions_id, "method_spec_hash": FIXTURE_GREEK_METHOD.method_spec_hash,
        }, "coherence_protocol_ids": [], "tick_definition_ids": ["fixture-usd-premium-tick-v1"]},
        "members": members,
    })


if __name__ == "__main__":
    target = Path(__file__).parent / "fixtures" / f"{FIXTURE_ID}.json"
    target.write_bytes(build_fixture())
    print(target)
