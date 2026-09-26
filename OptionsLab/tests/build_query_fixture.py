"""Build one registered synthetic atomic account-query source."""

from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path

from build_fixture import _member, _payload_bytes


FOLDER = Path(__file__).parent / "fixtures"
CATALOG = Path(__file__).parent.parent / "src/options_lab/_fixture_catalog.json"
SOURCE = "p21-query-v1"
GENERATOR = "optionslab-query-fixture-builder"
CAPTURE = "2026-09-04T14:05:00Z"
RESPONSE = "2026-09-04T14:05:01Z"
AVAILABLE = "2026-09-04T14:05:02Z"


def build_source() -> bytes:
    """Build actual query, account, and order members in one fixture.

    :returns: Complete canonical synthetic fixture bytes.
    :raises OSError: If the existing account template is unavailable.
    """
    template = json.loads((FOLDER / "p12c-account-context-v1.json").read_bytes())
    flat = next(row for row in template["members"] if row["record_id"] == "flat")
    account_units = next(row["units"] for row in template["modeled_source_profiles"]
                         if row["kind"] == "account_snapshot")
    def profile(kind, stream, units):
        """Build one exact modeled source profile.

        :param kind: Member kind.
        :param stream: Exact profile and stream ID.
        :param units: Existing unit contract for this kind.
        :returns: Source profile dictionary.
        """
        return dict(profile_id=stream, kind=kind, source="alpaca", stream_id=stream,
            feed_class=None, fidelity=None, availability_basis="measured",
            units=units, record_identity_rule="new_event_id_per_update")
    payload = dict(schema_version=1, normalization_version=1, fixture_id=SOURCE,
        generator_id=GENERATOR, generator_version="1",
        assembled_at=datetime.now(timezone.utc).isoformat(),
        generator_source_ref="OptionsLab/tests/build_query_fixture.py",
        origin="synthetic", permitted_use="core_fixture",
        modeled_source_profiles=[
            profile("broker_query", "alpaca-query-atomic-v1", {}),
            profile("account_snapshot", "alpaca-account", account_units),
            profile("order_update", "alpaca-orders", dict(quantity="contracts",
                cumulative_fill_notional="USD_total_cumulative",
                cumulative_fees="USD_total_cumulative")),
        ], definitions=template["definitions"], members=[])

    def add(record, kind, stream, body, received=AVAILABLE):
        """Add one independently hashed member and source envelope.

        :param record: Unique member ID.
        :param kind: Member kind.
        :param stream: Profile and stream ID.
        :param body: Complete raw source body.
        :param received: Source receipt timestamp.
        :returns: None.
        """
        envelope = dict(event_id="event-" + record, raw_ref="synthetic://p21/" + record,
            simulated_received_at=received, stream_id=stream, receive_sequence=None,
            supersedes_record_id=None, contract=None, metadata=None)
        payload["members"].append(_member(record, kind, stream, body, envelope))

    def account(record, **changes):
        """Add an account body with independently declared coverage.

        :param record: Unique member ID.
        :param changes: Source account fields to override.
        :returns: None.
        """
        body = deepcopy(flat["raw_body"])
        body.update(account_id="acct-1", source="alpaca", provider_record_id=record,
            available_at=AVAILABLE, as_of=CAPTURE, reconciled_at=CAPTURE,
            ledger_revision=record, reconciled_ledger_revision=record,
            reconciliation_id=record, holdings=[], open_orders=[])
        body.update(changes)
        add(record, "account_snapshot", "alpaca-account", body)

    def query(record, account_record, **changes):
        """Add one fixed-protocol request/capture/response claim.

        :param record: Unique query member ID.
        :param account_record: Exact same-fixture account member ID.
        :param changes: Source query fields to override.
        :returns: None.
        """
        body = dict(schema_version=1, account_id="acct-1", source="alpaca",
            request_id="request-" + record, response_id="response-" + record,
            requested_at=CAPTURE, captured_at=CAPTURE, responded_at=RESPONSE,
            available_at=AVAILABLE, completed=True, account_record_id=account_record,
            holdings_coverage="complete", orders_coverage="complete",
            included_order_record_ids=[], issued_actions=[], order_ref_aliases=[])
        body.update(changes)
        add(record, "broker_query", "alpaca-query-atomic-v1", body)

    account("account-flat")
    query("fresh-flat", "account-flat")
    account("account-partial", holdings_completeness="incomplete")
    query("partial-holdings", "account-partial", holdings_coverage="partial")
    query("missing-account", "absent")
    query("wrong-account", "account-flat", account_id="other")
    query("noncompleted", "account-flat", completed=False)
    query("unresolved-send", "account-flat", issued_actions=[dict(
        action_id="send-1", kind="submit", issued_at=CAPTURE,
        resolution="unresolved", broker_order_id=None, client_order_id="client-1")])
    query("malformed-clock", "account-flat", requested_at="2026-09-04T14:05:01Z")

    contract = dict(underlying="SPY", expiry="2026-09-04", right="call",
        strike="500", multiplier=100, deliverable_id="SPY-100")
    order = dict(order_ref="order-1", client_order_id="client-1",
        instrument_ref="SPY-option", contract=contract, side="buy", role="entry",
        status="terminal", remaining_quantity="0", cumulative_filled_quantity="1",
        reserved_cash="0", execution_uncertain=False, source="alpaca",
        provider_record_id="order-1", raw_ref="synthetic://p21/order-1")
    account("account-entry", open_orders=[order])
    report = dict(schema_version=1, account_id="acct-1", source="alpaca",
        provider_event_id="fill-1", broker_order_id="broker-1",
        client_order_id="client-1", replaces_broker_order_id=None,
        replaces_client_order_id=None, replaced_by_broker_order_id=None,
        replaced_by_client_order_id=None, decision_id="decision-1",
        contract=contract, role="buy_entry", event_at="2026-09-04T14:04:59Z",
        available_at="2026-09-04T14:04:59Z", provider_sequence=1,
        status="filled", pending=False, terminal=True, quantity_basis="cumulative",
        raw_filled_quantity="1", cumulative_fill_notional_usd="510",
        cumulative_fees_usd=None)
    add("entry-fill", "order_update", "alpaca-orders", report,
        "2026-09-04T14:04:59Z")
    query("included-entry", "account-entry", included_order_record_ids=["entry-fill"],
        order_ref_aliases=[dict(order_ref="order-1", alias_kind="broker",
            alias_id="broker-1", client_order_id="client-1")])
    add("later-fill", "order_update", "alpaca-orders", {
        **report, "provider_event_id": "late-fill",
        "event_at": "2026-09-04T14:05:01Z",
        "available_at": "2026-09-04T14:05:01Z"}, "2026-09-04T14:05:01Z")
    query("late-excluded", "account-flat", responded_at="2026-09-04T14:05:03Z",
        available_at="2026-09-04T14:05:03Z")
    query("late-included", "account-flat", responded_at="2026-09-04T14:05:03Z",
        available_at="2026-09-04T14:05:03Z",
        included_order_record_ids=["later-fill"])
    query("wrong-kind-ref", "account-flat", included_order_record_ids=["account-flat"])
    query("missing-ref", "account-flat", included_order_record_ids=["absent"])
    query("bad-alias", "account-entry", included_order_record_ids=["entry-fill"],
        order_ref_aliases=[dict(order_ref="order-1", alias_kind="broker",
            alias_id="other-broker", client_order_id="client-1")])
    action = dict(action_id="send-1", kind="submit", issued_at=CAPTURE,
        resolution="unresolved", broker_order_id=None, client_order_id="client-1")
    query("duplicate-action", "account-flat", issued_actions=[action, action])
    query("action-after-capture", "account-flat", issued_actions=[{
        **action, "issued_at": "2026-09-04T14:05:01Z"}])
    query("duplicate-include", "account-entry",
        included_order_record_ids=["entry-fill", "entry-fill"])
    alias = dict(order_ref="order-1", alias_kind="broker",
        alias_id="broker-1", client_order_id="client-1")
    query("duplicate-alias", "account-entry", included_order_record_ids=["entry-fill"],
        order_ref_aliases=[alias, alias])
    bad_contract = {**contract, "strike": "501"}
    account("account-wrong-contract", open_orders=[{**order, "contract": bad_contract}])
    query("bad-account-contract", "account-wrong-contract",
        included_order_record_ids=["entry-fill"], order_ref_aliases=[alias])
    query("missing-field", "account-flat")
    last = payload["members"][-1]
    last["raw_body"].pop("response_id")
    payload["members"][-1] = _member(last["record_id"], last["kind"],
        last["profile_id"], last["raw_body"], last["envelope"])
    query("wrong-source", "account-flat", source="lean")
    account("account-malformed", currency=None)
    query("malformed-account", "account-malformed")
    add("order-malformed", "order_update", "alpaca-orders",
        {**report, "provider_event_id": "bad-quantity", "raw_filled_quantity": "NaN"},
        "2026-09-04T14:04:59Z")
    query("malformed-order", "account-flat",
        included_order_record_ids=["order-malformed"])
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
