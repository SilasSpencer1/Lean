"""Build registered current account and held-mark records for exit clocks."""

from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path

from build_calibration_inputs_fixture import admitted, add as add_bundle_member, base, profile, reference, runtime_claim
from build_fixture import _member, _payload_bytes
from options_lab.bundle_inputs import normalize_model_bytes
from options_lab.bundle_manifest_inputs import normalize_bundle_manifest
from options_lab.bundle_validation import BUNDLE_ASSEMBLY_METHOD_ID, _checked_inputs
from options_lab.config import StrategyConfig
from options_lab.runtime import measure_runtime


FOLDER = Path(__file__).parent / "fixtures"
CATALOG = Path(__file__).parent.parent / "src/options_lab/_fixture_catalog.json"
FIXTURE_ID = "p18-exit-source-v1"
CASH_BUNDLE_ID = "p18-exit-cash-bundle-v1"
VALIDATION_ID = "p18-exit-validation-v1"


def build_fixture() -> bytes:
    """Frame independent source occurrences from actual P12 owners.

    :returns:         Canonical registered synthetic source bytes.
    :raises   OSError: If the retained P12 source is unavailable.
    """
    original = json.loads((FOLDER / "p12c-account-context-v1.json").read_bytes())
    templates = {row["record_id"]: row for row in original["members"]}
    profile_map = {row["profile_id"]: row for row in original["modeled_source_profiles"]}
    baseline = ("session", "good-instrument_tradability", "good-option_quote")
    rows = [deepcopy(templates[name]) for name in baseline]
    profiles = [deepcopy(profile_map[templates[name]["profile_id"]]) for name in baseline]

    def add(name, template, body, envelope):
        """Add one source member with a unique declared stream.

        :param    name:     Unique record and profile identity.
        :param    template: Actual existing owner member.
        :param    body:     Literal scenario body.
        :param    envelope: Literal scenario envelope.
        :returns:          Framed member with correct raw hash.
        """
        profiles.append({**deepcopy(profile_map[template["profile_id"]]),
            "profile_id": name, "stream_id": name})
        envelope.update(event_id="event-" + name, stream_id=name,
            raw_ref="synthetic://p18/" + name, receive_sequence=1,
            supersedes_record_id=None)
        row = _member(name, template["kind"], name, body, envelope)
        rows.append(row)
        return row

    def scenario(name, instant, *, filled="2026-09-04T14:05:00Z", quote=True,
                 account_changes=None, holding_changes=None, order=None,
                 bid="4.90", quote_time=None):
        """Add one current account with its exact selected nested mark.

        :param    name:            Scenario identity.
        :param    instant:         Current account and decision timestamp.
        :param    filled:          Actual entry fill timestamp, or None.
        :param    quote:           Whether a nested/independent mark is present.
        :param    account_changes: Literal account overrides.
        :param    holding_changes: Literal holding overrides.
        :param    order:           Optional observed order record.
        :param    bid:             Observed bid, or None.
        :param    quote_time:      Actual mark event time, or current instant.
        :returns:                  None.
        """
        source_mark = None
        if quote:
            template = templates["good-option_quote"]
            body, env = deepcopy(template["raw_body"]), deepcopy(template["envelope"])
            at = quote_time or instant
            body.update(bid=bid, bid_size=7 if bid is not None else None, bid_at=at)
            env["metadata"].update(event_at=at, available_at=instant,
                provider_record_id=name + "-mark")
            env["simulated_received_at"] = instant
            source_mark = add(name + "-mark", template, body, env)
        template = templates["occupied"]
        body, env = deepcopy(template["raw_body"]), deepcopy(template["envelope"])
        body.update(account_id="exit-account", source="fixture-account-ledger",
            provider_record_id="report-" + name, available_at=instant, as_of=instant,
            reconciled_at=instant, ledger_revision="ledger-" + name,
            reconciled_ledger_revision="ledger-" + name,
            reconciliation_id="reconcile-" + name)
        held = body["holdings"][0]
        held.update(entry_filled_at=filled,
            mark_quote=None if source_mark is None else
            {k: deepcopy(source_mark[k]) for k in ("raw_body", "envelope")})
        held.update(holding_changes or {})
        body.update(account_changes or {})
        if order is not None:
            body["open_orders"] = [order]
        env["simulated_received_at"] = instant
        add(name + "-account", template, body, env)

    for name, instant in (
        ("horizon-before", "2026-09-04T14:34:59Z"),
        ("horizon-at", "2026-09-04T14:35:00Z"),
        ("liquidation-before", "2026-09-04T19:34:59Z"),
        ("liquidation-at", "2026-09-04T19:35:00Z"),
        ("escalation-at", "2026-09-04T19:39:00Z"),
        ("deadline-at", "2026-09-04T19:40:00Z"),
        ("early-at", "2026-09-04T17:35:00Z"),
    ):
        scenario(name, instant, filled="2026-09-04T14:05:00Z"
            if name.startswith("horizon") else "2026-09-04T19:20:00Z"
            if name.startswith(("liquidation", "escalation", "deadline")) else
            "2026-09-04T17:20:00Z")
    session = templates["session"]
    body, env = deepcopy(session["raw_body"]), deepcopy(session["envelope"])
    body.update(kind="early_close", closes_at="2026-09-04T18:00:00Z",
        provider_record_id="early-session")
    add("early-session", session, body, env)
    instrument = templates["good-instrument_tradability"]
    body, env = deepcopy(instrument["raw_body"]), deepcopy(instrument["envelope"])
    body.update(closes_at="2026-09-04T18:00:00Z",
        effective_until="2026-09-04T18:00:00Z", provider_record_id="early-instrument")
    add("early-instrument", instrument, body, env)

    pending = dict(order_ref="exit-1", client_order_id="exit-client-1",
        instrument_ref="opaque-K", contract=deepcopy(templates["good-option_quote"]["envelope"]["contract"]),
        side="sell", role="exit", status="open", remaining_quantity="1",
        cumulative_filled_quantity="0", reserved_cash="0", execution_uncertain=False,
        source="fixture-orders", provider_record_id="exit-order-1",
        raw_ref="synthetic://p18/exit-order-1")
    scenario("pending-deadline", "2026-09-04T19:40:00Z",
        filled="2026-09-04T19:20:00Z", order=pending)
    scenario("pending-horizon", "2026-09-04T14:35:00Z", order=pending)
    scenario("unknown-fill", "2026-09-04T14:05:00Z", filled=None)
    scenario("prior-session", "2026-09-04T14:05:00Z",
        filled="2026-09-03T19:00:00Z")
    scenario("prior-local-day", "2026-09-04T14:05:00Z",
        filled="2026-09-04T00:30:00Z")
    scenario("quantity-two", "2026-09-04T14:05:00Z",
        holding_changes={"quantity": "2"})
    scenario("quantity-unknown", "2026-09-04T14:05:00Z",
        holding_changes={"quantity": None})
    scenario("stale-bid", "2026-09-04T14:05:00Z",
        quote_time="2026-09-04T14:04:50Z")
    scenario("missing-bid", "2026-09-04T14:05:00Z", bid=None)
    scenario("disconnected", "2026-09-04T14:05:00Z",
        account_changes={"connection": "disconnected"})
    scenario("stale-account", "2026-09-04T14:05:00Z",
        account_changes={"as_of": "2026-09-04T14:04:50Z"})
    scenario("daily-loss", "2026-09-04T14:05:00Z",
        account_changes={"session_realized_pnl": "-1000", "virtual_equity": "99000"})
    scenario("empty-but-entry", "2026-09-04T14:05:00Z",
        account_changes={"holdings": []}, order={**pending, "side": "buy", "role": "entry"})
    scenario("empty-entry-deadline", "2026-09-04T19:40:00Z",
        account_changes={"holdings": []}, order={**pending, "side": "buy", "role": "entry"})
    body, env = deepcopy(session["raw_body"]), deepcopy(session["envelope"])
    body.update(session_date="2026-09-03", opens_at="2026-09-03T13:30:00Z",
        closes_at="2026-09-03T20:00:00Z", available_at="2026-09-03T13:00:00Z",
        provider_record_id="wrong-day-session")
    add("wrong-day-session", session, body, env)
    original.update(fixture_id=FIXTURE_ID, generator_id="optionslab-exit-fixture-builder",
        generator_version="1", generator_source_ref="OptionsLab/tests/build_exit_fixture.py",
        assembled_at=datetime.now(timezone.utc).isoformat(), members=rows,
        modeled_source_profiles=profiles)
    return _payload_bytes(original)


def build_validation() -> bytes:
    """Record the actual current assembly check for the existing cash model.

    :returns:         One registered B2 validation source member.
    :raises   AssertionError: If model or runtime owners reject their inputs.
    """
    old = admitted("p15-decision-bundle-v1")
    body = next(member for member in old.members if member.record_id == "cash").decode_raw_body()
    model = normalize_model_bytes(body["model_utf8"].encode(), event_id="p18-model",
        raw_ref="synthetic://p18-model", received_at=datetime.now(timezone.utc)).value
    runtime = measure_runtime().value
    assert model is not None and runtime is not None
    fields, _ = _checked_inputs(model, None, None, None, None,
        StrategyConfig(), runtime, ())
    performed = datetime.now(timezone.utc)
    payload = base(VALIDATION_ID, [profile("bundle_validation",
        "optionslab-bundle-validation-fixture-builder", "reports")],
        "optionslab-bundle-validation-fixture-builder")
    payload["generator_source_ref"] = "OptionsLab/tests/build_exit_fixture.py"
    add_bundle_member(payload, "cash-validation", "bundle_validation", dict(
        schema_version=1, validation_id="cash-validation",
        method_id=BUNDLE_ASSEMBLY_METHOD_ID, performed_at=performed.isoformat(), **fields),
        "reports", performed.isoformat())
    payload["assembled_at"] = datetime.now(timezone.utc).isoformat()
    return _payload_bytes(payload)


def build_cash_bundle() -> bytes:
    """Bind the existing healthy cash model to this actual exit source.

    :returns:         Registered B2 cash bundle bytes.
    :raises   AssertionError: If its actual manifest fails normalization.
    """
    old, source, reports = (admitted(name) for name in
        ("p15-decision-bundle-v1", FIXTURE_ID, VALIDATION_ID))
    body = next(member for member in old.members if member.record_id == "cash").decode_raw_body()
    manifest = deepcopy(body["manifest"])
    manifest.update(fixture_id=CASH_BUNDLE_ID, bundle_record_id="cash",
        runtime_binding=runtime_claim(), validation_report=reference(reports,
            next(member for member in reports.members if member.record_id == "cash-validation")))
    manifest["provenance"]["built_at"] = datetime.now(timezone.utc).isoformat()
    manifest["data_manifest_hashes"] = [row for row in manifest["data_manifest_hashes"]
        if row["fixture_id"] != "p15-decision-source-v1"]
    manifest["data_manifest_hashes"].append(dict(role="source", fixture_id=FIXTURE_ID,
        payload_sha256=source.payload_sha256))
    manifest["data_manifest_hashes"].append(dict(role="source", fixture_id=VALIDATION_ID,
        payload_sha256=reports.payload_sha256))
    checked = normalize_bundle_manifest(manifest, event_id="p18-build",
        raw_ref="synthetic://p18-build", received_at=datetime.now(timezone.utc))
    assert checked.value is not None, checked.rejection
    payload = base(CASH_BUNDLE_ID, [profile("model_bundle", "optionslab-bundle-fixture-builder",
        "model-bundle")], "optionslab-bundle-fixture-builder")
    payload["generator_source_ref"] = "OptionsLab/tests/build_exit_fixture.py"
    add_bundle_member(payload, "cash", "model_bundle", dict(schema_version=1,
        manifest=checked.value.snapshot(), model_utf8=body["model_utf8"]),
        "model-bundle", "2026-09-01T01:00:00Z")
    return _payload_bytes(payload)


if __name__ == "__main__":
    import sys
    stage = sys.argv[1] if len(sys.argv) > 1 else "source"
    if stage not in ("source", "validation", "bundle"):
        raise ValueError("stage must be source, validation or bundle")
    name, generator, raw = {
        "source": (FIXTURE_ID, "optionslab-exit-fixture-builder", build_fixture),
        "validation": (VALIDATION_ID, "optionslab-bundle-validation-fixture-builder", build_validation),
        "bundle": (CASH_BUNDLE_ID, "optionslab-bundle-fixture-builder", build_cash_bundle),
    }[stage]
    raw = raw()
    (FOLDER / (name + ".json")).write_bytes(raw)
    catalog = json.loads(CATALOG.read_bytes())
    catalog["fixtures"] = [row for row in catalog["fixtures"] if row["fixture_id"] != name]
    catalog["fixtures"].append(dict(fixture_id=name, generator_id=generator,
        generator_version="1", payload_schema_version=1, normalization_version=1,
        expected_payload_sha256=sha256(raw).hexdigest()))
    CATALOG.write_bytes(_payload_bytes(catalog))
