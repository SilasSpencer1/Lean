# SPY Intraday Long-Options Paper System Design

**Status:** Adopted for staged core implementation on 2026-09-05; paper orders and economic promotion remain gated.

**Repository:** SilasSpencer1/Lean, upstream baseline 23b735d99a357807dc0df9f4c51d30f05fe0d277. Documents reviewed at 3c6ef2e7c1ad90948a3b41aac5a98839e2cd4308.

**Companion:** [review, evidence, typed interfaces and acceptance gates](../../reviews/2026-09-05-spy-options-review.md).

## Objective

Build a reproducible experimental research and paper platform for intraday long SPY options. The first useful result may be that the strategy cannot overcome costs or cannot trade at the intended capital. LEAN is the historical simulation and regression engine. A direct Alpaca runner consumes the same core policies for shadow and paper operation.

## Fixed scope and invariant limits

- SPY only; standard, unadjusted, US-listed options delivering 100 SPY shares with premium multiplier 100. Reference data, not multiplier alone, prove eligibility.
- One long call or put, exactly one contract, at most one position and one pending entry across a dedicated paper account. The one-contract limit applies throughout version one, including after the first 30 sessions.
- No shorts, spreads, 0DTE, planned exercise, overnight positions, borrowing or real-money execution.
- Decisions every five minutes, 10:00–15:00 America/New_York inclusive. A missed decision is logged, never replayed as a late new order.
- Nominal holding period: exactly 30 minutes from confirmed entry fill. Entry order lifetime ends at decision time plus five seconds; cancel at that instant, and do not assume a fill on the expiry boundary. No upward entry repricing. Quotes are revalidated at submission; submission itself must remain within the entry window.
- Cancel incompatible orders/start liquidation reconciliation at 15:35. Escalate a remaining confirmed position at 15:39; require broker-confirmed flat and no opening orders by 15:40. A failed flatten is an incident, never a fabricated success.
- No entries on holidays or early-close sessions. Recovery deadlines are the earlier of configured times and common-session close minus 25/20 minutes; escalation is one minute before the effective deadline.
- With `K = 100 × original decision ask cap`, require `K + max($1.00, configured round-trip fee floor, applicable round-trip fee estimate) + max(0.005 × K, 100 × current option spread) <= 0.005 × current conservative virtual equity`. Also require <=3 completed entries/session; halt at conservative daily P&L <=-1% of session-start equity or drawdown >=5% of persistent high watermark. Operator halts and drawdown halts survive restart; a daily halt resets only at a new reconciled session.
- Every configuration preserves or tightens these limits. The 30-minute holding period is fixed in version one. Changing it requires a design/target revision and new experiment/artifact; a smaller number is not automatically a scientifically interchangeable target.
- Prototype software/data budget remains zero. Genuine historical quote data is a separate, explicit licensed-data prerequisite for economic claims.
- Phase 1 targets Python 3.11. Integration pins and tests the actual LEAN image/Python environment, core wheel and SDK; do not infer runtime compatibility from an unbounded `>=3.11` declaration.
- Initial virtual equity is explicitly declared as USD 150,000 in the prerequisite record after the user delegated its selection. This simulation balance does not establish broker cash or actual contract affordability. Missing capital at a decision still means cash. Test fixtures may use large balances but cannot select the experimental capital implicitly.

## Architecture decision

Keep a dependency-light broker-neutral OptionsLab core and two thin adapters. The core owns normalization semantics, feature mathematics, eligibility, selection, risk, exit triggers, execution-policy decisions, lifecycle reduction and audit schemas. Adapters own I/O, symbol transport mapping, authoritative session retrieval and submission/reconciliation. They cannot define independent strategy rules.

The current official QuantConnect Alpaca integration supports equity options and validates module subscription. The direct adapter avoids that dependency lawfully; it does not remove or bypass validation. Recheck versioned capabilities before integration. A clean-room brokerage plugin remains deferred. See the inspected [platform evidence](../../reviews/2026-09-05-platform-evidence.md).

Keep an event loop, ordinary files, a local durable ledger and offline training commands. No message bus, feature-store service, distributed registry, deep learning, reinforcement learning, LLM trading agent or signature model is required.

## 1. Point-in-time information

Store immutable raw observations before normalization. Every item records source/feed, entitlement and allowed-use manifest, provider event time, actual receipt time when captured, assumed historical availability when needed, retrieval time, revision identity, raw hash and quality flags. A historic download's retrieval time is provenance, not its 2024 availability time. If receipt history is unavailable, declare the latency/availability assumption; never invent measurements.

Features require `available_at <= decision_time`. Time intervals are half-open. A one-minute bar covering 09:59–10:00 becomes eligible after its end and declared publication delay. A later correction does not alter an earlier feature vector. Same-timestamp ordering uses receive sequence. Fill-forward bars retain original event provenance and never masquerade as fresh quotes.

Required version-one observations:

- Completed SPY one-minute bars with open/close, volume, traded-dollar sum when available, interval endpoints, feed and availability; at least 31 consecutive same-session closes before a 30-return window is available.
- Fresh SPY and candidate option bid/ask with side sizes for economic evaluation; simultaneous validity must be supportable. Unknown size permits plumbing only. Freshness <=5 seconds uses the quote event timestamp, separately from availability.
- Historical contract listing/status, OCC identity fields, expiration, right, strike, multiplier, deliverable and reference effective/availability times. Enumerate contracts that existed then, including subsequently expired contracts.
- Independently timestamped delta and IV, with quote/underlying dependency hashes, calculation method/version, units and rate/dividend assumptions. Both must be finite and suitable for the same observation time; missing/unverifiable delta means cash. Missing exit Greeks never prevents recording a monetary outcome.
- Exchange sessions and broker-supported option hours, halt/operability state, account snapshot, open orders, fills, virtual ledger, latched halts and artifact/config identities.

The minimum model vector is explicit: 1/5/15/30-minute SPY log returns; square root of sums of squared one-minute log returns over 5/15/30 minutes (unannualized); session VWAP distance when actual traded dollars are available; minute volume and its within-training, same-time-of-day normalization; underlying spread; minutes from open/to common close; right, log moneyness, calendar DTE, option bid/ask/spread, quote age, delta and IV. All transforms, missingness behavior, feed semantics and units are hashed. No timestamp, model ID, account P&L or feed identifier is an alpha feature.

A feed without traded-dollar sums uses a separately named bar-price/volume proxy in a separate feature schema, not a falsely exact VWAP. A feed without a required feature has a separately validated reduced schema or abstains; never encode missing spread as zero. IEX-derived volume and consolidated volume are not interchangeable. Warmup failures produce cash. No backfill of unavailable opening history.

For delta/IV, Phase 1 only freezes the observation contract. A later data phase must prove one consistent provider path with field timestamps, or implement one common, pinned American-option analytics enrichment outside the dependency-free core. No silent switch between prior-day universe Greeks, contemporaneous LEAN model Greeks and snapshot values of unknown age. Until that gate passes, fixtures are valid and real-time decisions abstain.

Optional research features, each requiring a new schema and ablation: gamma/theta/vega with units, lagged option returns from the same contract, prior-day open interest with publication time, same-expiry skew, nearby-expiry term slope, synchronized IV-minus-realized-volatility diagnostics, and timestamped scheduled-event flags. Intraday realized volatility is not a forecast of the full 7–21-day option life. Trade/quote imbalance requires genuine timestamped trades/quotes and an explicit lagged forecast experiment; minute bar volume is not order flow.

News/social remain shadow-only: preserve publication, first-seen, retrieval, edits/deletions, content hash, source URL and usage rights; no collection/training rights are inferred from public visibility. Present-day text or engagement cannot reconstruct historical first-seen state. Exclude revised macro values, retroactively adjusted price levels against raw strikes, final daily volume/OI, future chain completeness, untimestamped Greeks, fabricated OPRA, and embeddings or sentiment without legitimate point-in-time history.

## 2. Targets and economics

Maintain distinct **quote-horizon diagnostics**, **execution-policy labels**, and **broker paper observations**. Only execution-policy labels on admitted genuine quote histories can support economic evaluation. Paper fills never become training labels.

At decision time t, define the buy limit L as the observed ask and capital basis K=100L. After frozen entry latency, fill one contract only at the first admissible subsequent quote with ask <=L and sufficient displayed size; simulate purchase at that ask, without price improvement. If a complete observation path shows no qualifying quote within five seconds, record NO_FILL, zero P&L and zero attempted return. A missing path is CENSORED, not NO_FILL. This is a conservative stated simulation assumption, not evidence that displayed liquidity would necessarily fill.

For a simulated fill at f with ask A, evaluate the shared exit policy continuously. Request the normal exit at f+30 minutes, or earlier for safety/liquidation; observe exit latency and bounded limit/cancel/reconcile semantics. If a sale is simulated at admissible bid B:

`net_pnl = 100*(B-A) - round_trip_fees - additional_execution_charges`

`attempt_net_return = net_pnl / K`

`filled_premium_return = net_pnl / (100*A)`

Spread is already paid by ask-to-bid prices; never subtract it twice. The predictor estimates expected attempted net return, including observed no-fills. Store prediction-to-dollar conversion K explicitly. Keep individual realized premium return as a reporting diagnostic. Labels record decision/fill/exit/available times, actual information interval, exit reason, all costs, quote IDs and target/execution/config hashes. One entry uses one contract and the same terminal exit rules in training and deployment.

Unknown/missing exits stay CENSORED with reasons and coverage by time/volatility/side. A zero bid is observable but not evidence that a sell could execute at zero; an unfilled position remains unresolved. Recompute sensitivity with zero liquidation value minus costs for every censored filled trade. A missing entry path that might conceal a fill receives the same worst-case loss up to reserved premium plus costs; carry uncertain/occupied state forward and block later entries until reconciled. Never assume flatness merely because a label is censored. Never drop hard-to-price losers and publish complete-case profits. Model fitting may use complete observed outcomes only with disclosed censoring; promotion additionally passes the conservative full-cohort analysis.

The legacy ask-at-t/bid-at-t+30-minute return is a separately named diagnostic. Its first post-horizon quote may be at most 60 seconds late, with actual elapsed time stored. It is not the execution-policy target. Minute OHLC cannot certify second-level marketability, side synchronization or execution latency and stays a lower-fidelity experiment.

Cost scenarios retain the original $1/contract round-trip floor and at least 0.5% of K additional adverse allowance. Before economic testing, use the larger of that fee floor and the documented applicable fee estimate. Base uses ask/bid, verified fees and declared measured or assumed latency (initial assumption one second). Adverse uses three-second entry/exit latency and adds `max(0.005*K, 100*0.5*(entry_spread+exit_spread))` to the realized replay outcome. Fully replay each scenario's different fills/exits and portfolio occupancy; subtracting a scalar from base P&L is insufficient. Severe doubles the adverse charge and injects ten-second stalls. A ten-second entry stall exceeds the absolute five-second order lifetime: it yields a canceled/no-fill attempt only when the full path proves no execution. Stress exits on already-filled positions separately so this scenario is not vacuously safe. Severe requires correct controls and disclosed losses, not positive expectancy. Costs are hypotheses to be calibrated from genuine quotes and timing, not guarantees.

Decision-time scores and capital reservations use only causal inputs: `adverse_reserve = max(0.005*K, 100*current_option_spread)`, or a higher frozen estimate learned solely on development data. Future exit spreads belong only to outcome evaluation. Reserve `K + max($1, fee_estimate) + adverse_reserve` under both cash and the unchanged 0.5% premium-risk cap; never claim the score's scalar reserve captures all adverse latency effects. SPY order prices use the broker-confirmed tick increment; reject unsupported prices or round conservatively (buy cap down, sell limit up), then rerun risk/marketability checks.

## 3. Candidates, forecasts and cash

Eligibility remains 7–21 calendar DTE, absolute delta 0.40–0.60 with correct right sign, positive bid/ask and ask>bid, spread <=max($0.05, 8% midpoint), fresh observations, standard deliverable, premium/cost cap and healthy regular session. The spread floor is preserved, not mistaken for an 8% hard cap; report its actual percentage near the floor. Reject off-grid/missed decisions and insufficient time to complete the policy before liquidation.

Choose one call and one put deterministically by distance from |delta|=0.50, spread fraction, DTE, then canonical identity. Run this once inside the public orchestration path. Candidate results bind context, time, configuration, capital state and rule version. Submission reuses the same eligibility function against fresh inputs; an old CandidateSet is not authority to order.

Initial champion is cash. Predeclare baselines: cash, fixed 5-minute SPY momentum and its mean-reversion inverse, and a regularized linear attempted-return predictor. Rule baselines receive identical contracts, costs, execution, session and capital controls; they cannot use more trades or ideal fills. First model search uses a small fixed ridge grid, e.g. 0.1/1/10 after training-only scaling. Histogram gradient boosting is one later challenger only if supported by honest OOS improvement. Total research trial ledger includes rejected schemas, thresholds, policies and manual changes.

For each candidate compute `score_dollars = K*(mean_attempt_return - threshold_return - uncertainty_return) - adverse_reserve`. The forecast already includes base costs; subtract only incremental adverse charges. Threshold and uncertainty are return units, converted to dollars by K. Buy the candidate with strictly positive highest conservative dollar score; exact ties and nonfinite/unknown uncertainty yield cash. The nonnegative uncertainty penalty is a simultaneous, session-block resampling upper bound on calibration-bucket mean overprediction, fitted on a separate calibration tail after hyperparameter/threshold selection. Preregister two-session calibration blocks and require at least ten complete blocks/20 distinct sessions plus 50 observed attempts per supported bucket; nonfinite/degenerate resampling or insufficient support means cash. The longer 5/10/20-session sensitivity applies only to portfolio P&L. This is not a claim of per-trade coverage. Ranking by dollars is explicit because quantity is fixed at one; report return ranking as a separate research alternative if desired.

Cash reasons include no candidates, cost/uncertainty hurdle, missing data, absent capital, unvalidated artifact, shift of feed/Greek method, stale state, pending order, market closure and all risk halts. A predictor can only propose entry; it cannot change limits or suppress exits.

## 4. Exits, lifecycle and failure handling

Evaluate exits on clock, quote, order, account and safety events, independently of five-minute entries and model availability. Preserve normal 30-minute hold and safety exits for stale data, reconciliation failure, artifact failure, daily loss and kill switch. A drawdown halt also disables new positions and may conservatively request exit.

Before normal exit, obtain a fresh bid and submit sell-to-close limit for the broker-confirmed long quantity. Reconcile every two seconds; permit up to three replacements, each only after cancellation/replacement acknowledgement or definitive broker reconciliation. Never oversell because an earlier exit may have filled. A pending entry cancellation is not flatness; a late fill is still a position.

On uncertain broker state, reconcile before sending a quantity-changing order. At deadline-minus-one-minute, cancel incompatible exits and use a verified brokerage-supported paper liquidation action for the remaining confirmed long, including a market order only if supported and safely reconciled. If market/data/broker conditions prevent this, preserve the position state, alert and latch the system off. No blind sell, stale-mark success, or retry that creates a second position.

Account recovery must represent unexpected prior-session fills, positions on early closes, unknown instruments and unexpected exercise/deliverables. These violate the strategy but are admissible recovery facts. They never pass entry eligibility. A missing calendar cannot authorize a blind close; query operability and holdings, request safest supported reduction, record unresolved incidents. The platform aims to remain flat but cannot guarantee execution during exchange halts or network failure.

Order events carry event/execution ID, client intent ID, broker order/replacement lineage, receive sequence and cumulative filled integer quantity. Exact duplicates are idempotent. Unexplained regressions or conflicting terminal events cause reconciliation, not discarded fills or a generic crash. One contract has no fractional fill; unsupported quantities are quarantined without losing actual exposure. Every resume goes through shared verified reconciliation; adapters cannot clear risk latches by constructing a fresh happy state.

## 5. Capital and risk precedence

Use a dedicated paper account and separate virtual ledger. Risk evaluates the freshest snapshot and decision at actual submission time, not only original signal time. Reject account snapshots older than five seconds, changed portfolio versions, stale quotes and intents past their five-second validity. Broker available cash/buying power and reserved funds also constrain a one-contract purchase; the broker's default paper balance never overrides virtual capital.

Preserve the existing stable precedence after boundary validation: integrity/finite money/consistent state and binding; kill switch; connection; reconciliation; data; artifact; pending orders/open position; quantity and entry count; authoritative current session/window; capital/premium; daily loss; drawdown. Reuse the same guards for submission and entry replacement. Exit orders use a separate reduction-only authorization and cannot be blocked by entry premium limits or an invalid predictor.

Conservative daily P&L is realized plus min(unrealized,0), using liquidation-side marks and estimated closing costs. Missing/stale marks reserve full premium loss for safety accounting, block new entry and initiate recovery; they do not fabricate an executed loss. Persistent high watermark, cash flows, costs, session-start equity and entry counts are reconstructed from durable broker facts. Threshold crossings latch even if later marks recover. No Kelly sizing, leverage scaling or stop-based sizing.

For the existing $5.10 ask/$1 cost fixture, the legacy fee-only lower bound is ($510+$1)/0.005=$102,200 virtual equity. Including the minimum new 0.5%-of-K adverse reserve raises that lower bound to $102,710; including this fixture's $0.10 current spread instead requires ($510+$1+$10)/0.005=$104,200. These are fixture calculations, not current SPY price estimates. If the declared account cannot afford eligible contracts, cash is the valid outcome.

## 6. Training, validation, promotion and disablement

Production-recipe refitting is offline at monthly boundaries; weekly jobs produce reports or inactive research candidates. No active artifact mutation occurs intraday or within an evaluated month. Artifacts bind model bytes, code, exact dependency/runtime versions, feature definitions, data/feed/Greek semantics, contract rules, target and execution policies, costs, effective strategy configuration, training/label-availability cutoff, calibration, threshold, seeds and data manifests. Validate content hashes on load, not just their hexadecimal format. Export linear coefficients as safe data; never deserialize untrusted model executables.

Preserve actual artifact creation and activation timestamps. Operational paper decisions require actual build/activation before use. Historical research may separately declare simulated availability under a registered fit/activation schedule, including label maturity and fitting/deployment delay; actual creation time is never backdated. Bind the availability basis and simulated time in the manifest, and prohibit simulated availability from authorizing operational paper decisions.

Use session-grouped chronological development: initially 12 months training, next three validation, next month test; advance monthly with fixed rolling 12/3 windows and at least six development test months. Within the three validation months, use the first two for model/threshold selection and the last for calibration only; if its buckets lack support, abstain. Reserve a further final six-month period, opened once only after choices freeze. At least 27 calendar months are needed for this initial schedule; more may be needed to meet trade counts. Predeclare the final end date before looking at performance; insufficient counts at that date mean inconclusive and require a new prospective protocol, not adaptive significance seeking.

Within each held-out month and during the final test, weekly jobs may generate reports but keep the evaluated model/normalizer/calibration frozen for that block. At the next monthly boundary refit coefficients and normalizers using the registered 12-month training window and update calibration from the registered validation tail using only matured past observations. Hyperparameters, feature choices, bucket construction rule and threshold remain frozen after development; the other validation months are monitoring-only in the final test. Earlier held-out months may enter past windows mechanically but are never inspected by a researcher to tune the recipe. Thus the final six months test an adaptive, preregistered monthly activation recipe; they are not a static-data exclusion from all later fits. Human access to aggregate test outcomes is withheld until its end. No intraday/week-by-week champion selection. A static six-month model would be a separately named experiment.

Store each sample's information interval through final outcome availability. Purge training labels intersecting a held-out interval, group all same-decision candidates together, and require every training label to have matured by the fit cutoff. Retain an embargo of at least 30 minutes, extend through actual exit/availability for longer labels, and test its application. With fully separated intraday sessions, the overnight gap may already satisfy it; adding arbitrary row gaps is not a substitute. A prospective forward test never trains on later dates.

All preprocessing, imputation, feature choice, model tuning, uncertainty estimates and thresholds are fitted inside training/validation. Use executed portfolio daily dollar P&L including cash days to compare policies; no-trade profit factor is undefined, not infinity. Track every research trial and use multiplicity-adjusted development inference. PBO/DSR are optional diagnostics, not optimization objectives.

Economic promotion requires genuine admitted data, >=126 development OOS eligible full sessions plus >=126 final eligible full sessions, >=500 total OOS executed trades including >=150 in final holdout, and session-block confidence bounds. These floors are proposed policy, not a theorem; a nominal six-month period with too few eligible sessions/trades is inconclusive, never a reason to force trades. Evaluate once at the predeclared horizon; do not repeatedly extend until significance appears.

Preserve and operationalize all existing gates: positive base/adverse expectancy; superiority to cash/momentum/reversion under identical risk; profit factor>1.10; max drawdown<5%; largest positive monthly P&L / sum of positive monthly P&Ls<=0.50; no causal/reproducibility failure; and positive expectancy for threshold changes of ±10% (a zero threshold uses a preregistered absolute increment). In addition require a positive one-sided 95% lower bound for adverse mean daily P&L and paired improvement over baselines, with development multiplicity control; report sensitivity to 5/10/20-session resampling blocks. Preserve the full cohort under censored-outcome pessimism.

Initial final holdout certifies one frozen research recipe, not unlimited future challengers. Later promotions use fresh forward evidence and a declared testing budget; never reuse the six-month holdout as a weekly leaderboard. Failed challengers leave a healthy champion unchanged. Integrity, feed/schema changes, unacceptable execution drift or latched risk stops disable entry immediately; cash is the fallback if prior champion compatibility is uncertain. Only activate a verified artifact between sessions, with atomic pointer change, recorded evaluation and tested rollback. The observation windows and specific operational/forecast-drift tests are in the companion validation program.

## Data/fidelity and release classes

**Tier 0:** synthetic/bundled fixtures prove mechanics only. **Tier 1:** free/indicative/aggregated data support plumbing and labeled operational experiments. **Tier 2:** licensed genuine historical synchronized bid/ask plus sizes, contract reference history and sufficient underlying observations support conditional economic evaluation. Tier 2 still requires defensible latency/fill assumptions.

Verify endpoint-level history for quotes, trades, bars and Greek inputs separately. An advertised options start date is not proof of historical NBBO or Greeks. A subscription is not proof of retention, model-use or redistribution rights. In particular, the documented QuantConnect Download license restricts internal LEAN use and conversion; obtain separately applicable permission before normalizing such downloads into independent Python training data. Preserve entitlement/feed/dataset versions and a permitted sample before committing to the economic study. No fee or license bypass. See the platform evidence for exact license scope; this is not a determination of the user's private agreements.

Separate three gates: CORE_VERIFIED; OPERATIONAL_PAPER_ONLY; ECONOMICALLY_VALIDATED_RESEARCH. An indicative-data paper experiment can meet only the operational gate. Its bounded rule policy is explicitly an engineering probe, not the promoted economic champion. Cash remains the economic champion until qualifying evidence exists. Every artifact/decision/report includes its permitted use; promotion cannot erase the original data class.

Before paper orders, complete >=10 full shadow sessions, >=600 scheduled decision records (including cash), all replay/recovery tests and paper endpoint/account/entitlement checks. Default all runs to shadow. Then observe >=30 full paper sessions and >=30 completed round trips for execution diagnostics; extend the observation plan prospectively if counts are too small, without forcing trades. Zero invariant breaches; 100% ledger reconciliation; confirmed flat/no opening orders at every eligible deadline; >=99% scheduled decision or explicit skip coverage; any new stale-data order fails release. Feed outage must yield correct abstention even if uptime cannot meet the operational target.

Compare paper facts to parallel genuine-quote simulation when available. Never train on paper P&L or claim equivalent live fills. Keep production order endpoints unreachable, including SDK defaults, environment overrides and redirects. No future live gate is implemented in version one.

## Audit and reproducibility

Persist typed decision, rejection, intent, order, fill, risk-halt, recovery, exit and promotion events with record version, event ID, decision/intent lineage, UTC event/receipt times, payload/config/model/data hashes and code commit. Rejected raw timestamps and malformed values remain recordable in a sanitized failure envelope; admitted observations use stricter types. Never overwrite a prior decision with later broker facts.

Use standard-library SQLite as the authoritative local event/ledger store. Append-only events and unique intent/reservation identity are committed atomically with a versioned control checkpoint; recovered state is verified against the event log. JSONL remains a canonical export, written to a temporary sibling and replaced only after complete flush. This replaces the original unsupported single-write atomicity promise with a small native transaction boundary. Configure/test durable local transactions and bounded lock timeouts; no distributed store is needed. Database failure blocks new risk, alerts, and must not deadlock the independent reduction-only exit/reconciliation path; record surviving broker facts afterward. Serialization is an allowlist, not a guarantee that scanning key names removes secrets from values.

Read secrets only from environment or OS credentials. Never put credentials in TOML, command arguments, model metadata or raw request logs. Use fake clients/inert credentials in tests. Pin fixtures, calendars, hashes, random seeds and dependency locks. News/social licensing remains separate shadow work after market-data and execution validity.

## Implementation boundary

Phase 1 freezes and tests the shared core interfaces and deterministic policies. It does not claim a trained model, data entitlement, LEAN parity, real broker reconciliation or paper readiness. Later phases implement point-in-time collectors/enrichment/training, LEAN quote-aware simulation, then Alpaca shadow/paper integration; news/social shadow ingestion remains optional and last. Do not modify the LEAN engine unless a demonstrated adapter seam requires it. The companion proposed Phase 1 plan supplies the task order and tests.

## Implementation increments

Deliver the core through [small behavior increments](../plans/2026-09-05-optionslab-premium-budget.md), beginning with pure one-contract affordability. Types live beside the behavior that owns them; the architecture table defines semantic contracts, not a requirement to create every future class in one domain module. The full Phase 1 obligations remain in the [core roadmap](../plans/2026-09-05-options-lab-core-v2.md). The [prerequisite record](../plans/2026-09-05-optionslab-prerequisites.md) admits synthetic fixtures only.
