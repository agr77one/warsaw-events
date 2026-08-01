# Warsaw Events Pipeline

Automated discovery, validation, deduplication, tracking, and publishing of substantive public events within roughly 75 miles of Warsaw, Indiana.

## Outputs

- `output/monthly_calendar.md`
- `output/changes.md`
- `output/events.json`
- `output/daily_alerts.json`

## GitHub Actions

- Daily watch runs every morning.
- Friday newsletter runs weekly.
- Manual workflow can be triggered from the Actions tab.

The first successful run establishes the event baseline. Later runs detect new and changed events.
