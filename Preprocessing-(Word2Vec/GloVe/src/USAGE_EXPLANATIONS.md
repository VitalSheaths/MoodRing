1. EconomicCalendar
Purpose: Central manager for the economic calendar — both recurring/scheduled events (NFP, CPI, ISM, etc.) and ad-hoc ingestion from external sources like RSS.
Key parts:

_RECURRING_RULES: Defines deterministic scheduling rules (e.g., "first Friday of the month at 08:30 ET" for Nonfarm Payrolls).
Helper functions (_nth_weekday_of_month, _resolve_rule, _make_timestamp): Generate future event dates without relying on external APIs.
generate_recurring() & generate_fomc_events(): Create MacroEvent objects for a given month/year.
seed_events(): Persists generated events via DB (upsert).
Ingestion: ingest_from_rss() for Fed press releases.
Query methods: get_upcoming(), get_next_critical(), get_by_category(), etc. — all return lists of MacroEvent.
_row_to_event(): Converts raw DB rows (with JSON strings for lists/dicts) back into immutable MacroEvent dataclasses.

What it does overall: Combines rule-based generation + light external ingestion + convenient querying.
2. EarningsCalendar
Purpose: Manages corporate earnings events with focus on volatility (IV crush) and strategy ideas.
Key parts:

Query methods: get_upcoming(), get_by_ticker(), etc. — return EarningsEvent.
IV analysis: get_iv_crush_history() and compute_expected_move() (straddle-based).
recommend_strategy(): Simple rule-based suggestions (iron condor if consistent crush, straddle if actual moves often beat expectations).
Ingestion: ingest_earnings() using yfinance to pull earnings dates, EPS surprises, etc.
_row_to_event(): DB row → EarningsEvent.

What it does overall: Earnings-specific calendar + post-event analytics + basic trading recommendations.
3. FOMCTracker
Purpose: Specialized tracker for FOMC meetings with deep statement analysis.
Key parts:

Keyword-based sentiment: _HAWKISH_TERMS and _DOVISH_TERMS with weights → score_hawkish_dovish().
generate_statement_diff(): HTML diff between statements (using HtmlDiff).
classify_decision(): Turns rate change into strings like "raise_25", "cut_50".
estimate_rate_probabilities(): Simple model based on market-implied rate.
update_statement(): Enriches a meeting with text, scores, and diff.
Queries and save_meeting() / _row_to_meeting().

What it does overall: Turns raw FOMC data into actionable sentiment + rate decision intelligence.
4. EventImpactAnalyzer
Purpose: Historical market reaction analysis and forward-looking predictions for any macro event type.
Key parts:

analyze_impact(): Computes avg/max/min SPY move, VIX change, collects HistoricalReaction objects. Uses caching.
predict_impact(): Gives expected move, confidence, and trading recommendation based on historical consistency (uses coefficient of variation).
get_sector_sensitivity(), get_surprise_correlation(): Advanced stats.
_pearson(): Simple correlation helper.

What it does overall: Turns past event outcomes into statistical insights and predictions.
5. InsiderFeed
Purpose: Ingestion and querying of insider (Form 4) and congressional trades.
Key parts:

Queries: get_recent(), get_notable(), get_buys_only(), get_by_ticker().
cross_reference_signals(): Links insider buys with other system signals (e.g., bullish ROT signals).
Ingestion: ingest_form4() using SEC EDGAR API (with proper User-Agent).
_parse_edgar_results(): Turns API JSON into InsiderTrade objects.
Stub for congressional trades.

What it does overall: Brings regulatory filings into the system with basic signal-crossing.
6. SeasonalAnalyzer
Purpose: Computes and serves seasonal/monthly patterns for tickers and sectors.
Key parts:

compute_seasonal_patterns(): From monthly returns → SeasonalPattern list (avg return, win rate, best/worst year, etc.).
fetch_ticker_seasonals(): Uses yfinance to get historical monthly data and compute.
Pre-computed baselines: _SECTOR_SEASONAL_BASELINES and _SPY_MONTHLY_AVG.
get_current_bias() and get_rotation_calendar(): Narrative + sector rotation views.

What it does overall: Adds calendar/seasonality overlay to trading decisions.

- Medallion Architecture Boundaries for This Code
These classes sit mostly in the Application / Service layer, not deep in the data lakehouse. They orchestrate logic on top of the underlying data layers.
Here’s how to clearly separate responsibilities so the code respects Bronze → Silver → Gold boundaries (and stays maintainable/scalable):
Layer,Responsibility,What lives here (examples),Who can import what?,Key Rules to Enforce
Bronze,"Raw ingestion only. Store exactly as received from source (API, RSS, yfinance, EDGAR). No cleaning, no dataclasses.","Raw JSON responses, CSV dumps, Parquet files with original structure. Ingestion scripts that call httpx, yfinance, feedparser.",Bronze code never imports macro.types or any *Calendar class.,"Append-only, keep full history + metadata (ingest_time, source_url, raw_payload)."
Silver,"Cleaning, validation, standardization, enrichment. This is where you create the dataclasses.","Transformation jobs: parse raw → validate against ALL_EVENT_TYPES, apply mappings (EVENT_TYPE_CATEGORY, EVENT_TYPE_IMPORTANCE), enrich with sectors. _row_to_event() logic belongs here or in a shared mapper.",Silver can import schemas.py (dataclasses). The *Calendar classes can call Silver services.,"One-time validation + enrichment. Use frozen dataclasses here. Deduplication, type coercion, JSON parsing for lists/dicts."
Gold,"Aggregated, business-ready, consumption-optimized views.","EventImpact objects, SeasonalPattern collections, joined views (e.g., macro event + its historical reaction + sector moves), pre-computed predictions, rotation calendars.",Gold imports schemas and reads from Silver (or materialized Gold tables).,"Heavy aggregation, caching, denormalization for fast queries/dashboards. No raw data access."

LayerResponsibilityWhat lives here (examples)Who can import what?Key Rules to EnforceBronzeRaw ingestion only. Store exactly as received from source (API, RSS, yfinance, EDGAR). No cleaning, no dataclasses.Raw JSON responses, CSV dumps, Parquet files with original structure. Ingestion scripts that call httpx, yfinance, feedparser.Bronze code never imports macro.types or any *Calendar class.Append-only, keep full history + metadata (ingest_time, source_url, raw_payload).SilverCleaning, validation, standardization, enrichment. This is where you create the dataclasses.Transformation jobs: parse raw → validate against ALL_EVENT_TYPES, apply mappings (EVENT_TYPE_CATEGORY, EVENT_TYPE_IMPORTANCE), enrich with sectors. _row_to_event() logic belongs here or in a shared mapper.Silver can import schemas.py (dataclasses). The *Calendar classes can call Silver services.One-time validation + enrichment. Use frozen dataclasses here. Deduplication, type coercion, JSON parsing for lists/dicts.GoldAggregated, business-ready, consumption-optimized views.EventImpact objects, SeasonalPattern collections, joined views (e.g., macro event + its historical reaction + sector moves), pre-computed predictions, rotation calendars.Gold imports schemas and reads from Silver (or materialized Gold tables).Heavy aggregation, caching, denormalization for fast queries/dashboards. No raw data access.
How the Current Classes Fit (and How to Refactor for Clean Boundaries)

EconomicCalendar, EarningsCalendar, InsiderFeed → Primarily Silver + Orchestration
Their ingestion methods (ingest_..., _fetch_...) belong in Bronze or a thin Bronze service.
The conversion to dataclasses (_row_to_event) and rule-based generation (generate_recurring) belong in Silver.
The query methods can stay as a service layer that reads from Silver/Gold tables.
FOMCTracker → Silver + Gold
Statement parsing, hawkish/dovish scoring, diff generation → Silver (enrichment).
Storing enriched FOMCMeeting → Silver.
Historical queries → can pull from Gold.
EventImpactAnalyzer → Gold
This is classic Gold — it consumes cleaned historical events (Silver) and produces aggregates, predictions, correlations, and cached impacts (EventImpact).
Keep computation and caching logic here, but make it read from Silver tables.
SeasonalAnalyzer → Gold
Computing patterns from historical prices and serving baselines/rotation calendars is pure Gold (business insight layer).

- Checklist 
Add/keep create_technical_features_with_confidence in silver.py and ensure features.technical.pipeline writes created_at.
Make create_model_features_view_safe depend on that scored view (or call the helper first).
Use created_at to guarantee that features were available at prediction time; still split by trade_date (or created_at windows) for training/validation.
For DL (MSE loss) use the same chronology rules (no leakage) — you can use walk‑forward / purged K‑fold or simple time splits, but always enforce created_at <= prediction_cutoff.

- Why the helper matters
created_at records when the technical feature row was produced (timestamp when features were computed/upserted).
create_technical_features_with_confidence computes per‑ticker row numbers, warmup flags or a feature_confidence measure, and days since first obs. That makes it easy to:
exclude warmup rows for training,
produce walk‑forward / time-split folds,
detect if a feature was computed after you would have needed it (leakage).
The model view should reference that scored data so the modeling code can easily filter to only rows that were actually available at prediction time.

