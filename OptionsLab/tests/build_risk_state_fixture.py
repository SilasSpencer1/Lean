"""Build literal source-backed account transitions for pure risk-state tests."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from build_fixture import _member, _payload_bytes


FIXTURE_ID = "p16b-risk-state-v1"
NOW = datetime(2026, 9, 4, 14, 5, tzinfo=timezone.utc)
NEXT = datetime(2026, 9, 8, 14, 5, tzinfo=timezone.utc)


def build_fixture() -> bytes:
    """Frame actual P08 account/calendar records with literal transition facts.

    :returns:             Complete registered synthetic fixture bytes.
    :raises   OSError:     If the current P12c template cannot be read.
    """
    original = json.loads((Path(__file__).parent / "fixtures/p12c-account-context-v1.json").read_bytes())
    session = deepcopy(next(row for row in original["members"] if row["record_id"] == "session"))
    flat = deepcopy(next(row for row in original["members"] if row["record_id"] == "flat"))
    option = next(row for row in original["members"] if row["record_id"] == "good-option_quote")
    session_profile = deepcopy(next(row for row in original["modeled_source_profiles"]
                                    if row["profile_id"] == session["profile_id"]))
    account_profile = deepcopy(next(row for row in original["modeled_source_profiles"]
                                    if row["profile_id"] == flat["profile_id"]))
    rows = [session]
    profiles = [session_profile]
    later = deepcopy(session)
    later["record_id"] = "next-session"
    later["profile_id"] = "risk-next-session"
    later["raw_body"].update(session_date="2026-09-08", opens_at="2026-09-08T13:30:00Z",
                             closes_at="2026-09-08T20:00:00Z", available_at="2026-09-08T13:00:00Z",
                             provider_record_id="risk-next-session")
    later["envelope"].update(event_id="event-risk-next-session", stream_id="risk-next-session",
                             simulated_received_at="2026-09-08T13:00:00Z",
                             raw_ref="synthetic://p16b/next-session")
    rows.append(_member(later["record_id"], later["kind"], later["profile_id"],
                        later["raw_body"], later["envelope"]))
    profiles.append({**session_profile, "profile_id": "risk-next-session", "stream_id": "risk-next-session"})

    def account(name, at, **changes):
        """Add one independent current account occurrence with actual source framing."""
        body, env = deepcopy(flat["raw_body"]), deepcopy(flat["envelope"])
        body.update(account_id="risk-account", source="fixture-account-ledger",
                    provider_record_id="report-" + name, available_at=at.isoformat(),
                    as_of=at.isoformat(), reconciled_at=at.isoformat(),
                    ledger_revision="ledger-" + name,
                    reconciled_ledger_revision="ledger-" + name,
                    reconciliation_id="reconciliation-" + name,
                    risk_state_revision="external-" + name,
                    halt_checkpoint_ref="checkpoint-" + name)
        body.update(changes)
        env.update(event_id="event-" + name, raw_ref="synthetic://p16b/" + name,
                   simulated_received_at=at.isoformat(), stream_id="risk-" + name,
                   receive_sequence=1, supersedes_record_id=None)
        profile = "risk-profile-" + name
        profiles.append({**account_profile, "profile_id": profile, "stream_id": "risk-" + name})
        rows.append(_member(name, "account_snapshot", profile, body, env))

    account("base", NOW)
    for name, cash in (("riskref-refresh", "100000"), ("same-event-conflict", "99000")):
        account(name, NOW + timedelta(seconds=1), virtual_cash=cash, virtual_equity=cash)
        row = rows[-1]
        row["envelope"]["event_id"] = "event-base"
        row["raw_body"].update(provider_record_id="report-base", available_at=NOW.isoformat(),
                               as_of=NOW.isoformat(), reconciled_at=NOW.isoformat(),
                               ledger_revision="ledger-base", reconciled_ledger_revision="ledger-base",
                               reconciliation_id="reconciliation-base")
        if name == "riskref-refresh":
            row["raw_body"]["risk_state_revision"] = "external-refreshed"
            row["raw_body"]["halt_checkpoint_ref"] = "checkpoint-refreshed"
    for name, cash, pnl in (("loss-exact", "99000", "-1000"),
                            ("loss-above", "99000.01", "-999.99"),
                            ("dd-exact", "95000", "0"),
                            ("dd-above", "95000.01", "0")):
        account(name, NOW, virtual_cash=cash, virtual_equity=cash,
                virtual_settled_cash=cash, broker_settled_cash=cash,
                broker_available_cash=cash, broker_nonmargin_buying_power=cash,
                session_realized_pnl=pnl)
    account("prior-peak", NOW, high_water_mark="110000")
    account("later-peak", NOW + timedelta(seconds=1), high_water_mark="110000")
    account("disconnected", NOW + timedelta(seconds=1), connection="disconnected")
    account("simultaneous", NOW + timedelta(seconds=1), virtual_cash="95000",
            virtual_equity="95000", virtual_settled_cash="95000",
            broker_settled_cash="95000", broker_available_cash="95000",
            broker_nonmargin_buying_power="95000", session_realized_pnl="-5000")
    account("recovered", NOW + timedelta(seconds=2))
    account("next-flat", NEXT, session_date="2026-09-08", virtual_cash="95000",
            virtual_equity="95000", virtual_settled_cash="95000", session_start_equity="95000",
            high_water_mark="95000", broker_settled_cash="95000",
            broker_available_cash="95000", broker_nonmargin_buying_power="95000")
    account("cash-before", NOW, ledger_revision="ledger-cash-before",
            reconciled_ledger_revision="ledger-cash-before")
    account("cash-after", NOW + timedelta(seconds=1), ledger_revision="ledger-cash-after",
            reconciled_ledger_revision="ledger-cash-after", virtual_cash="100012.50",
            virtual_equity="100012.50", virtual_settled_cash="100012.50",
            broker_settled_cash="100012.50", broker_available_cash="100012.50",
            broker_nonmargin_buying_power="100012.50")
    for number in (1, 2, 3):
        for filled, suffix in ((0, "zero"), (1, "one")):
            order = dict(order_ref=f"order-{number}", client_order_id=f"entry-{number}",
                         instrument_ref="opaque-K", contract=deepcopy(option["envelope"]["contract"]),
                         side="buy", role="entry", status="terminal" if filled else "open",
                         remaining_quantity="0" if filled else "1",
                         cumulative_filled_quantity=str(filled), reserved_cash="0",
                         execution_uncertain=False, source="fixture-orders",
                         provider_record_id=f"order-report-{number}",
                         raw_ref=f"synthetic://p16b/order-{number}")
            name = f"order{number}-{suffix}"
            at = NOW + timedelta(seconds=(number - 1) * 2 + filled)
            account(name, at, open_orders=[order])
            if number == 1 and filled == 0:
                for suffix, quantity in (("half", "0.5"), ("two", "2")):
                    bad = {**order, "status": "terminal", "remaining_quantity": "0",
                           "cumulative_filled_quantity": quantity}
                    account("order1-" + suffix, NOW + timedelta(seconds=1), open_orders=[bad])
    stressed_order = deepcopy(next(row for row in rows if row["record_id"] == "order1-two")["raw_body"]["open_orders"])
    account("adverse-order", NOW + timedelta(seconds=1), open_orders=stressed_order,
            virtual_cash="95000", virtual_equity="95000", session_realized_pnl="-1000")
    completed_order = deepcopy(next(row for row in rows if row["record_id"] == "order1-one")["raw_body"]["open_orders"])
    account("order1-regress", NOW + timedelta(seconds=2), open_orders=[{
        **completed_order[0], "status": "open", "remaining_quantity": "1",
        "cumulative_filled_quantity": "0"}])
    account("order1-mismatch", NOW + timedelta(seconds=1), open_orders=[{
        **completed_order[0], "client_order_id": "other-client"}])
    pending = deepcopy(next(row for row in rows if row["record_id"] == "order1-zero")["raw_body"]["open_orders"][0])
    sibling = {**pending, "order_ref": "order-sibling", "client_order_id": "entry-sibling",
               "provider_record_id": "order-report-sibling"}
    account("order1-sibling-zero", NOW, open_orders=[pending, sibling])
    account("order1-sibling-vanished", NOW + timedelta(seconds=1), open_orders=completed_order)
    account("order1-one-same-event", NOW + timedelta(seconds=1), open_orders=completed_order)
    rows[-1]["envelope"]["event_id"] = "event-order1-zero"
    payload = {**original, "fixture_id": FIXTURE_ID,
               "generator_id": "optionslab-risk-state-fixture-builder", "generator_version": "1",
               "generator_source_ref": "OptionsLab/tests/build_risk_state_fixture.py",
               "assembled_at": datetime.now(timezone.utc).isoformat(),
               "members": [_member(m["record_id"], m["kind"], m["profile_id"],
                                   m["raw_body"], m["envelope"]) for m in rows],
               "modeled_source_profiles": profiles}
    return _payload_bytes(payload)


if __name__ == "__main__":
    target = Path(__file__).parent / "fixtures" / (FIXTURE_ID + ".json")
    target.write_bytes(build_fixture())
    print(target)
