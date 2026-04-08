# Runbook

## Local Manual Pull

```powershell
powershell -ExecutionPolicy Bypass -File C:\VScode\vps-jsontrades-pipeline\scripts\pull_vps_apolloes_hermes_json.ps1 -VpsHost 104.245.104.71 -KeyPath C:\Users\tanve\.ssh\id_ed25519_vps_nt
```

## VPS Export Command

```bat
py "C:\trade-export\live_strategy_trade_export.py" --db-path "C:\Users\Administrator\Documents\NinjaTrader 8\db\NinjaTrader.sqlite" --output "C:\trade-export\out\apolloes-hermes-live-trades.json" --watch --poll-seconds 2
```

## VPS Hidden Startup Task

Active hidden task entry point:

```text
wscript.exe C:\trade-export\run_hidden.vbs
```

Wrapper behavior:

- `run_hidden.vbs` launches `cmd /c C:\trade-export\vps_run_apolloes_hermes_export.cmd` with hidden window mode
- the `.cmd` script starts the Python watcher and restarts it after failures

## VPS Runtime Inputs

- NinjaTrader DB:
  - `C:\Users\Administrator\Documents\NinjaTrader 8\db\NinjaTrader.sqlite`
- Apollo runtime-status files:
  - `C:\trade-export\runtime-status`
- Private hot writer output:
  - `C:\trade-export\out\apolloes-hermes-live-trades.writer.json`
- Public mirrored output:
  - `C:\trade-export\out\apolloes-hermes-live-trades.json`

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

## VPS Health Checks

- Confirm exporter task:
  - `schtasks /Query /TN "ApolloES Hermes Live Exporter" /V /FO LIST`
- Confirm exporter log:
  - `Get-Content C:\trade-export\out\apolloes-hermes-live-exporter.log -Tail 20`
- Confirm private hot writer timestamp:
  - `C:\trade-export\out\apolloes-hermes-live-trades.writer.json`
- Confirm VPS public output JSON timestamp:
  - `C:\trade-export\out\apolloes-hermes-live-trades.json`
- Confirm runtime-status files are refreshing:
  - `C:\trade-export\runtime-status`

## Runtime Status Verification

Check the final merged JSON, not only the per-account Apollo runtime file.

Expected final per-account fields:

- `status_code`
- `status_message`
- `order_sent_utc`
- `broker_accepted_utc`
- `execution_confirmed_utc`
- `nt_order_callback_seen_utc`
- `nt_execution_callback_seen_utc`
- `desync_suspected`
- `broker_acceptance_issue`

Expected hybrid interpretation:

- `ORDER_SENT`
  - Apollo sent the order, but DB confirmation has not arrived yet
- `BROKER_ACCEPTED`
  - a matching broker/order row exists in NinjaTrader `Orders`
- `EXECUTION_CONFIRMED`
  - a matching execution row exists in NinjaTrader `Executions`
- `BROKER_ACCEPTANCE_ISSUE`
  - Apollo reported `ORDER_SENT`, but no acceptable DB order row appeared in time
- `DESYNC_SUSPECTED`
  - Apollo runtime and persisted DB state disagree in a suspicious way

## Failure Checks

1. Confirm SSH key still works:
   - `ssh -i C:\Users\tanve\.ssh\id_ed25519_vps_nt Administrator@104.245.104.71`
2. Confirm VPS source file exists:
   - `C:\trade-export\out\apolloes-hermes-live-trades.json`
3. Confirm the private writer is still updating even if the public file is temporarily locked:
   - `C:\trade-export\out\apolloes-hermes-live-trades.writer.json`
4. Review `C:\trade-export\out\apolloes-hermes-live-exporter.log` for `public publish deferred` versus a full watcher crash
5. Review local pull log for the latest `FAILURE` line
6. If the VPS exporter crashed after replacing the script, re-check for Python environment issues and restart the task:
   - `schtasks /Run /TN "ApolloES Hermes Live Exporter"`
7. If the VPS Python install lacks IANA tzdata, use the current exporter version with built-in Eastern fallback logic rather than older `ZoneInfo("America/New_York")`-only builds
