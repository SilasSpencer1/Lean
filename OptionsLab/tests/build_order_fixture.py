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
    def order(record, **changes):
        """Append a distinct static report with its own source locator.

        :param record:  Member and provider event identity.
        :param changes: Source report field changes.
        :returns:       None.
        """
        body = {**report, "provider_event_id": "provider-" + record, **changes}
        row = add(payload, record, "order_update", body, "alpaca-orders",
                  body["available_at"] or at)
        row["envelope"]["raw_ref"] = "synthetic://p19/" + record

    for row in payload["members"]:
        row["envelope"]["raw_ref"] = "synthetic://p19/" + row["record_id"]
    order("entry-zero", raw_filled_quantity="0", cumulative_fill_notional_usd=None,
          status="accepted", pending=True, terminal=False, provider_sequence=0,
          event_at="2026-09-04T14:04:59Z", available_at="2026-09-04T14:04:59Z")
    order("same-id-conflict", provider_event_id="provider-fill-1",
          raw_filled_quantity="2", provider_sequence=2)
    order("cancel-request", broker_order_id="broker-2", client_order_id="client-2",
          raw_filled_quantity="0", cumulative_fill_notional_usd=None,
          status="cancel_requested", pending=True, terminal=False)
    order("entry-canceled", broker_order_id="broker-2", client_order_id="client-2",
          raw_filled_quantity="0", cumulative_fill_notional_usd=None,
          status="canceled", pending=False, terminal=True, provider_sequence=2)
    order("late-fill", broker_order_id="broker-2", client_order_id="client-2",
          status="filled", raw_filled_quantity="1", provider_sequence=3,
          event_at="2026-09-04T14:06:00Z", available_at="2026-09-04T14:06:00Z")
    order("new-entry-pending", broker_order_id="broker-3", client_order_id="client-3",
          raw_filled_quantity="0", cumulative_fill_notional_usd=None,
          status="accepted", pending=True, terminal=False)
    order("exit-pending", broker_order_id="broker-exit", client_order_id="client-exit",
          role="sell_exit", raw_filled_quantity="0", cumulative_fill_notional_usd=None,
          status="accepted", pending=True, terminal=False)
    order("exit-canceled", broker_order_id="broker-exit", client_order_id="client-exit",
          role="sell_exit", raw_filled_quantity="0", cumulative_fill_notional_usd=None,
          status="canceled", pending=False, terminal=True, provider_sequence=2)
    order("exit-fill", broker_order_id="broker-exit", client_order_id="client-exit",
          role="sell_exit", raw_filled_quantity="1", cumulative_fill_notional_usd="500",
          status="filled", pending=False, terminal=True, provider_sequence=3)
    order("replacement-parent", broker_order_id="broker-parent", client_order_id="client-parent")
    order("replacement-child", broker_order_id="broker-child", client_order_id="client-child",
          replaces_broker_order_id="broker-parent", replaces_client_order_id="client-parent")
    order("late-link-parent", broker_order_id="broker-late-parent",
          client_order_id="client-late-parent", event_at="2026-09-04T14:04:57Z",
          available_at="2026-09-04T14:04:57Z")
    order("late-link-child", broker_order_id="broker-late-child",
          client_order_id="client-late-child", event_at="2026-09-04T14:05:02Z",
          available_at="2026-09-04T14:05:02Z")
    order("late-link-report", broker_order_id="broker-late-child",
          client_order_id="client-late-child", replaces_broker_order_id="broker-late-parent",
          replaces_client_order_id="client-late-parent",
          event_at="2026-09-04T14:05:03Z", available_at="2026-09-04T14:05:03Z")
    order("quantity-regression", raw_filled_quantity="0", cumulative_fill_notional_usd=None,
          provider_sequence=0)
    order("sequence-low", broker_order_id="broker-seq", client_order_id="client-seq",
          provider_sequence=5)
    order("old-sequence-higher", broker_order_id="broker-seq", client_order_id="client-seq",
          raw_filled_quantity="2", provider_sequence=4)
    order("fractional-fill", broker_order_id="broker-frac", client_order_id="client-frac",
          raw_filled_quantity="0.5")
    order("unknown-fill", broker_order_id="broker-unknown", client_order_id="client-unknown",
          raw_filled_quantity=None, quantity_basis="unknown")
    order("two-fill", broker_order_id="broker-two", client_order_id="client-two",
          raw_filled_quantity="2")
    order("late-money", cumulative_fees_usd="1.25", provider_sequence=2)
    order("fee-regression", cumulative_fees_usd="0.50", provider_sequence=3)
    order("terminal-regression", status="accepted", pending=True, terminal=False,
          provider_sequence=3)
    order("no-provider-fill-a", provider_event_id=None,
          broker_order_id="broker-no-provider", client_order_id="client-no-provider")
    order("no-provider-fill-b", provider_event_id=None,
          broker_order_id="broker-no-provider", client_order_id="client-no-provider")
    order("client-reused", broker_order_id="broker-reused", client_order_id="client-1")
    order("unknown-role-fill", broker_order_id="broker-role", client_order_id="client-role",
          role="unknown")
    order("identity-zero-a", raw_filled_quantity="0", cumulative_fill_notional_usd=None,
          contract={**report["contract"], "strike": "501"}, provider_sequence=0,
          event_at="2026-09-04T14:04:58Z", available_at="2026-09-04T14:04:58Z")
    order("identity-zero-null", raw_filled_quantity="0", cumulative_fill_notional_usd=None,
          contract=None, provider_sequence=0,
          event_at="2026-09-04T14:04:58Z", available_at="2026-09-04T14:04:58Z")
    order("identity-zero-exit", raw_filled_quantity="0", cumulative_fill_notional_usd=None,
          role="sell_exit", provider_sequence=0,
          event_at="2026-09-04T14:04:58Z", available_at="2026-09-04T14:04:58Z")
    order("clock-fill", broker_order_id="broker-clock", client_order_id="client-clock")
    order("clock-zero-seq-early-time-late", broker_order_id="broker-clock",
          client_order_id="client-clock", raw_filled_quantity="0",
          cumulative_fill_notional_usd=None, provider_sequence=0,
          event_at="2026-09-04T14:05:01Z", available_at="2026-09-04T14:05:01Z")
    order("clock-zero-seq-late-time-early", broker_order_id="broker-clock",
          client_order_id="client-clock", raw_filled_quantity="0",
          cumulative_fill_notional_usd=None, provider_sequence=2,
          event_at="2026-09-04T14:04:59Z", available_at="2026-09-04T14:04:59Z")
    order("clock-zero-time-only", broker_order_id="broker-clock",
          client_order_id="client-clock", raw_filled_quantity="0",
          cumulative_fill_notional_usd=None, provider_sequence=None,
          event_at="2026-09-04T14:04:59Z", available_at="2026-09-04T14:04:59Z")
    order("clock-zero-seq-only", broker_order_id="broker-clock",
          client_order_id="client-clock", raw_filled_quantity="0",
          cumulative_fill_notional_usd=None, provider_sequence=0,
          event_at=None, available_at="2026-09-04T14:04:59Z")
    order("z-early-q1", broker_order_id="broker-between", client_order_id="client-between",
          provider_sequence=1)
    order("mid-zero", broker_order_id="broker-between", client_order_id="client-between",
          raw_filled_quantity="0", cumulative_fill_notional_usd=None,
          provider_sequence=2, event_at="2026-09-04T14:05:01Z",
          available_at="2026-09-04T14:05:01Z")
    order("a-later-q1", broker_order_id="broker-between", client_order_id="client-between",
          provider_sequence=3, event_at="2026-09-04T14:05:02Z",
          available_at="2026-09-04T14:05:02Z")
    order("unlinked-fill", broker_order_id=None, client_order_id=None)
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
