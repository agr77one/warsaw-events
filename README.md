# Warsaw Events Pipeline

Automated discovery, validation, deduplication, tracking, publishing, and email delivery of public events within roughly 75 miles of Warsaw, Indiana. The collection is intentionally comprehensive: library programs, classes, markets, performances, sports, festivals, family activities, and community events are all included.

## Architecture

- **Crawler:** JSON-LD first, configurable calendar-card extraction second, and linked official event pages where needed. A curl fallback handles public calendars that reject ordinary HTTP clients.
- **Tracking:** SQLite database at `data/events.db`.
- **Exports:** CSV and JSON under `output/`.
- **Newsletter:** Markdown and HTML under `output/`.
- **Portal:** Generated responsive site at `docs/index.html`, deployed with GitHub Pages, with search plus distance, date, and category filters.
- **Warsaw-first ranking:** Warsaw and Winona Lake appear first, followed by events within 25 miles, then regional events. Official sources outrank community indexes when duplicate listings are found.
- **Sunday email:** A polished 14-day guide organized into closest-to-home, nearby, and regional sections.

## Coverage

The configured source set starts with Warsaw Community Public Library, Downtown Warsaw, The Village at Winona, Wagon Wheel, and Warsaw/Winona Lake event indexes. It then expands through Rochester, Plymouth, Goshen, Wabash, Elkhart, Shipshewana, Fort Wayne, and South Bend.

Official venue and tourism calendars receive confidence grade A. Local reporting receives B, and public community indexes receive C. Grade C fills gaps in JavaScript-only and Facebook-first calendars but is clearly labeled on the portal; users should always confirm details at the linked page.

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

Each source has an approximate distance from Warsaw, and recognized event cities override the source-level distance. Proximity bonuses are weighted strongly enough that local events cannot be displaced by a larger regional festival merely because that festival has a more prominent title. Distance, confidence, category, and priority are included in the exports.

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

The expanded crawl currently checks 22 sources and follows a small number of official venue detail links. A measured full run completed in about 27 seconds and produced more than 350 upcoming events. The schedule creates roughly 34 crawler jobs and 34 Pages deployments per average month. Conservatively budgeting two minutes per crawler plus one minute per deployment gives about **102 runner minutes per month**. With 7-day daily retention and 30-day newsletter retention, steady compressed artifact storage should remain well under **10 MB**.

Standard GitHub-hosted runners are free for public repositories. See
[GitHub Actions billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions).

## Manual run

Open **Actions**, select a workflow, and choose **Run workflow**.

The first successful run establishes the baseline. Later runs detect new and changed events.
