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
