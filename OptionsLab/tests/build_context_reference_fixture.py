"""Build joined market/reference evidence with isolated adverse source streams."""

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path

from build_fixture import _member, _payload_bytes

FIXTURE_ID = "p08d-reference-context-v1"
KINDS = ("exchange_session", "instrument_tradability", "provider_contract_mapping", "contract_reference")


def build_fixture() -> bytes:
    """
    Assemble nine actual consumed kinds and explicit independent boundary traces.

    :returns: Readable fixture bytes with truthful current assembly time.
    """
    original = json.loads((Path(__file__).parent / "fixtures/p08d-current-market-v1.json").read_bytes())
    payload = {**original, "members": [], "modeled_source_profiles": []}
    market = [deepcopy(m) for m in original["members"] if m["record_id"].startswith("market-")]
    contract = next(m["envelope"]["contract"] for m in market if m["kind"] == "option_quote")
    for row in market:
        old = next(p for p in original["modeled_source_profiles"] if p["profile_id"] == row["profile_id"])
        stream = "joined-" + row["kind"]
        payload["modeled_source_profiles"].append({**old, "profile_id": stream, "stream_id": stream})
        row.update(record_id=stream, profile_id=stream)
        row["envelope"].update(stream_id=stream, event_id="event-" + stream,
                               raw_ref="synthetic://reference-context/" + stream)
        payload["members"].append(row)

    def add(scenario, label, kind, *, day="2026-09-08", sequence=1, parent=None, changes=None, env_changes=None):
        """Append one concrete owner-shaped body and its actual source profile."""
        stream, record = f"{scenario}-{kind}", f"{scenario}-{label}"
        source = "fixture-calendar" if kind == "exchange_session" else "fixture-instrument" if kind == "instrument_tradability" else "fixture-reference" if kind == "contract_reference" else "fixture-provider"
        available = day + "T13:00:00Z"
        common = dict(source=source, provider_record_id="provider-" + record,
                      available_at=available, availability_basis="measured")
        if kind == "exchange_session":
            body = dict(**common, calendar="XNYS", session_date=day, kind="regular",
                        opens_at=day + "T13:30:00Z", closes_at=day + "T20:00:00Z",
                        source_version="1", fidelity="genuine")
        elif kind == "instrument_tradability":
            body = dict(**common, contract=deepcopy(contract), instrument_ref="opaque-K",
                        session_date=day, opens_at=day + "T13:30:00Z", closes_at=day + "T20:00:00Z",
                        effective_from=day + "T14:00:00Z", effective_until=day + "T20:00:00Z",
                        status="tradable", source_version="1", fidelity="genuine")
        elif kind == "contract_reference":
            body = dict(**common, contract=deepcopy(contract),
                        components=[dict(kind="shares", asset="SPY", quantity="100")],
                        availability_evidence_ref="synthetic://availability/" + record,
                        listed_at="2026-08-01T00:00:00Z", listing_status="listed",
                        effective_from=day + "T14:00:00Z", effective_until=None)
        else:
            body = dict(provider=source, symbol="opaque-K", contract=deepcopy(contract),
                        available_at=available, availability_basis="measured",
                        availability_evidence_ref="synthetic://availability/" + record)
        if changes:
            body.update(changes)
        if not any(p["profile_id"] == stream for p in payload["modeled_source_profiles"]):
            payload["modeled_source_profiles"].append(dict(
                profile_id=stream, kind=kind, source=source, stream_id=stream,
                feed_class=None, fidelity="genuine" if kind in KINDS[:2] else None,
                availability_basis="measured", units={},
                record_identity_rule="new_event_id_per_update" if kind == "provider_contract_mapping" else "new_provider_record_id_per_update"))
        env = dict(event_id="event-" + record, raw_ref="synthetic://reference-context/" + record,
                   simulated_received_at=available, stream_id=stream, receive_sequence=sequence,
                   supersedes_record_id=f"{scenario}-{parent}" if parent else None, contract=None, metadata=None)
        if env_changes:
            env.update(env_changes)
        member = _member(record, kind, stream, body, env)
        payload["members"].append(member)
        return member

    for kind in KINDS:
        add("joined", kind, kind, day="2026-09-05")
    add("date", "S8", "exchange_session")
    add("date", "S9", "exchange_session", day="2026-09-09", sequence=2,
        changes={"available_at": "2026-09-08T14:00:00Z"})
    for name, changes in (
        ("revision", {"kind": "early_close", "closes_at": "2026-09-08T17:00:00Z"}),
        ("unknown-session", {"kind": "unknown"}),
        ("unknown-availability", {"available_at": None}),
        ("session-conflict", {}),
    ):
        add(name, "H", "instrument_tradability")
        add(name, "S0", "exchange_session")
        add(name, "S1", "exchange_session", sequence=2,
            parent=None if name == "session-conflict" else "S0", changes=changes)
    for name, kind, changes in (
        ("status-schedule", "instrument_tradability", {"status": "halted", "effective_from": "2026-09-08T16:00:00Z", "effective_until": "2026-09-08T17:00:00Z"}),
        ("status-overlap", "instrument_tradability", {"status": "halted"}),
        ("status-unknown", "instrument_tradability", {"effective_until": None}),
        ("reference-overlap", "contract_reference", {"components": None}),
        ("reference-schedule", "contract_reference", {"effective_from": "2026-09-08T16:00:00Z"}),
    ):
        add(name, "S", "exchange_session")
        add(name, "M", "provider_contract_mapping")
        add(name, "A", kind, changes={"effective_until": "2026-09-08T16:00:00Z"} if "schedule" in name else None)
        add(name, "B", kind, sequence=2, changes=changes)
    add("null-end", "S", "exchange_session")
    add("null-end", "M", "provider_contract_mapping")
    add("null-end", "R", "contract_reference")
    add("null-end", "H", "instrument_tradability", changes={"effective_until": None})
    add("null-start", "R", "contract_reference", changes={"effective_from": None})
    for name in ("mapping-revision", "mapping-conflict", "mapping-malformed", "mapping-roots"):
        add(name, "M0", "provider_contract_mapping")
        changed = {**contract, "multiplier": True} if name == "mapping-malformed" else {**contract, "right": "put"}
        add(name, "M1", "provider_contract_mapping", sequence=2,
            parent=None if name == "mapping-roots" else "M0", changes={"contract": changed},
            env_changes={"event_id": f"event-{name}-M0"} if name == "mapping-conflict" else None)
        add(name, "R", "contract_reference")
    for field in ("symbol", "provider"):
        for malformed in ("missing", "invalid"):
            for timing in ("causal", "future"):
                name = f"mapping-target-{field}-{malformed}-{timing}"
                add(name, "A", "provider_contract_mapping")
                changes = {"contract": {**contract, "right": "put"}}
                if timing == "future":
                    changes["available_at"] = "2026-09-08T16:00:01Z"
                row = add(name, "B", "provider_contract_mapping", sequence=2, changes=changes)
                if malformed == "missing":
                    del row["raw_body"][field]
                else:
                    row["raw_body"][field] = True
    for kind in KINDS:
        for prefix in ("duplicate", "identity"):
            name = f"{prefix}-{kind}"
            a = add(name, "A", kind)
            b = add(name, "B", kind, sequence=2,
                    env_changes={"simulated_received_at": "2026-09-08T13:00:01Z", "event_id": a["envelope"]["event_id"]})
            b["raw_body"] = deepcopy(a["raw_body"])
            if prefix == "identity":
                field = "kind" if kind == "exchange_session" else "status" if kind == "instrument_tradability" else "symbol" if kind == "provider_contract_mapping" else "listing_status"
                b["raw_body"][field] = "closed" if kind == "exchange_session" else "halted" if kind == "instrument_tradability" else "other-opaque" if kind == "provider_contract_mapping" else "inactive"
    for kind in ("instrument_tradability", "contract_reference"):
        for prefix in ("future", "scheduled-revision", "malformed-revision"):
            name = f"{prefix}-{kind}"
            add(name, "A", kind)
            changes = {"available_at": "2026-09-08T16:00:01Z", "contract": {**contract, "multiplier": True}} if prefix == "future" else {"effective_from": "2026-09-08T18:00:00Z"} if prefix == "scheduled-revision" else {"contract": {**contract, "multiplier": True}}
            add(name, "B", kind, sequence=2, parent="A", changes=changes)
    add("mapping-ambiguous", "M0", "provider_contract_mapping")
    add("mapping-ambiguous", "M1", "provider_contract_mapping", sequence=2, changes={"symbol": "other-opaque"})
    add("mapping-ambiguous", "R", "contract_reference")
    add("targets-ambiguous", "M", "provider_contract_mapping")
    add("targets-ambiguous", "R0", "contract_reference", changes={"contract": {**contract, "right": "put"}})
    add("targets-ambiguous", "R1", "contract_reference", sequence=2, changes={"contract": {**contract, "strike": "660"}})
    # A future request/link cannot establish a second causal stream.
    add("future-link", "A", "contract_reference")
    add("future-link", "B", "contract_reference", sequence=2,
        changes={"available_at": "2026-09-08T16:00:01Z"},
        env_changes={"supersedes_record_id": "reference-overlap-A"})
    for kind, missing in (("instrument_tradability", "source"), ("provider_contract_mapping", "provider")):
        row = add("raw-precedence", kind, kind, changes={"contract": {**contract, "extra": 1}})
        del row["raw_body"][missing]
    add("raw-precedence", "S", "exchange_session")
    add("raw-precedence", "R", "contract_reference")
    add("malformed-status-root", "A", "instrument_tradability")
    add("malformed-status-root", "B", "instrument_tradability", sequence=2,
        changes={"contract": {**contract, "multiplier": True}})
    add("unresolved", "H", "instrument_tradability", changes={"contract": None})
    add("unresolved", "S", "exchange_session")
    for kind in KINDS:
        add("wrong-source-" + kind, "A", kind, changes={"provider" if kind == "provider_contract_mapping" else "source": "wrong"})
        add("wrong-basis-" + kind, "A", kind, changes={"availability_basis": "assumed"})
        if kind in KINDS[:2]:
            add("wrong-fidelity-" + kind, "A", kind, changes={"fidelity": "unknown"})
    for row in payload["members"]:
        row["raw_hash"] = _member(row["record_id"], row["kind"], row["profile_id"], row["raw_body"], row["envelope"])["raw_hash"]
    payload.update(fixture_id=FIXTURE_ID, generator_id="optionslab-context-reference-fixture-builder",
                   generator_version="1", assembled_at=datetime.now(timezone.utc).isoformat(),
                   generator_source_ref="OptionsLab/tests/build_context_reference_fixture.py")
    return _payload_bytes(payload)


if __name__ == "__main__":
    target = Path(__file__).parent / "fixtures" / f"{FIXTURE_ID}.json"
    target.write_bytes(build_fixture())
    print(target)
