# VPS JSON Trades Pipeline

Pipeline project for exporting live NinjaTrader trade JSON from the VPS, merging Apollo-owned runtime readiness with DB-backed broker truth, and syncing the finished `ApolloES` / `Hermes` feed back to the local workspace.

## Purpose

Provide a stable home for:

- the live trade JSON exporter on the VPS
- Apollo runtime-status ingestion
- DB-backed broker acceptance / execution confirmation
- local pull automation
- pipeline notes and runbooks

without changing the working feed path used elsewhere in the workspace.

## Status

- State: active
- Owner: local workspace automation
- Primary path: `C:\VScode\vps-jsontrades-pipeline`

## Getting Started

```powershell
# Local one-shot pull
powershell -ExecutionPolicy Bypass -File C:\VScode\vps-jsontrades-pipeline\scripts\pull_vps_apolloes_hermes_json.ps1 -VpsHost 104.245.104.71 -KeyPath C:\Users\tanve\.ssh\id_ed25519_vps_nt

# VPS exporter command
py "C:\trade-export\live_strategy_trade_export.py" --db-path "C:\Users\Administrator\Documents\NinjaTrader 8\db\NinjaTrader.sqlite" --output "C:\trade-export\out\apolloes-hermes-live-trades.json" --watch --poll-seconds 2
```

## Key Paths

- Project scripts: `C:\VScode\vps-jsontrades-pipeline\scripts`
- Project launchers: `C:\VScode\vps-jsontrades-pipeline\tools`
- Local target JSON: `C:\VScode\Reports\Live\apolloes-hermes-live-trades.json`
- Local pull log: `C:\VScode\Reports\Live\logs\pull_vps_apolloes_hermes_json.log`
- VPS runtime-status directory: `C:\trade-export\runtime-status`
- VPS private hot writer JSON: `C:\trade-export\out\apolloes-hermes-live-trades.writer.json`
- VPS public pull JSON: `C:\trade-export\out\apolloes-hermes-live-trades.json`

## Data And Integrations

- Primary data source: VPS NinjaTrader DB at `C:\Users\Administrator\Documents\NinjaTrader 8\db\NinjaTrader.sqlite`
- Apollo runtime-status source: strategy-authored JSON files under `C:\trade-export\runtime-status`
- Transport: SSH / SCP to VPS `104.245.104.71` as `Administrator`
- External systems: Windows Task Scheduler on both local machine and VPS
- Downstream consumer: `C:\VScode\quantbreach-site-refined`

## VPS Task And Publish Model

- The active VPS task is `ApolloES Hermes Live Exporter`.
- It launches `wscript.exe C:\trade-export\run_hidden.vbs` so the exporter stays hidden in the Administrator session.
- The hidden wrapper launches `cmd /c C:\trade-export\vps_run_apolloes_hermes_export.cmd`, which keeps the Python watcher alive and restarts it after failures.
- The exporter now writes the authoritative hot snapshot to `apolloes-hermes-live-trades.writer.json` first, then best-effort publishes `apolloes-hermes-live-trades.json` for SCP/local consumers.

## Hybrid Status Model

`ApolloES` owns strategy/runtime truth and writes per-account status files. The exporter then adds broker/database truth from NinjaTrader `Orders` and `Executions`.

Apollo-owned states:

- `ALIVE`
- `REALTIME_READY`
- `WAITING_FOR_ORB`
- `ORB_FORMED`
- `ORB_SKIPPED`
- `ORDER_SENT`
- `DESYNC_SUSPECTED`

Exporter-owned broker states:

- `BROKER_ACCEPTED`
- `EXECUTION_CONFIRMED`
- `BROKER_ACCEPTANCE_ISSUE`

The final merged `runtime_statuses` objects are intentionally compact so QuantBreach can surface them directly.

## Related Docs

- `C:\VScode\vps-jsontrades-pipeline\docs\ARCHITECTURE.md`
- `C:\VScode\vps-jsontrades-pipeline\docs\DECISIONS.md`
- `C:\VScode\vps-jsontrades-pipeline\docs\RUNBOOK.md`
- `C:\VScode\vps-jsontrades-pipeline\CHANGELOG.md`
- `C:\VScode\.codex-contex\VPS ApolloES Hermes JSON Pipeline 2026-04-03.md`
