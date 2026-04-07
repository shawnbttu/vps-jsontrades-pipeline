# VPS JSON Trades Pipeline

Pipeline project for exporting live NinjaTrader trade JSON from the VPS and syncing the original `ApolloES` / `Hermes` feed back to the local workspace.

## Purpose

Provide a stable home for the live trade JSON exporter, local pull automation, pipeline notes, and future monitoring/logging improvements without changing the working feed path used elsewhere in the workspace.

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

## Data And Integrations

- Primary data source: VPS NinjaTrader DB at `C:\Users\Administrator\Documents\NinjaTrader 8\db\NinjaTrader.sqlite`
- Transport: SSH / SCP to VPS `104.245.104.71` as `Administrator`
- External systems: Windows Task Scheduler on both local machine and VPS

## Related Docs

- `C:\VScode\vps-jsontrades-pipeline\docs\ARCHITECTURE.md`
- `C:\VScode\vps-jsontrades-pipeline\docs\DECISIONS.md`
- `C:\VScode\vps-jsontrades-pipeline\docs\RUNBOOK.md`
- `C:\VScode\vps-jsontrades-pipeline\CHANGELOG.md`
- `C:\VScode\.codex-contex\VPS ApolloES Hermes JSON Pipeline 2026-04-03.md`
