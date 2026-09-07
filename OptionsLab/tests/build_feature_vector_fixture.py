"""Build one independent admitted September 4 market population for full vectors."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path

from build_fixture import _member, _metadata, _payload_bytes
from build_coherence_fixture import _quote_hash
from options_lab._input_parsing import _parse_contract_id
from options_lab.greeks import FIXTURE_GREEK_METHOD, GreekInputs, greek_input_hash

FIXTURE_ID = "p11-feature-vector-v1"
OPEN = datetime(2026, 9, 4, 13, 30, tzinfo=timezone.utc)
DECISION = OPEN + timedelta(minutes=35)


def build_fixture() -> bytes:
    """Frame actual market records and compute only their upstream semantic links.

    :returns: New readable fixture bytes with truthful assembly provenance.
    :raises ValueError: If generated source facts cannot normalize.
    """
    folder = Path(__file__).parent / "fixtures"
    prior = json.loads((folder / "p08d-reference-context-v1.json").read_bytes())
    templates = {m["kind"]: m for m in prior["members"] if m["record_id"].startswith("joined-")}
    profiles = {p["profile_id"]: p for p in prior["modeled_source_profiles"]}
    payload = {**prior, "fixture_id": FIXTURE_ID, "generator_id": "optionslab-feature-vector-fixture-builder",
               "generator_version": "1", "assembled_at": datetime.now(timezone.utc).isoformat(),
               "generator_source_ref": "OptionsLab/tests/build_feature_vector_fixture.py",
               "members": [], "modeled_source_profiles": []}
    contract = dict(underlying="SPY", expiry="2026-09-18", right="call", strike="434",
                    multiplier=100, deliverable_id="standard-spy-100")

    def add(name, kind, body, *, target=None, meta=None, stream=None, parent=None, sequence=1):
        """Use the existing framing with a genuinely distinct source stream."""
        stream = stream or name
        if not any(p["profile_id"] == stream for p in payload["modeled_source_profiles"]):
            if kind == "underlying_bar":
                profile = dict(kind=kind, source="fixture-volume-bars", feed_class="realtime", fidelity="genuine",
                               availability_basis="measured", units=dict(price="USD_per_share", volume="shares",
                               vwap_numerator="USD", vwap_denominator="shares"),
                               record_identity_rule="provider_record_id_and_revision_id")
            else:
                profile = deepcopy(profiles[templates[kind]["profile_id"]])
            payload["modeled_source_profiles"].append({**profile, "profile_id": stream, "stream_id": stream})
        at = (meta or body).get("available_at") or DECISION.isoformat()
        env = dict(event_id="event-" + name, raw_ref="synthetic://p11/" + name,
                   simulated_received_at=at, stream_id=stream, receive_sequence=sequence,
                   supersedes_record_id=parent, contract=target, metadata=meta)
        row = _member(name, kind, stream, body, env)
        payload["members"].append(row)
        return row

    session = deepcopy(templates["exchange_session"]["raw_body"])
    session.update(session_date="2026-09-04", opens_at=OPEN.isoformat(),
                   closes_at="2026-09-04T20:00:00Z", available_at="2026-09-04T13:00:00Z",
                   provider_record_id="p11-session")
    add("session", "exchange_session", session)
    for history in ("bars", "proxy-bars", "exact-bars", "source-bars", "late-bars", "equal-bars", "wrong-source-bars", "wrong-definition-bars"):
        for minute in range(1, 36):
            end = OPEN + timedelta(minutes=minute)
            meta = _metadata("fixture-volume-bars", history + "-" + str(minute))
            meta.update(kind="interval", event_at=None, available_at=end.isoformat(),
                        interval_start=(end - timedelta(minutes=1)).isoformat(), interval_end=end.isoformat())
            close = 399 + minute
            raw = dict(symbol="SPY", close_price=str(close), volume="1100",
                       vwap_numerator=str(1100 * close - (0 if history == "equal-bars" else 550)),
                       vwap_denominator="1100", price_basis="raw", volume_definition_id="synthetic-volume-v1",
                       vwap_definition_id="synthetic-vwap-v1", revision_id="r1", supersedes_revision_id=None)
            if history in ("proxy-bars", "source-bars") and minute == 1:
                raw.update(vwap_numerator=None, vwap_denominator=None, vwap_definition_id=None)
            if history == "exact-bars" and minute == 1:
                raw.update(volume=None, volume_definition_id=None)
            if history == "source-bars" and minute == 2:
                meta["source"] = "wrong-source"
            if history == "late-bars" and minute == 35:
                meta["available_at"] = (end + timedelta(microseconds=1)).isoformat()
            if history == "wrong-source-bars":
                meta["source"] = "other-volume-source"
            if history == "wrong-definition-bars" and minute == 35:
                raw["volume_definition_id"] = "other-volume-definition"
            add(f"{history}-{minute:02d}", "underlying_bar", raw, meta=meta, stream=history, sequence=minute)
    next(p for p in payload["modeled_source_profiles"] if p["profile_id"] == "wrong-source-bars")["source"] = "other-volume-source"
    old = next(m for m in payload["members"] if m["record_id"] == "bars-35")
    revised, revised_meta = deepcopy(old["raw_body"]), deepcopy(old["envelope"]["metadata"])
    revised.update(close_price="435", revision_id="r2", supersedes_revision_id="r1")
    revised_meta["available_at"] = (DECISION + timedelta(microseconds=1)).isoformat()
    add("bars-correction", "underlying_bar", revised, meta=revised_meta, stream="bars", parent="bars-35", sequence=36)

    for scenario in ("good", "warmup", "put", "spot", "stale", "missing-event", "bad-proof", "locked", "raw-precision", "future-malformed", "moneyness-bound", "source-age"):
        target = {**contract, "right": "put", "strike": "430"} if scenario == "put" else deepcopy(contract)
        if scenario == "moneyness-bound":
            target["strike"] = "434." + "0" * 90 + "1"
        decision = OPEN + timedelta(minutes=30) if scenario == "warmup" else DECISION
        event = decision - timedelta(seconds=6 if scenario == "stale" else 2)
        quote_members = []
        for kind in ("option_quote", "underlying_quote"):
            name = scenario + "-" + kind
            body = deepcopy(templates[kind]["raw_body"])
            body.update(bid_at=event.isoformat(), ask_at=event.isoformat())
            body.update(bid="4.90", ask="5.10") if kind == "option_quote" else body.update(bid="433.99", ask="434.01")
            if scenario == "spot" and kind == "underlying_quote":
                body.update(bid="434.99", ask="435.01")
            if scenario == "locked" and kind == "underlying_quote":
                body.update(bid="434", ask="434")
            if scenario == "raw-precision" and kind == "option_quote":
                body["bid"] = "4.90000000000000000000000000000000000001"
            meta = deepcopy(templates[kind]["envelope"]["metadata"])
            meta.update(provider_record_id=name, event_at=event.isoformat(),
                        available_at=(decision - timedelta(seconds=1)).isoformat())
            if scenario == "source-age" and kind == "option_quote":
                meta["event_at"] = (decision - timedelta(seconds=3)).isoformat()
            if scenario == "missing-event" and kind == "option_quote":
                meta["event_at"] = None
            quote_members.append(add(name, kind, body, target=target if kind == "option_quote" else None, meta=meta))
        option_hash, underlying_hash = (_quote_hash(row) for row in quote_members)
        body = deepcopy(templates["greek_observation"]["raw_body"])
        body.update(delta="-0.50" if scenario == "put" else "0.50", iv="0.20", as_of=event.isoformat(),
                    available_at=(decision - timedelta(seconds=1)).isoformat(), provider_record_id=scenario + "-greek")
        body["inputs"].update(option_quote_hash=option_hash, underlying_quote_hash=underlying_hash)
        inputs = GreekInputs(option_hash, underlying_hash, Decimal(body["inputs"]["rate"]),
                             Decimal(body["inputs"]["dividend_yield"]), body["inputs"]["rate_unit"],
                             body["inputs"]["dividend_unit"], body["inputs"]["assumptions_id"])
        body["input_hash"] = greek_input_hash(inputs, contract=_parse_contract_id(target),
                                             method=FIXTURE_GREEK_METHOD, as_of=event)
        add(scenario + "-greek", "greek_observation", body, target=target)
        body = deepcopy(templates["quote_coherence"]["raw_body"])
        body.update(contract=target, evidence_id=scenario + "-proof", option_quote_hash=option_hash,
                    underlying_quote_hash=underlying_hash, coherent_at=event.isoformat(),
                    available_at=(decision - timedelta(seconds=1)).isoformat(), snapshot_id=scenario + "-joint")
        if scenario == "bad-proof":
            body["coherent_at"] = (event + timedelta(microseconds=1)).isoformat()
        add(scenario + "-proof", "quote_coherence", body)
        for kind in ("instrument_tradability", "provider_contract_mapping", "contract_reference"):
            body = deepcopy(templates[kind]["raw_body"])
            body.update(contract=target, available_at="2026-09-04T13:00:00Z")
            if "provider_record_id" in body:
                body["provider_record_id"] = scenario + "-" + kind
            if kind != "provider_contract_mapping":
                body.update(effective_from=OPEN.isoformat())
                if kind == "instrument_tradability":
                    body.update(session_date="2026-09-04", opens_at=OPEN.isoformat(),
                                closes_at="2026-09-04T20:00:00Z", effective_until="2026-09-04T20:00:00Z")
            add(scenario + "-" + kind, kind, body)
    for name, kind, changes in (
        ("short-hours", "instrument_tradability", dict(closes_at="2026-09-04T19:00:00Z")),
        ("fractional-hours", "instrument_tradability", dict(closes_at="2026-09-04T19:00:00.000001Z")),
        ("future-unknown-hours", "instrument_tradability", dict(effective_from="2026-09-04T18:00:00Z", effective_until=None)),
        ("overlap-halt", "instrument_tradability", dict(status="halted")),
        ("unknown-hours", "instrument_tradability", dict(effective_until=None)),
        ("scheduled-halt", "instrument_tradability", dict(status="halted", effective_from="2026-09-04T18:00:00Z")),
        ("overlap-reference", "contract_reference", dict(components=None)),
        ("scheduled-reference", "contract_reference", dict(effective_from="2026-09-04T18:00:00Z")),
    ):
        body = deepcopy(next(m["raw_body"] for m in payload["members"] if m["record_id"] == "good-" + kind))
        body.update(changes, provider_record_id=name)
        add(name, kind, body)
    tick = deepcopy(templates["tick_rule"]["raw_body"])
    tick.update(contract=contract, available_at="2026-09-04T14:05:00Z", effective_from="2026-09-04T18:00:00Z", effective_until="2026-09-04T20:00:00Z")
    add("optional-tick", "tick_rule", tick)
    malformed = deepcopy(next(m for m in payload["members"] if m["record_id"] == "future-malformed-instrument_tradability"))
    malformed["raw_body"].update(available_at="2026-09-04T14:06:00Z", contract={**contract, "multiplier": True})
    add("aaa-future-malformed", "instrument_tradability", malformed["raw_body"])
    return _payload_bytes(payload)


if __name__ == "__main__":
    target = Path(__file__).parent / "fixtures" / (FIXTURE_ID + ".json")
    target.write_bytes(build_fixture())
    print(target)
