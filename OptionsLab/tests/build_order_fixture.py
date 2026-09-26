"""Build one registered static source of observed order reports."""

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path

from build_calibration_inputs_fixture import add, base, profile
from build_fixture import _payload_bytes


FOLDER = Path(__file__).parent / "fixtures"
CATALOG = Path(__file__).parent.parent / "src/options_lab/_fixture_catalog.json"
SOURCE = "p19-order-update-v1"
GENERATOR = "optionslab-order-fixture-builder"
UNITS = dict(quantity="contracts", cumulative_fill_notional="USD_total_cumulative",
             cumulative_fees="USD_total_cumulative")


def build_source() -> bytes:
    """Frame source reports and one malformed body for retained uncertainty.

    :returns: Canonical complete synthetic fixture bytes.
    """
    payload = base(SOURCE, [profile("order_update", "alpaca", "alpaca-orders", units=UNITS)],
                   GENERATOR)
    payload["generator_source_ref"] = "OptionsLab/tests/build_order_fixture.py"
    at = "2026-09-04T14:05:00Z"
    report = dict(schema_version=1, account_id="acct-1", source="alpaca",
        provider_event_id="provider-fill-1", broker_order_id="broker-1",
        client_order_id="client-1", replaces_broker_order_id=None,
        replaces_client_order_id=None, replaced_by_broker_order_id=None,
        replaced_by_client_order_id=None, decision_id="decision-1",
        contract=dict(underlying="SPY", expiry="2026-09-04", right="call",
                      strike="500", multiplier=100, deliverable_id="SPY-100"),
        role="buy_entry", event_at=at, available_at=at, provider_sequence=1,
        status="filled", pending=False, terminal=True, quantity_basis="cumulative",
        raw_filled_quantity="1", cumulative_fill_notional_usd="510",
        cumulative_fees_usd=None)
    add(payload, "fill-1", "order_update", report, "alpaca-orders", at)
    add(payload, "malformed-1", "order_update",
        {**report, "provider_event_id": "provider-malformed-1",
         "raw_filled_quantity": "NaN"}, "alpaca-orders", at)
    add(payload, "profile-mismatch-1", "order_update",
        {**report, "source": "lean", "provider_event_id": "provider-mismatch-1"},
        "alpaca-orders", at)
    return _payload_bytes(payload)


if __name__ == "__main__":
    raw = build_source()
    (FOLDER / (SOURCE + ".json")).write_bytes(raw)
    catalog = json.loads(CATALOG.read_bytes())
    catalog["fixtures"] = [row for row in catalog["fixtures"] if row["fixture_id"] != SOURCE]
    catalog["fixtures"].append(dict(fixture_id=SOURCE, generator_id=GENERATOR,
        generator_version="1", payload_schema_version=1, normalization_version=1,
        expected_payload_sha256=sha256(raw).hexdigest()))
    CATALOG.write_bytes(_payload_bytes(catalog))
    print(SOURCE, sha256(raw).hexdigest())
