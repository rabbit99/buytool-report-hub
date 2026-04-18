# BuyTool UI Version History

## v0.4.1-dashboard-navigation-timeline (2026-04-18)

Scope:
- Added cross-vendor switch buttons on dashboard overview pages.
- Added recent update timeline module based on ingest metadata.
- Kept KPI cards and category navigation as primary dashboard controls.

Notes:
- Dashboard home is now easier to browse across vendors and update windows.

## v0.4.0-ui-dashboard-overview (2026-04-18)

Scope:
- Added dashboard-style overview page output as 00_總覽.html for each vendor report set.
- Introduced KPI cards (message volume, category coverage, participant estimate, top category).
- Added category navigation buttons linking directly to section report pages.
- Added source coverage and category statistics tables in dashboard layout.

Notes:
- Generated with `gen_report.py --all --html` and optimized for desktop/mobile browsing.

## v0.3.0-ui-foundation (2026-04-18)

Scope:
- Imported design resource spec from LinHelp into project docs.
- Upgraded report HTML style to panel-based visual hierarchy.
- Added responsive layout tuning for desktop/mobile report reading.
- Stabilized task and npm execution paths around workspace venv usage.

Notes:
- This version is the baseline before introducing dashboard-style overview pages.
