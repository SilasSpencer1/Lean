"""Frame current entry source facts and a bundle that declares their profiles."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path

from build_calibration_inputs_fixture import admitted, add as add_bundle_member, base, profile, runtime_claim
from build_candidate_selection_fixture import _quote_hash
from build_fixture import _member, _payload_bytes
from options_lab._input_parsing import _parse_contract_id
from options_lab.bundle_manifest_inputs import normalize_bundle_manifest
from options_lab.greeks import FIXTURE_GREEK_METHOD, GreekInputs, greek_input_hash


FOLDER = Path(__file__).parent / "fixtures"
CATALOG = Path(__file__).parent.parent / "src/options_lab/_fixture_catalog.json"
SOURCE = "p17b-entry-source-v1"
BUNDLE = "p17b-entry-bundle-v1"


def build_source() -> bytes:
    """Add real later quote, Greek, coherence and account occurrences.

    :returns:         Canonical registered source bytes.
    :raises   OSError: If the immutable P15 source is missing.
    """
    source = json.loads((FOLDER / "p15-decision-source-v1.json").read_bytes())
    templates = {row["record_id"]: row for row in source["members"]}
    profiles = {row["profile_id"]: row for row in source["modeled_source_profiles"]}
    source.update(fixture_id=SOURCE, generator_id="optionslab-entry-fixture-builder",
        generator_version="1", generator_source_ref="OptionsLab/tests/build_entry_fixture.py",
        assembled_at=datetime.now(timezone.utc).isoformat())
    rows = source["members"]

    def add(name, template, body, envelope):
        """Frame one unique source occurrence and its declared profile.

        :param    name:     New record/profile/stream identity.
        :param    template: Actual P15 source member.
        :param    body:     Changed owner raw body.
        :param    envelope: Changed source envelope.
        :returns:          Newly framed member row.
        """
        source["modeled_source_profiles"].append({**deepcopy(profiles[template["profile_id"]]),
            "profile_id": name, "stream_id": name})
        envelope.update(event_id="event-" + name, raw_ref="synthetic://p17b/" + name,
                        stream_id=name, receive_sequence=1, supersedes_record_id=None)
        row = _member(name, template["kind"], name, body, envelope)
        rows.append(row)
        return row

    account = templates["account_snapshot"]
    risk_source = json.loads((FOLDER / "p16b-risk-state-v1.json").read_bytes())
    pending = deepcopy(next(row["raw_body"]["open_orders"][0]
        for row in risk_source["members"] if row["record_id"] == "order1-zero"))
    pending["contract"] = deepcopy(templates["c99-option_quote"]["envelope"]["contract"])
    pending["client_order_id"] = "existing-entry"
    for name, changes, clock in (("riskref-refresh", dict(risk_state_revision="risk-new",
                                                   halt_checkpoint_ref="halt-new"), "01"),
                          ("material-change", dict(virtual_cash="149000",
                                                   virtual_equity="149000"), "01"),
                          ("disconnected", dict(connection="disconnected"), "01"),
                          ("pending-order", dict(open_orders=[pending]), "01"),
                          ("late-flat", {}, "06")):
        body, envelope = deepcopy(account["raw_body"]), deepcopy(account["envelope"])
        instant = "2026-09-04T14:05:" + clock + "Z"
        body.update(available_at=instant, as_of=instant,
                    reconciled_at=instant, provider_record_id=name,
                    **changes)
        envelope["simulated_received_at"] = instant
        add(name + "-account_snapshot", account, body, envelope)

    for number in (1, 2, 3):
        for filled, suffix in ((0, "zero"), (1, "one")):
            name = f"entry-order{number}-{suffix}"
            at = datetime(2026, 9, 4, 14, 5, tzinfo=timezone.utc) + timedelta(
                seconds=(number - 1) * 2 + filled,
                microseconds=100000 if number == 1 and not filled else 0)
            instant = at.isoformat().replace("+00:00", "Z")
            order = {**deepcopy(pending), "order_ref": f"entry-order-{number}",
                     "client_order_id": f"prior-entry-{number}",
                     "provider_record_id": f"entry-order-report-{number}",
                     "raw_ref": f"synthetic://p17b/entry-order-{number}",
                     "status": "terminal" if filled else "open",
                     "remaining_quantity": "0" if filled else "1",
                     "cumulative_filled_quantity": str(filled)}
            body, envelope = deepcopy(account["raw_body"]), deepcopy(account["envelope"])
            body.update(available_at=instant, as_of=instant, reconciled_at=instant,
                        provider_record_id=name, ledger_revision="ledger-" + name,
                        reconciled_ledger_revision="ledger-" + name,
                        reconciliation_id="reconciliation-" + name,
                        open_orders=[order])
            envelope["simulated_received_at"] = instant
            add(name + "-account_snapshot", account, body, envelope)

    outside_at = "2026-09-04T19:00:00.000001Z"
    body, envelope = deepcopy(account["raw_body"]), deepcopy(account["envelope"])
    body.update(available_at=outside_at, as_of=outside_at,
                reconciled_at=outside_at, provider_record_id="outside-flat")
    envelope["simulated_received_at"] = outside_at
    add("outside-flat-account_snapshot", account, body, envelope)

    for name, changes in (("prior-loss", dict(session_realized_pnl="-1500",
                                               virtual_cash="148500", virtual_equity="148500",
                                               virtual_settled_cash="148500",
                                               broker_settled_cash="148500",
                                               broker_available_cash="148500",
                                               broker_nonmargin_buying_power="148500")),
                          ("prior-peak", dict(high_water_mark="160000",
                                               virtual_cash="160000", virtual_equity="160000",
                                               virtual_settled_cash="160000",
                                               broker_settled_cash="160000",
                                               broker_available_cash="160000",
                                               broker_nonmargin_buying_power="160000"))):
        body, envelope = deepcopy(account["raw_body"]), deepcopy(account["envelope"])
        body.update(available_at="2026-09-04T14:04:59Z", as_of="2026-09-04T14:04:59Z",
                    reconciled_at="2026-09-04T14:04:59Z", provider_record_id=name,
                    ledger_revision="ledger-" + name,
                    reconciled_ledger_revision="ledger-" + name, **changes)
        envelope["simulated_received_at"] = "2026-09-04T14:04:59Z"
        add(name + "-account_snapshot", account, body, envelope)

    original_quote = templates["c99-option_quote"]
    original_greek = templates["c99-greek_observation"]
    original_proof = templates["c99-quote_coherence"]
    underlying_hash = _quote_hash(templates["underlying_quote"])
    underlying = templates["underlying_quote"]
    underlying_hashes = {}
    for name, instant in (("fresh", "2026-09-04T14:05:04Z"), ("outside", outside_at)):
        later_body = deepcopy(underlying["raw_body"])
        later_body.update(bid_at=instant, ask_at=instant)
        later_envelope = deepcopy(underlying["envelope"])
        later_envelope["metadata"].update(event_at=instant,
            available_at=instant, provider_record_id="entry-" + name + "-underlying")
        later_envelope["simulated_received_at"] = instant
        later = add("entry-" + name + "-underlying_quote", underlying,
                    later_body, later_envelope)
        underlying_hashes[name] = _quote_hash(later)
    contract = _parse_contract_id(original_quote["envelope"]["contract"])
    for name, ask, bid, instant, available in (
            ("worse", "5.11", "4.90", "2026-09-04T14:05:00Z", "2026-09-04T14:05:01Z"),
            ("wide", "5.10", "4.80", "2026-09-04T14:05:00Z", "2026-09-04T14:05:01Z"),
            ("fresh", "5.10", "4.90", "2026-09-04T14:05:04Z", "2026-09-04T14:05:04Z"),
            ("outside", "5.10", "4.90", outside_at, outside_at)):
        quote_body, quote_envelope = deepcopy(original_quote["raw_body"]), deepcopy(original_quote["envelope"])
        quote_body.update(ask=ask, bid=bid, ask_at=instant, bid_at=instant)
        quote_envelope["metadata"].update(event_at=instant, available_at=available,
                                           provider_record_id="entry-" + name + "-option")
        quote_envelope["simulated_received_at"] = available
        label = "entry-" + name
        quote = add(label + "-option_quote", original_quote, quote_body, quote_envelope)
        quote_hash = _quote_hash(quote)
        actual_underlying_hash = underlying_hashes.get(name, underlying_hash)

        greek_body, greek_envelope = deepcopy(original_greek["raw_body"]), deepcopy(original_greek["envelope"])
        greek_body.update(as_of=instant, available_at=available,
                          provider_record_id="entry-" + name + "-greek")
        greek_body["inputs"].update(option_quote_hash=quote_hash,
                                    underlying_quote_hash=actual_underlying_hash)
        inputs = greek_body["inputs"]
        greek_body["input_hash"] = greek_input_hash(GreekInputs(quote_hash, actual_underlying_hash,
            Decimal(inputs["rate"]), Decimal(inputs["dividend_yield"]),
            inputs["rate_unit"], inputs["dividend_unit"], inputs["assumptions_id"]),
            contract=contract, method=FIXTURE_GREEK_METHOD,
            as_of=datetime.fromisoformat(instant.replace("Z", "+00:00")))
        greek_envelope["simulated_received_at"] = available
        add(label + "-greek_observation", original_greek, greek_body, greek_envelope)

        proof_body, proof_envelope = deepcopy(original_proof["raw_body"]), deepcopy(original_proof["envelope"])
        proof_body.update(available_at=available, coherent_at=instant,
            option_quote_hash=quote_hash, underlying_quote_hash=actual_underlying_hash,
            evidence_id="entry-" + name + "-proof", snapshot_id="entry-" + name + "-joint")
        proof_envelope["simulated_received_at"] = available
        add(label + "-quote_coherence", original_proof, proof_body, proof_envelope)

    source["members"] = [_member(row["record_id"], row["kind"], row["profile_id"],
                                  row["raw_body"], row["envelope"]) for row in rows]
    return _payload_bytes(source)


def build_bundle() -> bytes:
    """Bind the actual new source and consumed profiles to the fixed call.

    :returns:         Canonical registered B2 bundle fixture bytes.
    :raises   AssertionError: If the derived model manifest cannot normalize.
    """
    old, entry_source = admitted("p15-decision-bundle-v1"), admitted(SOURCE)
    body = next(member for member in old.members if member.record_id == "call").decode_raw_body()
    manifest = deepcopy(body["manifest"])
    manifest.update(fixture_id=BUNDLE, bundle_record_id="call",
                    runtime_binding=runtime_claim())
    manifest["provenance"]["built_at"] = datetime.now(timezone.utc).isoformat()
    manifest["data_manifest_hashes"] = [row for row in manifest["data_manifest_hashes"]
        if row["fixture_id"] != "p15-decision-source-v1"]
    manifest["data_manifest_hashes"].append(dict(role="source", fixture_id=SOURCE,
        payload_sha256=entry_source.payload_sha256))
    manifest["feature_binding"]["source_profiles"] = [row for row in
        manifest["feature_binding"]["source_profiles"]
        if row["fixture_id"] not in ("p15-decision-source-v1", "p11-feature-vector-v1")]
    manifest["feature_binding"]["source_profiles"].extend(
        dict(fixture_id=SOURCE, profile_id=row["profile_id"])
        for row in entry_source.decode_modeled_source_profiles()
        if row["profile_id"] in ("session", "underlying_quote", "account_snapshot", "bars",
            "riskref-refresh-account_snapshot", "material-change-account_snapshot",
            "disconnected-account_snapshot", "pending-order-account_snapshot",
            "late-flat-account_snapshot", "outside-flat-account_snapshot")
        or row["profile_id"].startswith(("c99-", "c100-", "p99-", "pdelta-",
                                          "entry-worse-", "entry-wide-", "entry-fresh-",
                                          "entry-outside-", "entry-order")))
    checked = normalize_bundle_manifest(manifest, event_id="p17b-build",
        raw_ref="synthetic://p17b-build", received_at=datetime.now(timezone.utc))
    assert checked.value is not None, checked.rejection
    payload = base(BUNDLE, [profile("model_bundle", "optionslab-bundle-fixture-builder",
                                     "model-bundle")], "optionslab-bundle-fixture-builder")
    payload["generator_source_ref"] = "OptionsLab/tests/build_entry_fixture.py"
    add_bundle_member(payload, "call", "model_bundle", dict(schema_version=1,
        manifest=checked.value.snapshot(), model_utf8=body["model_utf8"]),
        "model-bundle", "2026-09-01T01:00:00Z")
    payload["assembled_at"] = datetime.now(timezone.utc).isoformat()
    return _payload_bytes(payload)


if __name__ == "__main__":
    import sys
    stage = sys.argv[1] if len(sys.argv) > 1 else "source"
    name = SOURCE if stage == "source" else BUNDLE
    raw = build_source() if stage == "source" else build_bundle()
    (FOLDER / (name + ".json")).write_bytes(raw)
    catalog = json.loads(CATALOG.read_bytes())
    catalog["fixtures"] = [row for row in catalog["fixtures"] if row["fixture_id"] != name]
    catalog["fixtures"].append(dict(fixture_id=name,
        generator_id=("optionslab-entry-fixture-builder" if stage == "source"
                      else "optionslab-bundle-fixture-builder"), generator_version="1",
        payload_schema_version=1, normalization_version=1,
        expected_payload_sha256=sha256(raw).hexdigest()))
    CATALOG.write_bytes(_payload_bytes(catalog))
    print(name, sha256(raw).hexdigest())
