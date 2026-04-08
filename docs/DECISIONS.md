# Decisions

## Current Decisions

- Project name: `vps-jsontrades-pipeline`
- Keep the original local feed path unchanged: `C:\VScode\Reports\Live\apolloes-hermes-live-trades.json`
- Use VPS user `Administrator`
- Use VPS IP `104.245.104.71`
- Use `py` instead of `python` on the VPS
- Keep root-level task entry points working through compatibility wrappers rather than forcing immediate scheduled-task changes
- Prefer SSH key authentication over password prompts for unattended local pulls
- Keep logging lightweight and append-only in a plain text file under `C:\VScode\Reports\Live\logs`
- Keep `ApolloES` strategy runtime status and broker/database confirmation as separate responsibility layers
- Treat NinjaTrader `Orders` / `Executions` as the source of truth for `BROKER_ACCEPTED` and `EXECUTION_CONFIRMED`
- Do not treat Apollo callback timestamps alone as proof of broker-backed live execution
- Preserve strategy callback timestamps in the feed only as supporting evidence:
  - `nt_order_callback_seen_utc`
  - `nt_execution_callback_seen_utc`
- Keep the final `runtime_statuses` payload compact and site-friendly for future QuantBreach rendering
- Support VPS Python environments without installed `zoneinfo` tzdata by falling back to internal Eastern session-date conversion logic
