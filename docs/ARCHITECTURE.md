# Architecture

## Overview

The pipeline has two runtime halves:

1. VPS export
   The live NinjaTrader instance writes executions into its local `NinjaTrader.sqlite`. A Python exporter watches that DB and atomically rewrites the `ApolloES` / `Hermes` JSON feed on the VPS.

2. Local pull
   The local machine uses SSH/SCP with a dedicated key to pull the VPS JSON into the canonical workspace path consumed by local tools and downstream reporting.

## Main Flow

1. NinjaTrader on VPS updates `C:\Users\Administrator\Documents\NinjaTrader 8\db\NinjaTrader.sqlite`
2. VPS exporter rewrites `C:\trade-export\out\apolloes-hermes-live-trades.json`
3. Local scheduled task runs a hidden VBScript wrapper
4. The wrapper launches the PowerShell pull script silently
5. The pull script copies the VPS JSON into `C:\VScode\Reports\Live\apolloes-hermes-live-trades.json`
6. Success and failure receipts are appended to `C:\VScode\Reports\Live\logs\pull_vps_apolloes_hermes_json.log`

## Compatibility Layer

The legacy root-level paths remain in place:

- `C:\VScode\tools\pull_vps_apolloes_hermes_json.ps1`
- `C:\VScode\tools\run_pull_vps_apolloes_hermes_json_hidden.vbs`

Those are compatibility entry points so existing scheduled tasks and operator habits keep working while the project folder becomes the source of truth.
