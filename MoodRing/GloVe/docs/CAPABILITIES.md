# Capability index

This index locates existing capabilities; it does not turn isolated components
into an integrated data-to-trading pipeline. Follow links for contracts and commands
rather than treating this page as a second API reference.

| Capability | Current boundary | Authority / entry point |
| --- | --- | --- |
| Executable API workflow | Local scenario → caller-supplied signal → BUY/PASS → simulated entry → mark → close → history; replay save/list/compare | Running `/docs` and `/openapi.json`; [commands](PAPER_TRADING.md) |
| Disposable offline lesson | Owned temporary server, generated token and SQLite ledger; eight progress outcomes; fixed synthetic quotes | [Learning helper](../examples/api_smoke.py), [usage](PAPER_TRADING.md#start-here-disposable-offline-lesson) |
| Offline movement and P&L | Caller-supplied price events and deterministic replay; no live feed required | [Paper demo](../examples/paper_demo.py), [replay implementation](../src/paper_trading/backtest.py), [usage](PAPER_TRADING.md) |
| Universe/market contracts and synthetic jobs | Library contracts, bounded fake orchestration, explicit PostgreSQL persistence and disposable measurement tools; not prediction publishers | [Synthetic demo](../examples/synthetic_market_demo.py), [ingestion modules](../src/ingestion/), [data specifications](03_data_models.md) |
| Manual SEC access | Separate opt-in CLI; root metadata only, no downstream consumers or durable payload writes; live compatibility unproven after failed smoke | [SEC access](SEC_ACCESS.md), [approval plan](SEC_SMOKE_PLAN.md), [diagnostics PR](https://github.com/VitalSheaths/MoodRing/pull/26) |
| Research modules | Features, models and macro workflows are not wired into automated API predictions or trades | [Research source](../src/), [architecture](02_architecture.md) |
| Public identity/deployment | Planned, separately reviewed; no signup, token issuer or supported public hosting | [Product scope](01_prd.md), [delivery gates](04_rules.md) |

## Source of truth

- Running OpenAPI defines HTTP request/response contracts and route availability.
- [Usage guide](PAPER_TRADING.md) defines commands and local learning modes.
- [Product](01_prd.md), [architecture](02_architecture.md), [data](03_data_models.md)
  and [rules](04_rules.md) describe intended scope and approval boundaries.
- Offline mode remains supported after future live integration. Never describe
  fixed fixture refresh as changing prices or silently substitute it after outages.

No SEC or synthetic-universe-to-prediction/trade connection is claimed. Failed or
unvalidated metadata must not be connected to trading. Diagnostic improvements do
not authorize limit increases, another live request or broader integration.
