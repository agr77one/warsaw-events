# Warsaw Events Pipeline

Automated discovery, validation, deduplication, tracking, publishing, and email delivery of substantive public events within roughly 75 miles of Warsaw, Indiana.

## Architecture

- **Crawler:** JSON-LD first, then generic HTML extraction.
- **Tracking:** SQLite database at `data/events.db`.
- **Exports:** CSV and JSON under `output/`.
- **Newsletter:** Markdown and HTML under `output/`.
- **Portal:** Generated static site at `docs/index.html` and deployed with GitHub Pages.
- **Daily alerts:** Email only when an important new event or material change is found.
- **Friday email:** Full 14-day newsletter.

## Outputs

- `output/events.csv`
- `output/events.json`
- `output/source_health.json`
- `output/daily_alerts.json`
- `output/weekly_newsletter.md`
- `output/weekly_newsletter.html`
- `docs/index.html`
- `data/events.db`

## Required GitHub secrets for email

Add these under **Settings → Secrets and variables → Actions**:

- `EMAIL_USERNAME`: Gmail address used to send
- `EMAIL_APP_PASSWORD`: Google 16-character App Password
- `EMAIL_TO`: newsletter recipient

The workflows still run without these secrets, but email delivery is skipped.

## Workflows

- **Daily Event Watch:** daily at 12:00 UTC, conditional important-event email.
- **Friday Newsletter:** Fridays at 12:00 UTC, full newsletter email.
- **Deploy Event Portal:** deploys the generated `docs/` portal to GitHub Pages.

## Manual run

Open **Actions**, select a workflow, and choose **Run workflow**.

The first successful run establishes the baseline. Later runs detect new and changed events.
