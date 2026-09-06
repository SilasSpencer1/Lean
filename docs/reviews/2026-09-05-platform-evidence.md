# SPY options paper system: platform and market-structure evidence

Review date: 2026-09-05. Scope: platform capabilities, execution constraints, calendars, contract metadata, data provenance, and licensing. This is evidence for a design review, not a recommendation to trade or a finding that any strategy is profitable.

## Inspection record and limits

The full user request and both source documents were read before conclusions: [design, 264 lines](../superpowers/specs/2026-09-05-spy-options-paper-system-design.md) and [implementation plan, 1,572 lines](../superpowers/plans/2026-09-05-options-lab-core.md). Line references below refer to their reviewed versions. Local LEAN HEAD was `3c6ef2e7c1ad90948a3b41aac5a98839e2cd4308`; the design names upstream baseline `23b735d99a357807dc0df9f4c51d30f05fe0d277`.

The linked official pages and relevant source sections were inspected on the review date. Documentation without a publication date is identified as current documentation, not a dated historical guarantee. No account was authenticated, no entitlement or rate limit was probed, no dataset was purchased, and no order was submitted. Therefore availability to this user's particular account remains unverified. Alpaca SDK links to `master` describe inspected moving source, not a pinned dependency contract. The QuantConnect brokerage source below is pinned to a full commit.

## Main findings

1. **The proposed historical bid/ask labels do not have an established Alpaca data source.** Alpaca documents historical option bars and trades, while its quote endpoints and SDK methods are for latest quotes. A June 2026 staff response explicitly says historical option quotes are not on its roadmap. “History since February 2024” must not be read as historical NBBO, Greek, or point-in-time chain coverage.
2. **Paper fills and indicative-feed observations can represent different markets.** Alpaca describes paper matching at NBBO, while Basic options observations are indicative. Its paper simulator also omits important execution effects. Paper performance is useful for operational validation but cannot establish an executable strategy edge.
3. **Greek and underlying-feed provenance affect the model contract.** Alpaca snapshot Greeks have no independent timestamp in the SDK model; current LEAN distinguishes prior-day precomputed chain Greeks from intraday pricing-model Greeks. Basic IEX underlying features are not interchangeable with consolidated SIP features.
4. **Recovery must represent positions carried into the current session.** The plan's exit-context rejection of an entry outside the current session prevents the restart behavior required by the design. A liquidation deadline is an action-and-alert deadline; fills cannot be guaranteed during outages or halts.
5. **Current LEAN Alpaca supports equity options and performs license validation.** Using a direct SDK adapter to meet the stated free-tier constraint is reasonable; claiming that LEAN's Alpaca integration lacks option support would be incorrect.

## Capability matrix

| Capability needed by the design | Inspected evidence | What can safely be concluded | Design consequence |
|---|---|---|---|
| Historical option bars/trades | [Alpaca history overview](https://docs.alpaca.markets/us/docs/historical-option-data), [bars endpoint](https://docs.alpaca.markets/us/reference/optionbars), [trades endpoint](https://docs.alpaca.markets/us/reference/optiontrades) | General option history begins February 2024. The documented historical products are aggregates and trades; this does not establish quote availability. | Keep these as separate capability rows; neither supplies executable bid/ask labels. |
| Historical option bid/ask | [Latest-quotes endpoint](https://docs.alpaca.markets/us/reference/optionlatestquotes), [historical client source](https://raw.githubusercontent.com/alpacahq/alpaca-py/master/alpaca/data/historical/option.py), [Alpaca staff reply, June 17, 2026](https://forum.alpaca.markets/t/historical-option-quote-question/19029) | No historical quote method appears in the inspected complete client. The staff reply says the feature is not on the roadmap. This is stronger than an unsuccessful endpoint guess, but the forum is not a contractual service definition. | Block historical ask-to-bid training until a permitted supplier and sample are verified. Forward collection is a separate possible route. |
| Historical snapshots/Greeks | [Option-chain endpoint](https://docs.alpaca.markets/us/reference/optionchain), [snapshot models](https://raw.githubusercontent.com/alpacahq/alpaca-py/master/alpaca/data/models/snapshots.py) | Chain snapshots are latest data. Their IV and Greeks can be absent. The Greek object has no own observation timestamp. No historical point-in-time chain capability was established. | Do not reconstruct past eligibility with today's chain or treat snapshot request time as Greek source time. |
| Basic live option feed | [Trading API market-data plans](https://docs.alpaca.markets/us/docs/about-market-data-api), history overview above | Basic options are indicative; OPRA requires the appropriate entitlement. Indicative quotes are derived, and its trade messages are delayed. | Label indicative observations explicitly and prevent equivalence with genuine OPRA execution evidence. |
| Basic underlying equity feed | Market-data plans above | Basic equity coverage is IEX; the paid plan covers all US exchanges. | Bind underlying feed and feature definitions to each dataset and model; do not silently interchange volume, VWAP, or spread series. |
| Live option quotes | [Option stream](https://docs.alpaca.markets/us/docs/real-time-option-data) | The stream provides quote timestamps, prices, sizes, exchanges and conditions. It uses MsgPack, requires feed entitlement, and prohibits wildcard quote subscriptions. Quote/trade messages do not supply Greeks. | Subscribe only to the selected universe; capture source and receive times and obtain or compute Greeks through a separate, documented path. |
| Orders for long options | [Options trading](https://docs.alpaca.markets/us/docs/options-trading) | Paper options are enabled by default. Long calls/puts require the relevant option approval and buying power. Orders use whole contract quantities, supported time-in-force, and no extended-hours flag. | Contract/risk approval does not replace account capability and buying-power checks at execution. |
| Paper execution | [Paper trading](https://docs.alpaca.markets/us/docs/paper-trading) | The simulator describes NBBO matching, does not constrain order quantity to displayed liquidity, and omits queue position, latency slippage, market impact and certain costs. | Separate broker paper fills, market observations, and conservative reconstructed outcomes; record their market-data feeds independently. |
| Official LEAN Alpaca options | [QC supported assets/order table](https://www.quantconnect.com/docs/v2/cloud-platform/live-trading/brokerages/alpaca), [pinned brokerage source](https://github.com/QuantConnect/Lean.Brokerages.Alpaca/blob/389a97e105d3b8d5bd1e060c9ad8c70c828ba189/QuantConnect.AlpacaBrokerage/AlpacaBrokerage.cs#L146) | Options are supported. Initialization invokes subscription validation; its failure path terminates the process. | The direct adapter is a cost/dependency architecture choice. Preserve all upstream license checks. |
| QC CLI and downloaded data | [CLI requirements](https://www.quantconnect.com/docs/v2/lean-cli/key-concepts/getting-started), [dataset licensing](https://www.quantconnect.com/docs/v2/cloud-platform/datasets/licensing) | The documented CLI requires membership in a paid organization. QC's Download license imposes internal LEAN-use and format-conversion restrictions. | Distinguish open-source engine use from CLI/services; verify separate permission before feeding QC downloads into normalized Python datasets. |

**Endpoint qualification:** The [SDK reference](https://alpaca.markets/sdks/python/api_reference/data/option/historical.html) describes historical trades as available “up to 7 days ago,” while the formal trade endpoint and history overview do not establish that same universal limit. This inconsistency is unresolved. Do not advertise either unlimited February-2024 trade access or a universal seven-day ceiling without a dated account-level probe. The absence of a documented historical-quote endpoint is supported by inspected API/client content and the staff answer; it was not inferred solely from a failed URL.

**Plan limits:** Current Trading API tables list Basic option quote subscriptions at 200 and the paid option feed at 1,000, with historical API limits and recent-data restrictions that differ by plan. These are documentation values, not measured capacity. Broker API commercial tiers are a separate product. Capability discovery should record product, account environment, entitlement, pagination behavior, and observed limits; do not use a single `free/paid` boolean.

## Detailed evidence and required changes

### 1. Establish the quote-data dependency before calling the plan executable

**Locations:** design 102–106, 123–133, 195–210; plan 650–763 and 1572.

**Observed:** The client file [`alpaca/data/historical/option.py`](https://raw.githubusercontent.com/alpacahq/alpaca-py/master/alpaca/data/historical/option.py) was read completely: historical methods cover bars and trades, while quotes are latest-only; snapshot and chain methods do not implement historical as-of requests. The June 17 support reply linked above independently confirms the present historical-quote gap. The current chain endpoint's `updated_since` filters recent quote/trade updates; it is not a historical snapshot selector.

**Inference and change:** The design's Tier 1 language currently encourages treating general historical option access as enough to build its quote-derived labels. Replace it with three separately qualified capabilities: historical trades/bars; live indicative observations; genuine historical quotes from a verified source. A source is accepted only after a small sample proves contract coverage, bid/ask semantics, timestamp meaning, corrections, pagination, session coverage, and permitted use. Until then, the core can be built and checked with clearly marked synthetic fixtures, but no historical strategy validation claim is supported. There is no evaluated replacement vendor in this review.

### 2. Preserve point-in-time chain, Greek and feature provenance

**Locations:** design 67–86, 123–133; plan 256–306, 400–451, 864–883, 920, 1017–1052.

**Observed:** The [Alpaca market-data FAQ, Options Greeks section](https://docs.alpaca.markets/us/docs/market-data-faq) says its calculation uses Black–Scholes, current quote input, the latest SIP underlying trade, nonexpired contracts, and a convergent IV solution. The inspected [SDK snapshot model](https://raw.githubusercontent.com/alpacahq/alpaca-py/master/alpaca/data/models/snapshots.py) makes IV/Greeks optional and supplies no Greek-specific timestamp. [QC option pricing documentation](https://www.quantconnect.com/docs/v2/writing-algorithms/reality-modeling/options-models/pricing) distinguishes prior-day precomputed universe/chain Greeks from intraday `Slice` price-model values; its current American default is Binomial Cox–Ross–Rubinstein.

**Inference and change:** `FEED` versus `LEAN_MODEL` is insufficient to establish comparability or freshness. Store provider, entitlement/feed, calculation family/version, input price convention, input timestamps, availability time, rate/dividend assumptions when known, and the rule used when inputs are unavailable. Unknown Greek source timing must remain unknown. Do not mint a timestamp from HTTP receipt and call it measurement time. A quote should be representable without Greeks; candidate selection can require qualified Greeks while quote-only label construction remains possible.

The plan needs an explicit feature-availability contract, not just precomputed values accompanied by one timestamp. The FAQ's bar section identifies bars by their interval's left edge; a completed minute's features are not available at that left-edge timestamp. Confirm the precise convention for each ingested product and enforce `available_at <= decision_at`. Bind option feed, underlying feed, Greek policy and feature definitions to the model artifact and prediction context.

### 3. Keep paper P&L out of market-return training labels

**Locations:** design 208–210, 227, 233; plan 650–763, 1511–1515.

**Observed:** [Alpaca paper documentation, Simulation and Orders](https://docs.alpaca.markets/us/docs/paper-trading) describes marketable order fills against NBBO and randomized partial fills. Its generic simulator description does not prove fractional fills for a one-contract option order. It also says displayed quantity does not constrain the paper fill quantity. Market-data subscriptions and paper execution are separate services.

**Inference and change:** The phrase “without using paper P&L as training data until the weekly cutoff” makes the cutoff appear to cure simulator bias. It does not. Keep broker fills as operational evidence. If forward paper-period observations later become training examples, reconstruct labels from a separately defined, permitted market-data series and conservative execution policy; store that label independently of broker paper P&L. A Basic indicative observation and an NBBO-based simulator fill must never be combined into an unlabeled homogeneous dataset. Thirty sessions can be an operational gate, not proof of economic edge.

### 4. Define the execution model instead of inheriting LEAN fallback fills

**Locations:** design 123–133, 137–147, 210, 220; plan 650–763, 1313–1408.

**Observed local code:** [`Common/Orders/Fills/FillModel.cs`](../../Common/Orders/Fills/FillModel.cs), inspected at local HEAD, implements `InternalLimitFill` around lines 660–730 and `GetPrices` around 1145–1207. Its limit logic can fill the full requested quantity using bar prices after the order timestamp. Price extraction can fall back from quote-side data to trade/current prices when quote data is absent. These defaults are not a quote-only execution guarantee.

**Inference and change:** A later isolated OptionsLab adapter should provide the intended fill policy without changing engine defaults. Define source quote timing versus arrival time, order activation time, quote age, limit marketability, spread-crossing and additional costs, size treatment, timeout, replacement, and unavailable-exit treatment. No quote must mean unavailable execution evidence rather than a trade-price substitute. Apply the same normalized policy when comparing LEAN results with Python labels. Avoid claiming that conservative labels already model queue position or executable size.

### 5. Contract identity and deliverable cannot be inferred from multiplier alone

**Locations:** design 11, 123–133; plan 275–306, 314, 352, 746–750.

**Observed:** The OCC's Options Industry Council [corporate-action FAQ](https://www.optionseducation.org/referencelibrary/faq/splits-mergers-spinoffs-bankruptcies) includes a reverse-split example where the premium multiplier remains 100 but the deliverable is only ten shares. Other adjustments can create mixed deliverables. [Alpaca option contract documentation](https://docs.alpaca.markets/us/docs/options-trading) exposes contract style, status, tradability, size, and dated reference values. Its default contract query horizon and pagination are narrower than an assumed complete 7–21 DTE universe.

**Inference and change:** Identify SPY contracts as ETF options and explicitly restrict v1 to verified standard, American-style, physically settled contracts with the expected deliverable. Preserve point-in-time contract reference data and adjustment status; validate symbol, root, underlying, expiry, right and strike consistently. `multiplier == 100` alone is not an adequate standard-contract check. Preserve open-interest observation date; do not substitute retrieval time. Set explicit expiry bounds and exhaust pagination before declaring the candidate universe complete.

### 6. Calendar policy is separate from venue and broker hours

**Locations:** design 25, 137–147; plan 263–272, 400, 621, 1270, 1341–1356.

**Observed:** [NYSE's calendar and options-hours sections](https://www.nyse.com/trade/hours-calendars) distinguish equity closes from option closes, including 4:00/4:15 p.m. normal closes and 1:15 p.m. for eligible options on early-close dates. [Cboe's 2026 options calendar](https://www.cboe.com/about/hours/us-options) lists September 7 as a holiday and November 27/December 24 as early closes. The generic venue tables alone do not establish the routing behavior of a specific Alpaca account. [Alpaca's options-hours support page](https://alpaca.markets/support/when-do-options-trade), dated March 2025, describes 9:30 a.m.–4:00 p.m.; this is another reason to avoid assuming venue hours equal broker acceptance hours.

**Inference and change:** `XNYS` 9:30–16:00 is a defensible conservative strategy window if named as that policy. It should not be presented as the complete SPY option-exchange calendar. Store the calendar source/version and distinguish underlying session, tradable contract session, broker order window, and strategy window. Preserve the explicit 15:35/15:40 caps; do not derive later limits from a 16:15 option close. Disallow new entries on early-close sessions in v1 if intended, while allowing recovery logic to represent those sessions. A pinned calendar package still needs a versioned update and exceptional-closure process.

### 7. Restart reconciliation must handle overdue positions and uncertain fills

**Locations:** design 137–147, 220–222; plan 1341–1408.

**Observed:** Plan line 1356 rejects an `entry_filled_at` outside the supplied session. The [pinned official Alpaca brokerage source](https://github.com/QuantConnect/Lean.Brokerages.Alpaca/blob/389a97e105d3b8d5bd1e060c9ad8c70c828ba189/QuantConnect.AlpacaBrokerage/AlpacaBrokerage.cs#L449), inspected in its order-event handling section, tracks execution identifiers and cumulative fill information and handles repeated terminal events. This is evidence that order reconciliation needs more than a state-name transition table; it is not a requirement to copy the module.

**Inference and change:** Represent a broker position whose fill occurred before the current session, even if it violates strategy policy. Block new exposure, reconcile open orders and positions, and attempt a closing order only within the current accepted trading window. Add explicit unknown-order outcome, cancel-requested versus cancel-confirmed, duplicate/replayed event, reconnect and replacement semantics in the later adapter plan. Do not submit a second close merely because the first response timed out. Treat flatten deadlines as initiation/escalation deadlines and record unfilled residual exposure as an incident. Market halts and outages make an unconditional same-day-flat guarantee impossible.

### 8. Expiration risk is long-holder exercise risk, not early assignment

**Locations:** design 123–147, 195, 220–222; plan 1341–1356.

**Observed:** The OCC's [exercise explanation](https://www.optionseducation.org/optionsoverview/exercising-options) distinguishes the long holder's exercise right from assignment to a writer. Alpaca's current options guide describes automatic exercise of sufficiently in-the-money expiring options, broker risk liquidation before expiration, and exercise/assignment reporting through account activities; such activity is not generally an option trade websocket event.

**Inference and change:** An intended intraday long-only position with 7–21 DTE should not normally encounter expiration, but a failed close followed by an unattended account can. Reconcile all broker positions and account activities, including unexpected underlying shares; preserve a recovery workflow for aging positions. Do not describe a purchased option as subject to early assignment. Avoid relying on manual do-not-exercise support requests as an automated risk control. The initial strategy's no-overnight and no-equity-exposure policies remain appropriate, but violations must be representable.

### 9. Paper-only enforcement belongs at the trading transport

**Locations:** design 29, 235, 253; plan 524–544 and later adapter boundary.

**Observed:** The inspected constructor in [`alpaca/trading/client.py`, lines 45–82](https://raw.githubusercontent.com/alpacahq/alpaca-py/master/alpaca/trading/client.py) defaults `paper=True`, but an explicit `url_override` takes precedence over the paper/live default. Alpaca documents distinct paper credentials and the paper trading API host. That default alone does not protect against configuration mistakes.

**Inference and change:** Validate the final trading transport's exact HTTPS paper host, reject alternate hosts and redirects, use paper credentials, and make unauthorized endpoint configuration fail before any order-capable connection. Market-data hosts are a separate allowlist. Verify environment variables, constructor overrides and persisted configuration cannot route orders to live trading. These controls are required in the adapter phase; a core `paper_only` dataclass field does not itself enforce them.

### 10. Order price increments and buying power require broker-level checks

**Locations:** design 123–133, 151–162; plan 471–487, 746–750, 1270.

**Observed:** [Alpaca's price-increment guidance](https://alpaca.markets/support/options-pricing-increments-and-options-order-handling), inspected under premium increments, lists SPY among options with penny price increments at all premium levels. Broker approval and buying power are independent of the system's virtual risk ledger.

**Inference and change:** Quantize order prices to the applicable contract tick using decimal or integer cents, then repeat risk and marketability checks on the submitted price. Distinguish virtual strategy budget, reserved exposure for working orders, real paper account balances, option approval and broker buying power. An account snapshot needs identity and observation time. Do not enable borrowing simply because the broker offers margin.

### 11. Do not add the obsolete PDT rule to this design

**Locations:** design 151–162; plan 471–487, 524–544; future broker account checks.

**Observed:** [FINRA Regulatory Notice 26-10](https://www.finra.org/rules-guidance/notices/26-10) states that revised intraday-margin rules became effective June 4, 2026, replacing the old pattern-day-trader framework, with a permitted firm transition through October 20, 2027. The SEC approved the change in [Release 34-105226, April 14, 2026](https://www.sec.gov/files/rules/sro/finra/2026/34-105226.pdf); this review inspected its overview, rationale and approval sections, not every page. [Alpaca's implementation announcement](https://alpaca.markets/blog/finra-retires-the-pdt-rule-introducing-alpacas-new-intraday-margin-framework/), updated June 4, reports production adoption for Trading and Broker APIs and a July 6 removal schedule for legacy fields.

**Inference and change:** Treat any stale $25,000/four-day-trade statement in module README material as historical, not the current account rule. Do not turn the strategy's three-entry limit into a regulatory claim. Read current broker account constraints and fail closed on unavailable or inconsistent account state. The rule change does not authorize this system to borrow, add leverage, or trade real funds. Paper and live account behavior were not authenticated in this review.

### 12. Data licenses must cover the actual storage and training workflow

**Locations:** design 33–48, 96–98, 195–210; plan 400–451 and artifact metadata.

**Observed:** [QC dataset licensing, Download section](https://www.quantconnect.com/docs/v2/cloud-platform/datasets/licensing) limits the documented download license to internal LEAN use and restricts redistribution and conversion. The [OPRA Electronic Subscriber Agreement](https://cdn.opraplan.com/documents/OPRA_Electronic_Subscriber_Agreement.pdf), inspected completely, limits subscriber use and retransmission; its nonprofessional addendum identifies personal/nonbusiness circumstances. The [OPRA Non-Display Declaration](https://cdn.opraplan.com/documents/OPRA_Non_Display_Declaration.pdf) includes automated trading, risk and portfolio applications for covered datafeed recipients. The [OPRA Datafeed Policy](https://cdn.opraplan.com/documents/OPRA_Datafeed_Policy.pdf) addresses uncontrolled feeds and distribution approval.

**Inference and change:** Maintain a rights record per source: subscriber status, permitted local storage, retention, normalization, backtesting/training, derived-data output, redistribution and termination obligations, supported by the applicable vendor agreement and date. Do not assume QC purchase permits Python conversion. Do not assume every personal Alpaca API subscriber owes an enterprise non-display fee: OPRA's vendor/professional uncontrolled-feed documents do not establish that conclusion. Entitlement to view or query quotes is not independently proof of every downstream use right. This review did not inspect the user's signed agreements.

### 13. Pin platform versions and distinguish current documentation from repository behavior

**Locations:** design 28, 33–48, 258; plan dependency/runtime setup and adapter boundary.

**Observed local code:** [`Common/Brokerages/AlpacaBrokerageModel.cs`](../../Common/Brokerages/AlpacaBrokerageModel.cs), read fully at local HEAD, accepts option market and limit orders; the current QC live-brokerage documentation lists additional option stop types. The v1 market/limit subset avoids that discrepancy. [`DockerfileLeanFoundation`](../../DockerfileLeanFoundation) and [`DockerfileLeanFoundationARM`](../../DockerfileLeanFoundationARM) pin Python 3.11.11. The external module's README contains older Python, settlement and PDT statements, so it is not uniformly current.

The official module commit history identified `389a97e105d3b8d5bd1e060c9ad8c70c828ba189` dated August 24, 2026. Its actual file [initialization](https://github.com/QuantConnect/Lean.Brokerages.Alpaca/blob/389a97e105d3b8d5bd1e060c9ad8c70c828ba189/QuantConnect.AlpacaBrokerage/AlpacaBrokerage.cs#L146) and [validation](https://github.com/QuantConnect/Lean.Brokerages.Alpaca/blob/389a97e105d3b8d5bd1e060c9ad8c70c828ba189/QuantConnect.AlpacaBrokerage/AlpacaBrokerage.cs#L906) were inspected. The validation path calls the module license service, verifies status/expiry and exits on failure. No bypass was attempted or proposed.

**Inference and change:** Pin the LEAN revision, Python minor/runtime, SDK version, data schema, pricing model and calendar package. Test Python 3.11 rather than only a newer locally installed interpreter. Use current broker endpoint documentation for external capabilities and pinned code for implementation behavior, explicitly recording conflicts rather than silently merging them.

## Required acceptance evidence before historical validation or paper execution

These are verification requirements for subsequent implementation work, not claims that the present phase supplies an adapter.

| Gate | Minimum concrete evidence |
|---|---|
| Historical quote source | Dated permitted sample with bid/ask and source/availability timestamps, verified contract reference, explicit feed, pagination and missing-data behavior. Historical bars/trades do not pass. |
| Greek eligibility | Null-Greek fixture rejected for entry without losing the quote; stale/missing input provenance rejected; prior-day and intraday LEAN Greeks kept distinct. |
| Feed parity | A model trained on one option/underlying feed combination rejects an incompatible prediction context. |
| Execution consistency | No fills from missing quotes or trade fallback; no execution before order activation/quote availability; submitted tick-rounded price drives risk checks. |
| Calendar/recovery | September 7 holiday, November 27 early close, normal-session deadlines, previous-session position after restart, halt, unavailable broker and rejected close are all representable. |
| Broker state | Duplicate/out-of-order events, timeout with unknown submission outcome, cancel/replace race, outstanding order exposure and account snapshot staleness do not create a second position or orphan an existing one. |
| Paper transport | Live host, URL override, redirect and configuration substitution are rejected before trading; exact paper host succeeds with test transport. |
| Rights | Source-specific agreement and permitted-use record covers the actual storage/normalization/training/output flow. |
| Validation claims | Paper operational reliability, quote-based research results and broker simulator P&L are reported separately; session count alone does not establish profitability. |

## Remaining uncertainties

- The user's actual subscriptions, option approvals, account configuration, signed data agreements and paper execution behavior have not been inspected.
- No historical NBBO/Greek supplier has been selected or evaluated, and the initial eligible contract universe has not been measured against subscription limits.
- Exact SPY route/session behavior must be checked at the instrument and broker level. Venue-wide calendars alone are insufficient.
- Alpaca's SDK historical-trade lookback wording conflicts with broader API history descriptions; authenticated capability checks are needed before a data-ingestion promise.
- Several provider pages lack publication dates. Pinning a dependency cannot freeze external service behavior; preserve retrieval dates and repeat targeted capability checks before deployment.
- Official documentation supports the architecture constraints above. It does not demonstrate that the proposed model has predictive or economic edge.
