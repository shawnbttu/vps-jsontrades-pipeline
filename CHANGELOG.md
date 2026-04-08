# Changelog

## 2026-04-07

- Extended `scripts\live_strategy_trade_export.py` to load both NinjaTrader `Orders` and `Executions`, merge Apollo runtime-status files, and publish compact hybrid `runtime_statuses` for downstream consumers.
- Added DB-backed broker truth states:
  - `BROKER_ACCEPTED`
  - `EXECUTION_CONFIRMED`
  - `BROKER_ACCEPTANCE_ISSUE`
- Kept Apollo strategy-owned states separate from broker truth so the final feed no longer treats strategy callbacks as proof of live broker execution.
- Added runtime-status fallback session-date handling for VPS Python installs without `zoneinfo` tzdata support.
- Verified the new hybrid model against uploaded April 7 VPS artifacts: accounts with internal Apollo callbacks but no persisted DB confirmation now resolve to `BROKER_ACCEPTANCE_ISSUE` or `DESYNC_SUSPECTED` instead of false execution-confirmed states.
- Hardened public-file replacement against transient Windows locks by retrying atomic replace before failing.
- Split the VPS export flow into:
  - private hot writer: `C:\trade-export\out\apolloes-hermes-live-trades.writer.json`
  - public mirror: `C:\trade-export\out\apolloes-hermes-live-trades.json`
- Changed the exporter so public-file lock collisions are logged as deferred publish failures instead of crashing the watcher.
- Standardized the VPS startup flow around the hidden wrapper `wscript.exe C:\trade-export\run_hidden.vbs`.

## 2026-04-03

- Created the `C:\VScode\vps-jsontrades-pipeline` project as the canonical home for the live VPS-to-local JSON trade feed.
- Added project-owned copies of the working exporter, all-strategies exporter, local pull script, VPS launcher, and hidden local runner.
- Kept the existing root-level entry points alive as compatibility wrappers so the already-working scheduled tasks do not need to change.
- Added local pull logging to `C:\VScode\Reports\Live\logs\pull_vps_apolloes_hermes_json.log`.
