# Runbook

## Local Manual Pull

```powershell
powershell -ExecutionPolicy Bypass -File C:\VScode\vps-jsontrades-pipeline\scripts\pull_vps_apolloes_hermes_json.ps1 -VpsHost 104.245.104.71 -KeyPath C:\Users\tanve\.ssh\id_ed25519_vps_nt
```

## VPS Export Command

```bat
py "C:\trade-export\live_strategy_trade_export.py" --db-path "C:\Users\Administrator\Documents\NinjaTrader 8\db\NinjaTrader.sqlite" --output "C:\trade-export\out\apolloes-hermes-live-trades.json" --watch --poll-seconds 2
```

## Local Silent Scheduled Task Entry Point

```text
wscript.exe C:\VScode\vps-jsontrades-pipeline\tools\run_pull_vps_apolloes_hermes_json_hidden.vbs
```

## Local Health Checks

- Confirm target JSON timestamp:
  - `C:\VScode\Reports\Live\apolloes-hermes-live-trades.json`
- Confirm pull log:
  - `C:\VScode\Reports\Live\logs\pull_vps_apolloes_hermes_json.log`
- Confirm scheduled task:
  - `schtasks /Query /TN "Pull VPS ApolloES Hermes JSON" /V /FO LIST`

## Failure Checks

1. Confirm SSH key still works:
   - `ssh -i C:\Users\tanve\.ssh\id_ed25519_vps_nt Administrator@104.245.104.71`
2. Confirm VPS source file exists:
   - `C:\trade-export\out\apolloes-hermes-live-trades.json`
3. Review local pull log for the latest `FAILURE` line
