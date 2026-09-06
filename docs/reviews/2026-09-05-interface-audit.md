# Independent Phase 1 interface and consistency audit

Reviewed on 2026-09-05. Both supplied documents were read completely before conclusions: the 264-line system design and 1,572-line Phase 1 plan. Repository inspected: `SilasSpencer1/Lean`, branch `design/spy-options-paper-v1`, commit `3c6ef2e7c1ad90948a3b41aac5a98839e2cd4308`. No original document or trading code was changed. This report reviews specifications and supplied code snippets; it does not claim the unimplemented OptionsLab tests pass.

The hybrid architecture is viable, and the phase boundary is sound. Preserve the paper-only scope, single-contract exposure, ask-to-bid economics, cash baseline, deterministic selection, risk precedence, offline training, model/config hashes, authoritative sessions, immutable pre-order records, and deferral of news/social. The interfaces should not yet be declared frozen: several currently discard the facts needed to implement their own safety and reproducibility promises.

Final recommendation alignment: this evidence-only revision's companion [main review](2026-09-05-spy-options-review.md) supersedes any exploratory proxy-first proposal; the forthcoming `docs/superpowers/plans/2026-09-05-options-lab-core-v2.md` is proposed separately in [companion PR #2](https://github.com/SilasSpencer1/Lean/pull/2). The primary target is attempted-policy return from shared stateful replay; fixed-horizon quote return is diagnostic only. The accepted durable store is standard-library SQLite for authoritative append-only events and atomic reservation/checkpoint transactions, with JSONL export. Freeze the corrected core schemas in revised Phase 1; no original source document or implementation is changed by this annex.

In locations below, **S** denotes `docs/superpowers/specs/2026-09-05-spy-options-paper-system-design.md`; **P** denotes `docs/superpowers/plans/2026-09-05-options-lab-core.md`. High findings can invalidate research or permit an unintended order under the planned interfaces; medium findings introduce material ambiguity or unreliable recovery. This is a focused engineering audit, not the parent review's complete methodological evidence matrix.

## Findings

### I1 — High: candidate eligibility is not bound to a decision

**Location:** P 885–920 (`CandidateSet` and selection); 1051–1173 (`choose_action`); 1270–1280 (risk). S 121–133.

**Evidence and consequence:** `CandidateSet` validates right/membership/unique symbols, but stores no decision, context, portfolio, or configuration binding. `choose_action` never checks `global_rejections` and never calls eligibility. Its feature builder checks membership and availability, not DTE, spread, delta, quote age, or quality flags. A public `CandidateSet` containing a 90-DTE contract from the context can therefore pass feature construction, receive a positive prediction, and reach risk; risk has no contract information with which to reject its DTE. More realistically, a selection made under one configuration can be reused with a tighter configuration. Supplying a predictor with the new config hash does not revalidate the old selection.

**Minimal correction:** expose one orchestrator `evaluate_entry(context, state, predictor, config, now)` that computes candidates internally and invokes selection, scoring, and risk. Treat `CandidateSet` as an output for auditing, not caller-supplied authorization. If the standalone scorer remains public, bind the candidate result to a context digest and config hash, reject nonempty global failures, and validate the binding. Reuse the existing eligibility function instead of copying its checks.

**Required tests:** reused candidates after tighter DTE/spread/equity settings; manually constructed candidates with a hard quality flag; stale quote; global rejection plus populated `call`; 90-DTE quote; reordering the input chain preserves selection and audit ordering.

### I2 — High: stale intents and account snapshots can pass risk indefinitely

**Location:** P 455–490 (`TradeIntent`, `PortfolioState`); 1200 and 1270 (`RiskEngine.evaluate`). S 133, 151, 162.

**Evidence and consequence:** risk evaluates session and entry-window rules against `intent.decision_time`, not the time of order authorization or submission. `PortfolioState` contains no observation/reconciliation/mark timestamp or session identifier. A 10:00 intent evaluated at 15:39 with an old all-healthy account snapshot can satisfy every specified guard. No current-time parameter can distinguish it from a timely evaluation. Five-minute decision cadence is also merely descriptive; an adapter may issue repeated decisions within a slot.

**Minimal correction:** add aware `now`, `intent.expires_at`, `decision_id`, `state.as_of`, `state.reconciled_at`, and `state.session_date`; require fresh quote/account/mark evidence and current session/window at authorization. Expiry is absolute original decision+five seconds, not submission+five seconds; no submission occurs after 15:00. Reserve the intent atomically before adapter submission and use its decision/client-order ID for duplicate prevention. Put cadence/slot selection in shared entry orchestration, while adapters only deliver clock events.

**Required tests:** decision at 10:00 submitted after validity expires or after 15:00; previous-session account counters; stale conservative marks; decision replay; two concurrent requests against the same flat snapshot; exact 5-second boundary and monotonic clock disagreement.

### I3 — High: the proposed shared feature interface delegates the feature policy to adapters

**Location:** P 402–430 (`MarketFeatures`); 864–885 (feature vector); S 31–39, 67–85.

**Evidence and consequence:** `MarketFeatures` is explicitly precomputed by an adapter. `build_feature_vector` mostly copies supplied returns, realized volatilities, VWAP distance, and volume z-score. No common bar-history contract or equations define annualization, sampling, overnight reset, warm-up, missing intervals, VWAP price/volume input, or volume seasonality. Schema hashing names and a version string does not make these transformations identical. Two adapters can emit numerically different signals under the same schema ID while each passes the planned tests.

**Minimal correction:** introduce a small normalized completed-bar type and one shared feature-state update/build function. Keep the existing numeric vector as the output. Freeze definitions and a transform-version digest, including close-to-close log-return horizons, realized-volatility units, session VWAP definition, a training-only same-time-of-day volume baseline, warm-up sufficiency, and missing-bar behavior. Derive session-minute counts from `ExchangeSession` instead of trusting independently supplied integers. Implement and test this shared builder in revised Phase 1; freeze the corrected schema with raw-observation contract fixtures before the adapter phases.

**Required tests:** exact values from a hand-computed bar fixture; zero-volume handling; missing intervals; first 30 minutes and session reset; DST/early close; parity from identical raw normalized bars, not merely parity from already-computed features.

### I4 — High: one timestamp cannot represent quotes, bars, and derived Greeks truthfully

**Location:** P 269–294, 400, 621, 920; S 67, 85, 127.

**Evidence and consequence:** the spec permits historical minute observations, but the core uniformly requires source age at most five seconds. A minute quote bar has interval boundaries and availability at its end, not necessarily a last-side update within five seconds. Its bid and ask can have different update times. Copying `EndTime` into `source_at` would invent freshness. Greek/IV provenance is only `FEED` or `LEAN_MODEL`; it has no own as-of time, input cutoff, model/version, rates/dividend assumptions, or units. A new quote can consequently carry yesterday's Greek as though contemporaneous. `source_at <= observed_at` also assumes perfectly ordered clocks, while the design wants clock failures logged.

**Primary evidence:** LEAN `QuoteBar.EndTime` is `Time + Period` at [QuoteBar.cs 191–200](https://github.com/SilasSpencer1/Lean/blob/3c6ef2e7c1ad90948a3b41aac5a98839e2cd4308/Common/Data/Market/QuoteBar.cs#L191). The fill-forward enumerator clones prior data and advances its timestamps at [FillForwardEnumerator.cs 450–475](https://github.com/SilasSpencer1/Lean/blob/3c6ef2e7c1ad90948a3b41aac5a98839e2cd4308/Engine/DataFeeds/Enumerators/FillForwardEnumerator.cs#L450). Current official documentation distinguishes previous-day universe Greeks from `OnData` price-model Greeks; they are not interchangeable observations. [QuantConnect pricing, “Greeks in Universe Selection / Chain Requests / Data Events”](https://www.quantconnect.com/docs/v2/writing-algorithms/reality-modeling/options-models/pricing).

**Minimal correction:** preserve `event_at`, `available_at`, optional `received_at`, interval start/end, observation kind, fill-forward flag, source/feed class, and raw-record ID. Represent missing quote-side source times explicitly. Keep five-second freshness unchanged for genuine live quotes; give historical bars their own explicitly lower-fidelity rule and never grant them execution-quality status. Derived values need their own `as_of`, `available_at`, method ID, and inputs hash. Make Greek provenance and feed class part of artifact compatibility.

**Required tests:** bar close cannot be used at bar start; fill-forward bars never acquire fresh quote status; independent side updates; fresh quote with prior-day delta; revised history available only later; delayed feed cannot be relabeled OPRA.

### I5 — High: the quote-return target and actual policy return are different estimands

**Location:** S 102–106 versus 133–147; P 691–763 versus 1342–1356.

**Evidence and consequence:** the label enters at an observed ask and exits at the bid 30–31 minutes after that observation. Actual execution may fill later, may never fill before timeout, and exits 30 minutes after the fill or earlier on a safety trigger. Entry and exit slippage, cancellation outcomes, and latency are absent from the label. Calling the label “executable” overstates what it identifies. Waiting for a convenient exit quote within a one-minute allowance is also ambiguous unless the first acceptable quote rule is fixed.

**Minimal correction:** preserve this simple label as a clearly named **30-minute ask-to-bid quote-return diagnostic**, record target time and actual selected observation time, and choose the first permitted observation by a deterministic rule. Make the primary learning outcome shared stateful policy replay: fixed decision ask cap, first eligible post-latency fill before timeout, fill-relative 30-minute/safety exit, actual simulated ask-to-bid costs and no-fill/censoring status. Predict expected attempted return on the decision-cap capital basis, and compare conservative expected dollars for exactly one contract. Add target/execution-policy hashes to labels and artifacts; identical policy replay drives promotion. A quote observation alone is never proof of a fill.

**Required tests:** entry timeout produces no trade; delayed fill shifts the holding clock; quote at 30:30 is distinguished from exact-horizon quote; safety exit changes policy P&L while leaving the descriptive quote-return proxy unchanged; threshold evaluation uses the same cost convention as training.

### I6 — High: absent/zero exit bids and unrelated missing Greeks silently censor labels

**Location:** S 106; P 273–322 (`OptionQuote` requires positive prices and IV/delta), 691–763 (`NetReturnLabel | None`).

**Evidence and consequence:** an entry and an exit both require the complete valid quote/Greek object. A perfectly observed exit bid with unavailable IV cannot be represented. Zero bids and missing exit data disappear as `None`; the result preserves no censoring reason. Complete-case performance may consequently be biased toward liquid, observable exits. This is an inference about possible selection bias, not a claim that every missing quote is a loss. A zero bid is not proof that a sell at zero actually filled.

**Minimal correction:** separate raw/normalized quote validity from entry eligibility. Permit nonnegative bid and optional side/Greek fields in observations; keep strictly positive uncrossed bid/ask and approved GreekReadiness READY for new entries, including operational probes. Return a canonical `LabelResult(status, reason, observation_ids, ...)` for every attempted target. Monetary outcome recording depends on price observations, not an exit Greek. Report missing/zero-bid rates by regime and selected action; use conservative loss bounds rather than dropping uncloseable positions. A missing entry path that could contain a fill is also censored: reserve worst-case premium plus costs, retain occupied/uncertain state and block new entries until reconciled. Neither censoring nor the next decision resets exposure to flat.

**Required tests:** missing IV at exit does not erase a valid price observation; zero bid is recorded distinctly from missing bid; every candidate/horizon attempt appears once in the label ledger; loss-bound sensitivity includes every censored selected trade.

### I7 — High: symbol text can contradict approved contract metadata

**Location:** P 271–322 and 455–469; S 16–20 and 123–129.

**Evidence and consequence:** an `OptionQuote` merely requires a nonempty symbol; separate expiration/strike/right/underlying fields control eligibility. The adapter ultimately orders the symbol. A string encoding a different expiry/right/underlying can pass all supplied validation when accompanied by plausible SPY metadata. `multiplier == 100` alone does not establish a standard 100-SPY-share deliverable. LEAN itself derives strike/right from a structured `Symbol.ID`, not independent unverified attributes: [OptionContract.cs 34–48](https://github.com/SilasSpencer1/Lean/blob/3c6ef2e7c1ad90948a3b41aac5a98839e2cd4308/Common/Data/Market/OptionContract.cs#L34).

**Minimal correction:** normalize into `ContractId(underlying, expiration, right, strike, multiplier, deliverable_id)` plus vendor symbol mappings; validate vendor identity against the contract reference record in the adapter. Accept only standard SPY deliverables in v1. Use the validated contract identity in intents and verify the outgoing broker symbol matches it. Pin the contract-reference source and availability time.

**Required tests:** put symbol with call metadata; 0DTE symbol with 14-DTE metadata; adjusted/nonstandard deliverable; wrong underlying; round-trip LEAN SID ↔ contract identity ↔ broker symbol. An adapter mapping fixture is appropriate; a new general-purpose option-symbol framework is unnecessary.

### I8 — High: recovery state rejects the position it needs to liquidate

**Location:** P 1356 (`ExitContext` validation); S 24, 147, 228–233 (early-close/restart/reconciliation promises).

**Evidence and consequence:** `ExitContext` rejects an entry timestamp outside the supplied session. If restart discovers an unintentionally overnight position with its real prior-day fill time, the caller cannot construct the context that would request safe liquidation. Contradictory pending/position facts likewise fail construction rather than producing an explicit reconciliation instruction. A desired invariant for normal operation is not a safe assumption for recovery.

**Minimal correction:** distinguish trusted reconciled positions from a recovery observation. Preserve real fill timestamps across sessions; an overnight or inconsistent position must block entries and produce `RECONCILE_THEN_LIMIT` or the already-defined forced-liquidation instruction according to current tradability and the existing deadlines. Do not erase history by substituting `entry_filled_at=None`. An unreconciled snapshot may carry an unknown position quantity until broker query resolves it.

**Required tests:** prior-session position at restart; same position on early close; wrong-day session; closed market plus discovered holding; exit pending while local holdings temporarily disagree; recovery never emits a blind sell or new entry.

### I9 — High: audit validation excludes important rejected inputs and omits decision threshold

**Location:** P 400 versus 1511; 1017 versus 1472–1511; S 60, 235–239.

**Evidence and consequence:** `DecisionContext` explicitly permits future/stale observations for rejection/audit, but `DecisionRecord.source_timestamps` rejects anything later than decision time. It also requires a valid `PortfolioState`, so malformed raw money/counters cannot be recorded as rejected input. Invalid model metadata is handled thoughtfully as raw strings, but the equivalent market/risk rejection path is missing. Separately, the audit metadata does not include `decision_threshold`; it is neither a StrategyConfig field nor recorded in predictions. Exact decision reproduction depends on external artifact resolution rather than the promised effective decision-policy record.

**Minimal correction:** add a small `InputRejectionRecord` with raw-record references, received/decision times, offending-field names and redacted safe values, stage, reason, and code/config identity. Reserve validated DecisionRecord for valid normalized inputs. Future event times are evidence on a rejected input and must not be silently removed. Record the effective threshold and target/execution-policy identities (or a verified complete immutable bundle manifest containing them). Validate that approved risk intent equals proposed intent and that a buy is backed by the chosen same-symbol prediction/vector, not merely by a matching config hash.

**Required tests:** future timestamp rejected and durably recorded; missing/NaN account equity rejected and recorded; wrong schema metadata preserved; different approved/proposed symbol cannot form a valid record; threshold changes produce a different decision-policy identity and are visible in audit.

### I10 — Medium: lifecycle events lack identity, reconciliation, and cancellation-race semantics

**Location:** P 1386–1408; S 133, 147, 226–233.

**Evidence and consequence:** `LifecycleEvent` is just an enum; no order ID, event ID, filled quantity, broker timestamp, or snapshot revision can associate it with the pending order. Replayed `ENTRY_FILLED` while OPEN raises; after cancellation to FLAT, a delayed fill cannot be represented. After a new pending entry, an old fill can be mistaken for the new order. The one-contract simplification correctly removes fractional fill state, but it does not remove late messages, duplicate delivery, cancellation races, or restart reconciliation. LEAN events provide order ID, event ID, UTC time and quantity at [OrderEvent.cs 42–99](https://github.com/SilasSpencer1/Lean/blob/3c6ef2e7c1ad90948a3b41aac5a98839e2cd4308/Common/Orders/OrderEvent.cs#L42).

**Minimal correction:** retain the small phase table as the reducer of already-reconciled semantic events. Explicitly define a broker-neutral `OrderUpdate` envelope and one deduplication/reconciliation gate that produces those events. Adapters map transport fields; common code decides whether a duplicate/stale/cancel-race event requires no-op or reconciliation. Freeze this envelope now, implement broker transport in its later phase. Never equate “cancel requested” with “canceled and no fill.”

**Required tests:** duplicate fill; late fill after cancel acknowledgement; old-order event while new order pending; fill arriving before submit acknowledgement; cancel/replace lineage; ambiguous request timeout; restart from broker holdings and open orders. Exactly one contract means cumulative quantity is 0 or 1; impossible quantities halt and reconcile.

### I11 — Medium: entry risk, exit authority, and latched halts are not one clear contract

**Location:** S 151 says risk is evaluated “before every order”; P Task 5 only authorizes buys and rejects open positions/kill switch, while Task 6 permits safety exits. `PortfolioState` lacks daily/drawdown halt state; `ExitContext` separately accepts `daily_loss_halted`.

**Consequence:** applying entry risk literally to every order blocks necessary exits. Conversely, a stateless daily/drawdown comparison can re-enable entries after a recovering mark even if the intended circuit breaker should remain latched. Independent booleans and the lifecycle halted flag can disagree.

**Minimal correction:** name the canonical opening-risk API `authorize_entry`; keep exit authorization in shared exit policy, always requiring reconciled long quantity and prevention of overselling. Define `HaltState(entries: tuple[HaltEntry,...], revision)` once. Each HaltEntry retains its own reason, latch time, session/persistent scope, reset rule and evidence. Daily loss remains halted through the session, drawdown until an explicit audited reset, and data/broker pauses resume only after verified recovery. Clearing one reason cannot erase another. Initial normal-operation limits remain unchanged. Define virtual equity as cash plus conservative current liquidation value, with costs and deposits handled explicitly.

**Required tests:** kill switch and daily loss block buys but permit liquidation; daily loss touched then mark recovers; drawdown touched then recovers; session rollover; restart retains halt and high-water mark; simultaneous halt reasons have stable precedence.

### I12 — Medium: “tightening” permits an unvalidated change of the economic strategy

**Location:** P 621 and 623; S fixed scope and 102/137.

**Evidence and consequence:** `holding_period <= 30 minutes` permits a one-minute holding target through a TOML edit. A shorter hold is not necessarily economically safer: turnover/cost/selection properties change. Advancing liquidation to immediately after entry is permitted if local time ordering is preserved. The spec's “threshold exceeded and positive after adverse costs” implies expected return > max(threshold, adverse buffer); P 1090 uses their sum, a stricter but different rule. The full config hash binds these choices, which is sound, but does not make them equivalent or scientifically preapproved. `min_dte <= max_dte` is also missing from the listed configuration relations, though inverted values primarily force cash rather than increase risk.

**Minimal correction:** freeze 30-minute horizon and the selected threshold/cost formula in v1; changes create a new target/policy version and require retraining/revalidation. Retain tighter capital and freshness guards as operational settings. Explicitly require a feasible entry/fill/horizon/liquidation schedule and ordered DTE bounds. Specify costs in dollars per contract and adverse scenario deltas once, avoiding ambiguous double counting with the spec's per-share adverse buffer.

**Required tests:** one-minute holding cannot silently reuse a 30-minute artifact; infeasible temporal settings rejected; equality at selected threshold tested; base/adverse costs trace to the same label/policy manifest.

**Final cost/latency convention:** `score = K*(mean_attempt_return − return_threshold − return_uncertainty) − max(0.005*K,100*current_spread)`, where threshold/uncertainty are returns and K is the original decision ask cap×100. Fit the uncertainty penalty on an independent chronological calibration tail after tuning. Reserve `K + max($1,applicable round-trip fees) + max(0.005*K,100*current_spread)` against the unchanged 0.5% cap. The old $5.10/$1 fixture's $102,200 equity bound excludes the new reserve: the 0.005K floor alone raises it to $102,710; including its actual $0.10 spread gives $104,200. These are fixture bounds, not current quotes. Base/adverse replay entry latency is one/three seconds before the absolute decision+five-second deadline. A complete known ten-second entry-submission stall produces cancel/no-fill; a missing path remains censored. Stress already-filled exits separately. Ex-post adverse charge `max(0.005*K,100*0.5*(entry_spread+exit_spread))` belongs only to stateful evaluation; future exit spread never enters a live hurdle or reservation. Changed latency/cost scenarios replay fills, exposure and subsequent decisions, not only scalar deductions from the base trade list.

### I13 — Medium: default LEAN fills do not implement the specified execution contract

**Location:** S 133–147 and 211–213; P later LEAN-adapter gate at 1572.

**Evidence:** the pinned [DefaultBrokerageModel.cs 223–241](https://github.com/SilasSpencer1/Lean/blob/3c6ef2e7c1ad90948a3b41aac5a98839e2cd4308/Common/Brokerages/DefaultBrokerageModel.cs#L223) selects ImmediateFillModel for equity options. That is an empty subclass of FillModel. [FillModel.cs 678–731](https://github.com/SilasSpencer1/Lean/blob/3c6ef2e7c1ad90948a3b41aac5a98839e2cd4308/Common/Orders/Fills/FillModel.cs#L678) waits for later data, uses side-specific bar extrema and assumes a complete fill. [GetPrices 1150–1210](https://github.com/SilasSpencer1/Lean/blob/3c6ef2e7c1ad90948a3b41aac5a98839e2cd4308/Common/Orders/Fills/FillModel.cs#L1150) can fall back to trades when side quotes are unavailable. These behaviors broadly match the [official Immediate Model documentation, market/limit order sections](https://www.quantconnect.com/docs/v2/writing-algorithms/reality-modeling/trade-fills/supported-models/immediate-model).

**Consequence and correction:** “quote-aware backtest” must specify actual fill policy. This is a required later-phase gate, not a defect because the adapter is deferred. Add an explicit instruction now: configure an OptionsLab per-security fill model or equivalent adapter seam with quote-only side prices, bounded latency/timeouts, no trade fallback, no same-observation execution, and explicit unknown liquidity. Preserve LEAN engine code. Quote-at-limit, cancellation timing, and force-liquidation semantics must agree with the common execution policy. OHLC bars cannot establish intrabar path or queue priority; report that fidelity limitation.

**Required tests:** ask-to-bid round trip on known quotes; equality and crossed-through limit; missing side quote produces no ordinary fill; interval data cannot create an order before availability; full fills never exceed one contract; order timeout and replacement cannot cause two exits.

### I14 — Medium: single-write JSONL is not a complete crash/durability protocol

**Location:** P 1513–1517; S 235–239.

**Evidence and consequence:** the plan correctly verifies the byte count and calls fsync, but a short write can leave a truncated JSON prefix before it raises. The next append can join onto that prefix; a successful threaded happy-path test does not establish power-loss, short-write, multiprocess, or filesystem behavior. Python explicitly returns the number of bytes actually written. [Python 3.11 `os.write` documentation](https://docs.python.org/3.11/library/os.html#os.write).

**Final correction:** use standard-library SQLite as the authoritative local append-only event store, with unique decision/client-order keys and one transaction for the intent reservation and control-state checkpoint. JSONL is a deterministic export, not the transaction log. New entries require durable reservation; database failure blocks them. Pure exit evaluation and the later urgent reduction/reconciliation path do not wait on database writes; reconcile surviving broker facts afterward. Test commit/reopen, failures before/after commit, retries and duplicate identities. Do not describe one file write as universal atomicity or maintain a second authoritative ledger in JSONL.

**Required tests:** injected short write; fsync error; process crash before/after write; truncated trailing line; disk full; retry after uncertain success without duplicated decision IDs. Key-name filtering is a useful guard, not proof that arbitrary string values contain no credentials; log only explicit allowlisted structured fields.

### I15 — Medium: active model compatibility and promotion remain underspecified at the frozen boundary

**Location:** P 1017–1090 and 1472; S 168–185, 242–245.

**Evidence and consequence:** model/config/schema hashes and cutoff checks are good controls. A hash-shaped string is not evidence that the loader verified artifact bytes. A training cutoff alone does not state whether it bounds feature timestamps, label information intervals, or label availability; nor when the artifact was built or promoted. Feed identity and Greek model are not bound. The unchanged feature names can conceal an Indicative-to-OPRA or model-Greek-to-feed-Greek shift. The paper gate lists metrics but lacks pass/fail limits for most of them.

**Minimal correction:** freeze the contract for a verified `ModelBundleManifest`: content hash, source manifest, feature/transform schema, target and execution policy, allowed feed/fidelity and Greek method, training observation cutoff, last label availability, built/promoted times, decision threshold, and validation-report ID. The later offline loader/promotion implementation must verify these fields, not just their string shapes. Predeclare telemetry thresholds and minimum observations before shadow or paper begins. Do not require training or a production endpoint in Phase 1.

**Required tests:** altered model bytes; threshold manifest mismatch; historical model unavailable at replay time; a label finalized after the training cutoff; wrong feed class; changed Greek method; insufficient promotion sample; failed challenger preserves current champion; rollback returns to an independently verified bundle or cash.

## Small consistency corrections that preserve scope

- S 56 scores every eligible candidate; S 131 and P Task 4 score only the deterministic best call and put. Change S 56 to “scores the selected eligible call and put.” Rank-before-score is simpler and avoids a larger model-dependent contract search.
- S 58 permits risk to reduce quantity, but one indivisible contract means the approved v1 actions are approve one or cash. Say that explicitly.
- S 154 says one contract for the initial 30 sessions, while the plan permanently enforces one. Preserve the stricter permanent v1 cap; a later increase needs a design revision.
- P 864 calls the feature subset universally available, yet underlying spread is mandatory and S 75 makes it optional; IV/delta histories also depend on data/model access. Specify required-source readiness, an explicit optional-feature schema, or cash; never silently substitute zero for unavailable spread.
- XNYS can be the conservative **strategy clock** without claiming it is the option's exact tradability calendar. Preserve 09:30–16:00 and all tighter entry/liquidation times. Retain the actual option exchange/tradability calendar in the adapter for reconciliation and exceptional exits. LEAN [SecurityExchangeHours.GetMarketHours 509–566](https://github.com/SilasSpencer1/Lean/blob/3c6ef2e7c1ad90948a3b41aac5a98839e2cd4308/Common/Securities/SecurityExchangeHours.cs#L509) explicitly applies holidays, early closes, and late opens; pin the calendar data/version as well as code.
- LEAN [QCAlgorithm.History.cs 1020–1068](https://github.com/SilasSpencer1/Lean/blob/3c6ef2e7c1ad90948a3b41aac5a98839e2cd4308/Algorithm/QCAlgorithm.History.cs#L1020) truncates future history requests to engine time. That is useful protection, not a guarantee that revised datasets or all supplied feature values were available historically. Preserve original availability evidence in the independent data pipeline.
- Optional gamma/theta/vega/option-return fields are unused by the v1 schema. Deferring their domain fields as well as model usage is reasonable; keep the contract simple until the chosen data source can supply the associated provenance.

## Minimal typed interface revisions

These are proposed contracts, not implemented code. Dataclasses can stay frozen/slotted; functions remain pure where possible. Avoid inventing an event platform or dependency framework.

| Interface | Minimal fields or signature | Owner and invariant |
|---|---|---|
| `ObservationMeta` | `event_at: datetime \| None`, `available_at: datetime`, `received_at: datetime \| None`, `source: str`, `feed_class: FeedClass`, `kind: ObservationKind`, `is_fill_forward: bool`, `raw_record_id: str` | Adapter normalizes facts; core checks availability/fidelity. Missing source time is explicit. |
| `ContractId` | `underlying: str`, `expiration: date`, `right: OptionRight`, `strike: Decimal`, `multiplier: int`, `deliverable_id: str` | Reference mapping verifies broker symbol/SID against one contract identity. Standard SPY only. |
| `QuoteObservation` | `contract: ContractId`, `meta: ObservationMeta`, `bid/ask: Decimal \| None`, side update timestamps/sizes where known | Holds invalid-for-entry but real observations; entry eligibility remains strict. |
| `UnderlyingBar` | `start/end: datetime`, `available_at: datetime`, `close: Decimal`, `volume: int`, VWAP numerator/denominator inputs, quality/source identity | Completed raw-price bar, with split/dividend treatment explicit. Shared builder computes features. |
| `ExchangeSession` | Existing fields + `calendar_version: str`; optional instrument tradability reference | Core uses strategy session. Adapters supply authoritative calendar observations. |
| `GreekObservation` | `delta/iv: float \| None`, `as_of/available_at: datetime`, `method_id: str`, `input_hash: str`, units/quality | Greek/IV provenance and READY entry status do not inherit fresh quote timestamps; probes cannot relax readiness. |
| `FeatureVector` | Existing tuple + transform schema digest and input/availability manifest | Shared feature builder owns calculations and warm-up once. |
| `LabelResult` | `label: NetReturnLabel \| None`, `status`, `reason`, `target_time`, `actual_exit_observation_time`, `information_end`, `available_at`, observation IDs, target/policy hash | Offline labels retain censoring, true information interval and exact quote timing. |
| `VerifiedBundle` | Verified model/manifest hash, feature/transform/normalization schema, target/policy hash, allowed feed/Greek method, training/calibration/label-availability cutoffs, build/promotion time, return threshold, validation ID | Offline validation constructs manifest; loader verifies; scorer consumes immutable verified descriptor. |
| `Predictor` | `metadata: VerifiedBundle`; `predict(vector: FeatureVector) -> Forecast` | Vector carries contract identity; no second contract argument. Forecast returns mean attempted return, return-unit overprediction penalty/bucket and bundle identity. |
| `evaluate_entry` | `(context, account, predictor, config, now) -> EntryEvaluation` | Canonical `decision.py` entry API called by outer `engine.evaluate_step` after independent exit evaluation. |
| `TradeIntent` | Existing fields + contract ID, `decision_id`, `expires_at`, observation/context hash | Binds order identity and validity; v1 quantity remains one. |
| `AccountSnapshot` | Existing state + `as_of`, `reconciled_at`, `session_date`, marks/ledger revision, cash available, halt state | Risk can prove freshness, no borrowing, and account-wide exclusivity. |
| `authorize_entry` | `(intent, context, account, session, config, now) -> RiskDecision` | Canonical opening guards and final revalidation; approved intent must equal proposed intent. |
| `HaltState` | `entries: tuple[HaltEntry,...]`, revision; each entry has reason/latch time/scope/reset rule/evidence | Session reset clears only qualifying session reasons; persistent drawdown/manual reasons survive. |
| `evaluate_exit` | Existing context plus recovery position facts and authoritative current session/tradability | Shared safety/deadline precedence; never rejects the existence of a real overnight holding. |
| `OrderUpdate` | `event_id`, `order_id`, `client_order_id`, `replaces_id`, `event_at`, `received_at`, `status`, `cumulative_filled: int`, optional fill facts | Shared identity/dedup/reconciliation gate; adapter translates transport only. |
| `AuditEvent` | `DecisionRecord \| InputRejectionRecord \| OrderEventRecord \| FillEventRecord` | Typed append-only records. Order/fill implementation remains in later adapter phase; error record needed with Phase 1 validation. |

Rules that must exist once in shared code: timestamp/fidelity eligibility; feature equations; candidate rank/ties; call/put/cash threshold and cost interpretation; five-minute decision slots; risk limits/precedence/halt state; holding/deadline/safety-exit triggers; terminal lifecycle semantics and event reconciliation decisions; canonical audit fields and identities. Adapters own API transport, subscriptions, vendor mapping, calendar acquisition, current quote retrieval, sending already-approved commands, and translating responses. Offline jobs own data acquisition, labels, fold construction, training and promotion, but consume the same target/policy/schema definitions.

## Concrete replacement language

**Replace P Goal/Phase boundary's implication that all interfaces freeze immediately:**

> Phase 1 validates the broker-neutral decision/risk core and the normalized-observation, model-manifest, order-update, and rejected-input audit contracts. It also implements shared feature computations and small deterministic execution-policy replay fixtures. It does not implement market-data transport, model training, LEAN execution, or Alpaca orders. Freeze the revised interfaces and golden contract fixtures in this phase; later integration must pass actual adapter parity and any interface change must be explicitly versioned.

**Replace S Outcome Predicted's opening paragraph:**

> The primary supervised target is attempted net return from the versioned one-contract execution policy, including observed no-fills and costs, on the fixed decision ask-cap capital basis. Its replay uses admitted quote histories, explicit latency, fixed entry cap/timeout and the same fill-relative holding/safety exits used at runtime. Censored outcomes retain unknown utility and separate conservative loss bounds. The 30-minute ask-to-bid quote return remains a diagnostic. Neither synthetic nor Indicative quote replay proves executable performance. Labels, artifacts and reports identify target and execution-policy versions.

**Add after P decision policy:**

> The adapter's entry API is evaluate_entry. It takes the current clock, normalized context and fresh reconciled account snapshot, generates candidates internally, and returns an immutable auditable evaluation. No externally supplied CandidateSet constitutes eligibility approval. Immediately before submission the shared authorization checks current validity and atomically reserves the intent by decision/client-order ID. Expired or duplicate intents cannot be submitted.

**Add after P exit policy:**

> Entry validation rejects prohibited exposure; recovery validation must preserve observed exposure even when it violates policy. An existing prior-session, unmatched or uncertain holding blocks entries and requests reconciliation/liquidation under the existing conservative deadlines. Only reconciled long quantity may be sold. Data and account failures cannot prevent recording the reason or evaluating the emergency exit path.

**Replace P lifecycle's last paragraph:**

> The four-phase table applies only to identified, deduplicated semantic order events. One contract eliminates fractional quantity states; it does not eliminate out-of-order delivery, repeated fills, cancellation races or restart reconciliation. The shared OrderUpdate contract retains order/event identities, terminal status, cumulative quantity and times. A separate common reconciliation gate resolves duplicate and stale updates before applying the phase transition. Broker transports are implemented in later plans.

**Replace P audit's atomicity claim:**

> Standard-library SQLite owns authoritative append-only audit events, unique intent reservations and risk/lifecycle checkpoints. Persist reservation plus checkpoint in one local transaction before permitting a new entry. Restore latches and pending identities after restart; conflicting retries halt and reconcile. Export JSONL deterministically from this ledger. Database or export failures never fabricate successful orders and cannot block pure exit evaluation; urgent risk reduction remains independent of audit I/O. Test commit/reopen, interruption, full-disk/write failure and duplicate concurrent reservations. No custom file-append transaction protocol is required.

## Phase-aware acceptance additions

1. **Phase 1 core:** exact boundary/unit tests above; permutation/property tests for deterministic ranking and risk monotonicity; every rejected raw input has a safe audit representation; no malformed candidate result authorizes a trade; stale intent/account snapshots fail closed; exits remain available under all entry halts.
2. **Data/training phase:** shared feature golden fixtures; point-in-time contract universe and versioned observations; no forward-filled quote presented as fresh; every label attempt has an outcome status; information intervals/availability cutoffs enable purging; feature/threshold fitting entirely inside folds; complete verified bundle manifest. Economic tests requiring genuine quotes cannot use Indicative data as substitute evidence.
3. **LEAN phase:** custom quote-only fill policy fixtures, calendar and fill-forward tests, order timing/latency/timeout stress, reconstructed costs, policy P&L with censored observations, and complete no-overlap/no-overnight records. Reuse engine seams; no LEAN source modifications are warranted by this review.
4. **Alpaca shadow phase:** identical raw normalized event replay yields identical feature vectors, candidates, actions, risk checks, exits and audit identities; lifecycle permutations deduplicate identically. Frozen threshold and measured availability rules apply to the actual entitlement class.
5. **Paper-order phase:** endpoint gate and verified credentials/account class; replay/forced-liquidation gates pass before order enablement; at least 30 completed sessions as already required, with enough actual order/cancel/recovery observations to exercise the declared mechanisms. Zero duplicate orders, oversells, unapproved entries, or unexplained holdings. Every session ends with reconciled flat holdings or an explicit gate failure. A lack of opportunities does not count as fill/recovery evidence.

No phase here adds a live endpoint. No paid-data entitlement or licensing bypass is proposed.
