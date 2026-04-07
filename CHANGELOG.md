# Changelog

## 2026-04-03

- Created the `C:\VScode\vps-jsontrades-pipeline` project as the canonical home for the live VPS-to-local JSON trade feed.
- Added project-owned copies of the working exporter, all-strategies exporter, local pull script, VPS launcher, and hidden local runner.
- Kept the existing root-level entry points alive as compatibility wrappers so the already-working scheduled tasks do not need to change.
- Added local pull logging to `C:\VScode\Reports\Live\logs\pull_vps_apolloes_hermes_json.log`.
