# OptionsLab prerequisite and experiment status

Recorded 2026-09-05 after the user approved the research review and requested implementation. This record distinguishes permission to build the core from evidence needed to trade or make economic claims.

| Item | Current decision or evidence | Gate |
|---|---|---|
| Core contracts | Adopted reviewed design and amended seven-task roadmap; small behavior increments supersede the large types-first delivery task | Core work authorized |
| Required features and Greeks | Retain exact completed-bar calculations, separate Greek provenance/availability, cash on missing inputs, and all reviewed candidate limits | Semantics fixed; real provider readiness unverified |
| Target and selection | Attempted policy return on decision ask capital K; ask-to-bid execution, explicit costs, no-fill/censoring distinction; conservative expected dollars for one contract; ties/unknown uncertainty choose cash | Fixed in adopted spec |
| Exits and risk | Preserve one contract and require `K + max($1.00, configured round-trip fee floor, applicable round-trip fee estimate) + max(0.005 × K, 100 × current option spread) <= 0.005 × virtual equity`. Preserve the 1% daily loss, 5% drawdown, three entries, fixed hold and liquidation rules; entry rejection cannot suppress recovery | Fixed; implementation pending |
| Initial virtual equity | **USD 150,000**, selected on 2026-09-05 after the user delegated the choice. This is a declared simulation balance, separate from broker cash and test fixtures | Capital declared; actual eligible-contract affordability remains unmeasured |
| Prototype budget | USD 0; no paid data purchase authorized or made | Fixed |
| Initial data admission | Self-generated synthetic fixtures only, explicitly identified as such; no external source admitted | Core tests permitted |
| Bundled LEAN data | Paths include SPY minute quote/trade files for 2023-08-03 and eight daily universe dates from 2023-12-28 through 2024-01-09 | Not admitted; presence proves neither rights nor required coverage |
| Historical quotes | No endpoint/sample evidence demonstrating the registered history, synchronized quote sides/sizes and execution timing | Economic backtesting blocked |
| Rights | No source-specific storage, normalization and Python training permission manifest | External datasets unadmitted |
| Greek enrichment | No verified external historical or operational method/input availability record | External entry readiness missing |
| Study protocol | Reviewed rolling development/final windows, trial control and cost gates retained. Concrete dataset IDs, calendar dates, trial registry and final end date must be frozen before the first economic run | No study run or performance claim |
| Broker readiness | No account, credential, subscription or endpoint capability probe performed | Paper orders blocked |
| Operational gates | Core, parity, stress, at least 10 shadow sessions, then separate paper evaluation requirements remain in adopted design | Not satisfied by this first increment |
| Future live use | Outside v1 scope; no live route or authorization | Disabled |

To admit an external source, record product/endpoint/feed, sample and coverage dates, contract reference/deliverable history, source/availability times, quote-side size semantics, Greek provenance, and evidence of permitted storage/normalization/training use. A source cannot be relabeled synthetic to avoid these checks. Indicative or delayed data can only support an explicitly admitted plumbing experiment.

Each synthetic fixture records its generator/version in its test or fixture manifest. When fixtures cross a replay/artifact boundary, their canonical content hash and synthetic provenance travel with them. The initial unit tests generate values directly; no downloaded market dataset or pricing history enters the package.

No account-wide authorization follows from `PremiumBudget.affordable`. The later authorization transaction must bind current observations, session, model/configuration, account revision and durable reservation, and revalidate at submission. Complete actual endpoint restrictions and independent exit/recovery checks before any paper runner exists.

The initial USD 150,000 virtual balance gives a USD 750 premium-plus-cost ceiling, USD 1,500 session loss threshold and USD 7,500 initial drawdown threshold. Subsequent thresholds follow the reviewed current-equity, session-start-equity and persistent-high-watermark rules. The choice is a round simulation balance that accommodates the known synthetic example; it is not a claim about current option prices or a recommendation to fund a real account. No implicit default capital is added to the policy function.
