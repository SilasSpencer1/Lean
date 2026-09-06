# SPY Intraday Long-Options Paper System Design

> **Superseded:** Historical document. Use the [adopted revision](2026-09-05-spy-options-paper-system-design-v2.md) and its implementation increments; do not execute this archived version.

**Status:** Approved for implementation on 2026-09-05

**Repository:** SilasSpencer1/Lean, forked from QuantConnect/Lean

**Upstream baseline:** master at 23b735d99a357807dc0df9f4c51d30f05fe0d277

## Objective

Build an automated research and paper-trading system for intraday SPY options. The first version buys only a single long call or a single long put, holds at most one position, and is always flat before the close. LEAN is the historical simulation and regression engine. A small direct Alpaca paper runner uses the same decision and risk package because the official LEAN Alpaca plugin requires a QuantConnect module license even when it is compiled locally.

The system is an experiment platform, not a claim of profitability. Its first job is to make every input, label, decision, fill assumption, risk decision, and model version reproducible enough that a negative result is trustworthy.

## Fixed Scope

- Underlying: SPY only.
- Instruments: US-listed SPY equity options.
- Positions: one long single-leg call or one long single-leg put.
- Excluded: short options, spreads, exercise as a planned exit, 0DTE, overnight positions, and real-money execution.
- Entry window: 10:00 through 15:00 America/New_York.
- Research decision cadence: every five minutes.
- Nominal holding horizon: 30 minutes.
- Hard liquidation deadline: 15:40 America/New_York.
- Version one opens no position on holidays or early-close sessions. Adapters supply an authoritative XNYS session; if an existing position is discovered on an early-close day, its liquidation window advances with that close.
- Initial paper size: one contract, subject to all tighter risk limits.
- Cost target: zero-cost software and data tiers for the prototype.
- Python version: 3.11, matching the current LEAN Python runtime requirement.
- Execution mode: Alpaca paper only. Production endpoints are rejected by configuration validation.

## Architecture Decision

### Selected: hybrid free-tier architecture

The fork contains an OptionsLab package with domain, feature, model, decision, risk, and telemetry code that has no dependency on LEAN or Alpaca. Two thin adapters call it:

1. A LEAN QCAlgorithm adapter translates Slice and OptionChain data into package inputs and translates approved intents into LEAN orders for historical simulation.
2. An Alpaca paper adapter translates Alpaca data into the same inputs and translates approved intents into paper orders.

This preserves one implementation of the trading rules while avoiding removal or bypass of the official plugin's license validation.

### Rejected for version one: supported all-LEAN live deployment

This has the best backtest/live parity, but the current LEAN CLI live deployment and official Alpaca brokerage module require a paid QuantConnect organization or module entitlement. It violates the free-tier constraint.

### Deferred: independent LEAN Alpaca brokerage plugin

A clean-room IBrokerage and data-queue adapter could keep paper execution inside LEAN without using the licensed plugin. It adds reconnection, subscription, symbol mapping, order reconciliation, partial-fill, and dependency-packaging responsibilities before the strategy has demonstrated value. It is deferred until the model survives the paper gates.

## System Flow

1. Data collectors store timestamped raw market observations without modifying them.
2. A deterministic normalizer rejects stale, crossed, missing, or malformed observations.
3. Feature generation uses observations available at or before the decision timestamp.
4. Candidate generation applies fixed contract and liquidity rules.
5. The model predicts the net 30-minute return for each eligible candidate.
6. The selector compares the best call, best put, and cash.
7. The risk engine can reduce or reject an intent; it cannot be overridden by the model.
8. An adapter submits and reconciles a marketable limit order.
9. Telemetry records the full decision, including no-trade decisions and rejected orders.
10. Offline jobs create labels, run walk-forward evaluation, and train challengers. A separate promotion gate selects the next frozen champion.

## 1. Information Received

### Decision features

All features are point-in-time and carry an observation timestamp, source timestamp when available, and quality flag.

SPY features:

- One-, five-, fifteen-, and thirty-minute log returns.
- Five-, fifteen-, and thirty-minute realized volatility.
- Distance from session VWAP.
- Current volume and rolling volume z-score.
- Underlying bid/ask spread when available.
- Minutes since open and minutes until close.

Option features:

- Contract right, strike, expiration, and multiplier.
- Log moneyness and calendar DTE.
- Bid, ask, midpoint, spread dollars, and spread as a fraction of midpoint.
- Quote age and quote-quality flags.
- One-, five-, and fifteen-minute option returns when continuous observations exist.
- Implied volatility and delta, gamma, theta, and vega, with a flag identifying whether each value came from the feed or a LEAN price model.
- Bid/ask size and prior-day open interest only when the source supplies them reliably. They are not required features in the initial champion.

Operational features:

- Data-feed identity and entitlement class.
- Decision time and model identifier.
- Current position, pending order, number of entries, realized daily P&L, high-water mark, and circuit-breaker state.

### News and popular-thread data

News and social data are shadow-only in version one. Each item must retain source, publication timestamp, first-seen timestamp, retrieval timestamp, canonical URL, content hash, and license/usage classification. The shadow pipeline may compute sentiment, novelty, topic, and attention features, but those features cannot affect an order until they have a point-in-time history and pass the same walk-forward and ablation gates as market features.

No page is scraped when its terms or rights do not permit collection or model use. Deleted or edited social content is not silently rewritten in historical records. A source that cannot be reconstructed point-in-time is excluded from causal backtests.

## 2. Outcome Predicted

The primary target is the executable net return of buying one candidate option at its ask and selling it 30 minutes later at its bid:

net_return = (exit_bid - entry_ask - per_share_costs) / entry_ask

Per-share costs include configured commissions, exchange/regulatory fees, and a configurable adverse-execution buffer. An observation receives no label if the entry ask or exit bid is absent, non-positive, stale, crossed, or outside regular trading hours.

This target is intentionally option-specific. Predicting only SPY direction ignores implied-volatility movement, theta, gamma, and the bid/ask spread. The model interface returns expected net return, model ID, training cutoff, and feature-schema version for each candidate.

Initial challengers are deliberately small:

- A zero-return/no-trade benchmark.
- Fixed momentum and mean-reversion rules.
- A regularized linear regressor.
- A histogram gradient-boosted regressor from scikit-learn.

Deep learning, reinforcement learning, language-model agents, and signature models remain research candidates. They do not enter version one unless a simpler model first establishes that the labels contain stable net-of-cost signal.

## 3. Call, Put, or Cash Selection

Candidate generation is deterministic and precedes model scoring. A contract is eligible only when:

- DTE is between 7 and 21 calendar days.
- Absolute delta is between 0.40 and 0.60.
- Bid and ask are positive and ask is greater than bid.
- Spread is no greater than the larger of $0.05 or 8% of midpoint.
- Live quote age is no more than five seconds. Historical minute data must contain a quote for the decision minute.
- The contract can be purchased within the premium-risk cap.
- The system is inside the entry window and all operational health checks pass.

For each right, the selector keeps the candidate closest to 0.50 absolute delta, breaking ties by lower spread percentage and then nearer expiration. The model scores the selected call and put. The action is cash unless the largest predicted net return exceeds a threshold chosen only from training/validation data and remains positive under the configured adverse-cost scenario.

Orders use a marketable-limit policy. Entry starts no higher than the current ask and is canceled if it cannot fill inside the configured timeout. The system never turns a stale unfilled intent into an unrestricted market order.

## 4. Position Closing

The normal exit is 30 minutes after the complete entry fill. The adapter submits a sell limit priced to execute at or above the current bid, reconciles partial fills, and records every replacement.

An early exit is allowed only for deterministic safety conditions:

- Market-data staleness or loss of the required stream.
- Broker/reconciliation state inconsistent with the local ledger.
- Model or feature-schema integrity failure.
- Daily loss circuit breaker.
- Explicit operator kill switch.

At 15:35 the system cancels non-exit open orders and begins forced reconciliation. At 15:40 it sends the brokerage-supported liquidation order needed to be flat and raises an alert if the position remains. No new entry can be submitted while an entry or exit ticket is pending.

## 5. Money at Risk

Risk rules are deterministic, versioned, and evaluated before every order:

- At most one option position and one pending entry across the account.
- At most one contract during the initial 30 paper sessions.
- Premium paid plus configured costs may not exceed 0.5% of virtual net liquidation value.
- At most three completed entries per session.
- Stop opening positions after realized plus conservative marked daily P&L reaches -1.0% of session-start equity.
- Disable new positions when peak-to-current drawdown reaches 5.0%.
- Never use borrowing, short options, or an expected stop fill to justify size.
- If one eligible contract exceeds the risk cap, the correct action is cash.

The paper runner maintains a virtual equity ledger so a broker's large default paper balance cannot create unrealistic sizing. Missing, stale, NaN, or contradictory risk inputs fail closed.

## 6. Retraining and Promotion

Training is offline and may not mutate the active model during a market session.

- A challenger job runs weekly after the final session of the week.
- Training data end at the logged cutoff; later data are inaccessible to that run.
- Splits are chronological by session. Overlapping 30-minute labels are purged at boundaries, followed by a 30-minute embargo.
- Feature scaling and all threshold selection are fitted inside each training fold.
- A final six-month period remains untouched until model and policy choices are frozen.
- Every artifact stores source-data manifest hashes, feature schema, parameters, library versions, training cutoff, random seed, and model hash.

A challenger is promoted only when all of the following hold on net-of-cost out-of-sample results:

- Positive expectancy under base and adverse-cost scenarios.
- Better expectancy than the zero-return, momentum, and mean-reversion baselines.
- Profit factor above 1.10.
- Maximum drawdown below 5% under the fixed risk policy.
- No single month supplies more than half of total positive P&L.
- No missing-data request or look-ahead audit failure.
- Results remain positive when the decision threshold is perturbed around its selected value.

Failing promotion leaves the current champion unchanged. The first champion is the no-trade model until a challenger passes.

## Data and Fidelity Tiers

### Tier 0: deterministic fixtures

Synthetic and bundled LEAN option data prove parsing, chain selection, labels, order lifecycle, risk rejection, and liquidation behavior. They provide no evidence of economic performance.

### Tier 1: free prototype data

Alpaca Basic provides limited option history beginning in February 2024 and an Indicative options feed. Alpaca documents that Indicative quotes are modified rather than actual OPRA quotes and that Indicative trades are delayed. Tier 1 is suitable for downloader validation, feature plumbing, model mechanics, and paper operation. All reports must be marked INDICATIVE_DATA and may not support a real-money promotion.

### Tier 2: genuine point-in-time OPRA history

Credible economic evaluation requires genuine historical bid/ask data, contract reference data, and corporate-action coverage. This will likely require a paid or separately licensed source. Adding Tier 2 is a future explicit budget decision, not hidden inside the free prototype.

## Validation Strategy

Validation has four independent layers:

1. Unit tests cover timestamp rules, labels, candidate ranking, risk limits, state transitions, and fail-closed behavior.
2. Replay tests feed the same normalized events to the LEAN and Alpaca adapters and require identical intents and risk decisions.
3. LEAN backtests validate order timing, no overlapping positions, scheduled liquidation, quote-aware fills, and result telemetry.
4. Paper shadow/live tests compare requested, simulated, and broker-reported fills without using paper P&L as training data until the weekly cutoff.

Backtests use entry at ask and exit at bid at minimum. Base, adverse, and severe cost scenarios are always reported. Midpoint-only performance is diagnostic and cannot pass promotion.

Primary research metrics are trade count, coverage, net expectancy, median trade, hit rate, profit factor, turnover, maximum drawdown, time under water, calibration by prediction bucket, and performance by month/time-of-day/volatility regime. Sharpe and Sortino are secondary because option returns are skewed and the number of trades may be small.

## Failure Handling

- Data unavailable, stale, crossed, or unentitled: hold cash; log a structured rejection.
- Feature or model schema mismatch: hold cash; quarantine the artifact.
- Broker disconnected: submit no entry; reconcile before resuming.
- Unknown order state: block entries and query the broker until resolved.
- Partial entry fill: treat filled quantity as the position and cancel the remainder after timeout.
- Exit rejected: retry only through the bounded liquidation policy and alert.
- Process restart: reconstruct holdings and orders from the broker before enabling decisions.
- Clock disagreement or market closed: hold cash.

## Observability and Audit Trail

The append-only audit trail is keyed by decision ID. Each immutable DecisionRecord includes event time, source timestamps, raw-data manifest, effective configuration and hash, feature values, eligible and rejected candidates with reasons, predictions, proposed/final action, portfolio state, risk checks, model metadata, and code commit. Later OrderEventRecord and FillEventRecord entries carry broker order IDs, replacements, fills, exit reason, and realized P&L under the same decision ID; broker facts are never fabricated in or retroactively written into the earlier decision record.

Secrets are read only from environment variables or the operating-system credential store. They are never logged, stored in TOML, committed, or accepted as command-line arguments. Tests use fake clients and inert credentials.

## Paper Evaluation Gate

Paper execution begins in shadow mode, which generates decisions but sends no orders. Order submission is enabled only after replay parity and forced-liquidation tests pass. The initial paper phase lasts at least 30 completed market sessions. It is evaluated on feed uptime, decision parity, rejection correctness, fill deltas, stale-quote rate, forced-liquidation success, and net P&L after conservative reconstructed costs.

Paper fills are not evidence that equivalent live fills were available. No path to real-money endpoints is included in version one.

## Research Basis and Transfer Limits

The design uses the following evidence without treating any paper as a ready-made bot:

- Bali, Beckmeyer, Moerke, and Weigert predict option returns using option- and stock-level characteristics and find nonlinear models useful out of sample. Their portfolios are cross-sectional and mostly weekly/monthly, not a SPY intraday strategy.
- Lim, Chen, and Yap forecast ten-minute changes in risk-neutral moments and report net profitability primarily for a skewness portfolio. Their instruments are E-mini futures options and their strategy uses multi-leg hedged portfolios, not a long call/put rule.
- Ait-Sahalia, Fan, Xue, and Zhou find very short-horizon stock-return predictability from trade and quote imbalance, but the signal decays with latency and requires higher-fidelity data than a delayed indicative feed.
- Tan, Roberts, and Zohren optimize option-straddle positions end to end and show the importance of turnover-aware objectives. Their study uses end-of-day OptionMetrics data, S&P 100 cross-sections, and static delta-neutral straddles.
- Signature Methods in Finance separates alpha extraction from risk/execution and emphasizes liquidity, volatility, costs, temporal dependence, and regime-sensitive estimation. Signature models remain too complex for the first falsifiable baseline.

Any transfer from SPX, E-mini, single-stock options, daily data, or institutional feeds to intraday SPY options is explicitly a hypothesis to test.

## Implementation Boundary

Custom work lives under OptionsLab. Existing LEAN engine behavior is not modified for version one. The fork exists to pin and audit the engine, add the QCAlgorithm adapter and regression fixtures, and permit later upstream-aware changes if a demonstrated engine seam requires them.

The direct Alpaca runner is paper-only and uses Alpaca's official Python SDK. It shares normalized domain objects and deterministic strategy/risk functions with the LEAN adapter. Adapter parity tests are a release gate.

## References

- QuantConnect LEAN: https://github.com/QuantConnect/Lean
- LEAN Alpaca module validation: https://github.com/QuantConnect/Lean.Brokerages.Alpaca/blob/master/QuantConnect.AlpacaBrokerage/AlpacaBrokerage.cs
- Alpaca historical options data: https://docs.alpaca.markets/us/docs/historical-option-data
- Bali et al., Option Return Predictability with Machine Learning and Big Data: https://doi.org/10.1093/rfs/hhad017
- Lim et al., Intraday Information from S&P 500 Index Futures Options: https://doi.org/10.1016/j.finmar.2018.10.001
- Ait-Sahalia et al., How and When Are High-Frequency Stock Returns Predictable?: https://www.nber.org/papers/w30366
- Tan et al., Deep Learning for Options Trading: https://arxiv.org/abs/2407.21791
- Bayer et al., Signature Methods in Finance: https://doi.org/10.1007/978-3-031-97239-3
