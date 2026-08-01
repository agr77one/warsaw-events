# Warsaw Events Pipeline

Automated discovery, validation, deduplication, tracking, publishing, and email delivery of substantive public events within roughly 75 miles of Warsaw, Indiana.

## Architecture

- **Crawler:** JSON-LD first, then generic HTML extraction.
- **Tracking:** SQLite database at `data/events.db`.
- **Exports:** CSV and JSON under `output/`.
- **Newsletter:** Markdown and HTML under `output/`.
- **Portal:** Generated static site at `docs/index.html` and deployed with GitHub Pages.
- **Distance-aware ranking:** Events within 10 miles of Warsaw receive the largest
  priority boost, decreasing through the 75-mile coverage area.
- **Sunday email:** Full 14-day newsletter.

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
- `EMAIL_TO`: one recipient or a comma-separated distribution list

Only the Sunday workflow uses these secrets. Workflows still run without them,
but email delivery is skipped.

Recipient addresses remain inside the encrypted Actions secret and are sent via
**Bcc**. The repository and generated outputs do not contain the list, and
newsletter recipients cannot see one another's addresses. GitHub administrators
can replace a secret but cannot reveal its stored value through the repository UI.

## Workflows

- **Daily Event Watch:** every day at 8:00 PM Warsaw time; refreshes the database,
  exports, and portal without sending email.
- **Sunday Newsletter:** Sundays at 9:00 AM Warsaw time; refreshes everything and
  sends the full newsletter email.
- **Deploy Event Portal:** deploys after a successful daily or Sunday workflow,
  and when `docs/` is changed by an ordinary push. The completion trigger is
  required because pushes made with a workflow's `GITHUB_TOKEN` do not start
  another workflow.

Schedules use `America/Indiana/Indianapolis`, so daylight-saving changes are
handled automatically.

Manual Sunday workflow runs default to a no-email test. To intentionally send
during a manual run, enable the `send_email` input when dispatching it.

## Location priority

Each source has an approximate distance from Warsaw, and recognized event cities
override the source-level distance. Priority bonuses are +4 within 10 miles, +3
within 25 miles, +2 within 50 miles, +1 within 75 miles, and zero beyond that or
when distance is unknown. Distance and priority are included in the exports.

## Facebook-only sources

Direct Facebook HTML scraping is intentionally disabled. Facebook commonly
requires login for page content, and Meta's automated-data terms require express
permission for automated collection. Safe source options, in preference order,
are:

1. A venue-owned public website, calendar, RSS feed, or newsletter.
2. A Meta-supported API integration authorized by the Page owner.
3. Manually verified event details supplied by the venue or an editor.

See [Meta's Automated Data Collection Terms](https://www.facebook.com/legal/automated_data_collection_terms).
Do not add a Facebook page URL to `config/sources.yaml` unless collection has
been expressly authorized and implemented through a supported interface.

## Free-tier usage estimate

A measured local crawl takes about 13–18 seconds and produces an artifact of
about 164 KB. The schedule creates roughly 35 crawler jobs and up to 35 Pages
deployment jobs per average month. Conservatively rounding every job up to one
minute gives about **70 runner minutes per month**. With 7-day daily artifact
retention and 30-day newsletter retention, steady retained artifacts are about
**1.85 MB**.

Standard GitHub-hosted runners are free for public repositories. See
[GitHub Actions billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions).

## Manual run

Open **Actions**, select a workflow, and choose **Run workflow**.

The first successful run establishes the baseline. Later runs detect new and changed events.
