# Architecture

## Overview

The pipeline has three runtime layers:

1. Strategy runtime
   `ApolloES` writes per-account runtime-status JSON files under `C:\trade-export\runtime-status`. These files contain strategy-owned readiness and intent states such as `ALIVE`, `REALTIME_READY`, `WAITING_FOR_ORB`, `ORB_FORMED`, `ORB_SKIPPED`, `ORDER_SENT`, and `DESYNC_SUSPECTED`.

2. VPS export
   The live NinjaTrader instance writes orders and executions into `NinjaTrader.sqlite`. The Python exporter watches that DB, reads the runtime-status files, derives DB-backed broker truth, writes a private hot snapshot, and then best-effort publishes the merged `ApolloES` / `Hermes` public JSON feed on the VPS.

3. Local pull
   The local machine uses SSH/SCP with a dedicated key to pull the VPS JSON into the canonical workspace path consumed by local tools and downstream reporting.

## Main Flow

1. NinjaTrader on the VPS updates `C:\Users\Administrator\Documents\NinjaTrader 8\db\NinjaTrader.sqlite`
2. `ApolloES` writes per-account runtime-status files to `C:\trade-export\runtime-status`
3. The VPS exporter reads:
   - NinjaTrader `Orders`
   - NinjaTrader `Executions`
   - Apollo runtime-status JSON files
4. The exporter derives final compact `runtime_statuses` with hybrid truth:
   - strategy/runtime readiness from Apollo
   - broker acceptance / execution confirmation from the DB
5. The exporter writes the authoritative hot snapshot:
   - `C:\trade-export\out\apolloes-hermes-live-trades.writer.json`
6. The exporter then best-effort publishes the public mirror:
   - `C:\trade-export\out\apolloes-hermes-live-trades.json`
7. Public-file lock collisions are logged and deferred instead of crashing the watcher
8. A local scheduled task runs a hidden VBScript wrapper
9. The wrapper launches the PowerShell pull script silently
10. The pull script copies the VPS JSON into `C:\VScode\Reports\Live\apolloes-hermes-live-trades.json`
11. Success and failure receipts are appended to `C:\VScode\Reports\Live\logs\pull_vps_apolloes_hermes_json.log`

## Hybrid Status Responsibility Split

Apollo runtime owns:

- `ALIVE`
- `REALTIME_READY`
- `WAITING_FOR_ORB`
- `ORB_FORMED`
- `ORB_SKIPPED`
- `ORDER_SENT`
- `DESYNC_SUSPECTED`

The exporter owns broker/database truth:

- `BROKER_ACCEPTED`
- `EXECUTION_CONFIRMED`
- `BROKER_ACCEPTANCE_ISSUE`

This split is intentional so the final feed never treats a strategy callback alone as proof of a broker-backed live execution.

## Compatibility Layer

The legacy root-level paths remain in place:

- `C:\VScode\tools\pull_vps_apolloes_hermes_json.ps1`
- `C:\VScode\tools\run_pull_vps_apolloes_hermes_json_hidden.vbs`

Those are compatibility entry points so existing scheduled tasks and operator habits keep working while the project folder becomes the source of truth.

## VPS Task Model

- The active VPS scheduled task is `ApolloES Hermes Live Exporter`.
- It launches `wscript.exe C:\trade-export\run_hidden.vbs` so the exporter runs silently.
- The VBScript wrapper launches `cmd /c C:\trade-export\vps_run_apolloes_hermes_export.cmd`, which starts the Python watcher and restarts it on failure.

## QuantBreach Interface

`C:\VScode\quantbreach-site-refined` consumes the local mirrored JSON at `C:\VScode\Reports\Live\apolloes-hermes-live-trades.json`.

The new `runtime_statuses` payload is the intended source for future QuantBreach live-readiness surfaces, including:

- ORB readiness / ORB formed visibility
- broker acceptance confirmation
- execution confirmation
- desync / broker-acceptance failure visibility
