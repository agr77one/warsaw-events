# Warsaw Events Pipeline

Automated discovery, validation, deduplication, tracking, publishing, and email delivery of public events within roughly 75 miles of Warsaw, Indiana. The collection is intentionally comprehensive: library programs, classes, markets, performances, sports, festivals, family activities, and community events are all included.

## Architecture

- **Crawler:** JSON-LD first, configurable calendar-card extraction second, and linked official event pages where needed. A curl fallback handles public calendars that reject ordinary HTTP clients.
- **Community submissions:** a public Google Form feeds a private moderation workbook; only approved event fields are mirrored into a separate public CSV feed for the crawler.
- **Tracking:** SQLite database at `data/events.db`.
- **Exports:** CSV and JSON under `output/`.
- **Newsletter:** Markdown and HTML under `output/`.
- **Portal:** Generated responsive site at `docs/index.html`, deployed with GitHub Pages, with search plus distance, date, and category filters.
- **Warsaw-first ranking:** Warsaw and Winona Lake appear first, followed by events within 25 miles, then regional events. Official sources outrank community indexes when duplicate listings are found.
- **Sunday email:** A polished 14-day guide organized into closest-to-home, nearby, and regional sections.

## Submit and review an event

Use the [Warsaw Weekend event form](https://docs.google.com/forms/d/e/1FAIpQLSf3XuV_y1QgqL9byWZYKt0Q_TrEGBKU1k0b4Pv7_qF7Au7Rfg/viewform) to suggest a missing event. New submissions enter the private review queue as `Pending`. An owner checks the official link, date, time, venue, address, price, and local relevance, then chooses one of these statuses:

- `Pending`: waiting for review
- `Needs Information`: the submitter or organizer must clarify a detail
- `Approved`: eligible for the public feed and the next site update
- `Rejected`: not published
- `Withdrawn`: removed from the public feed at the next successful update

The public workbook contains only the submission ID and event details. Submitter names, email addresses, reviewer identity, and review notes remain in the private workbook. The crawler reads the sanitized [approved-event CSV feed](https://docs.google.com/spreadsheets/d/1Yh1bXAiwe_ArXnINUhSSZyWbDWXu9Dz_W4Lg0t_HyYI/gviz/tq?tqx=out:csv&sheet=Approved%20Events) during the 8:00 PM daily run and Sunday newsletter run. A failed feed request leaves previously published community events untouched; a successful feed removes items that are no longer approved.

`COMMUNITY_EVENTS_FEED_URL` can override the built-in public feed URL for testing or migration. It is not a secret and must point only to a sanitized CSV with the documented event columns.

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
- `output/historical_validation.json`
- `output/historical_validation.md`
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

Direct Facebook HTML scraping remains disabled. Facebook currently presents the
North Pointe Cinemas public page behind a login dialog, and Meta's automated-data
terms require express permission for automated collection. The pipeline now has
an optional **Meta Graph API** extractor for North Pointe Cinemas. It maps event
names, recurring times, venue/address, description, cover image, cancellation,
admission text, and the canonical Facebook event link without scraping HTML.

To activate it, obtain API access authorized by the Page owner (or Meta-approved
access to public Page content), then configure:

- Actions secret `FACEBOOK_PAGE_ACCESS_TOKEN`
- Actions secret `FACEBOOK_NORTHPOINTE_PAGE_ID`
- Actions variable `FACEBOOK_GRAPH_API_VERSION`, set to the version approved for
  the Meta app

Until all three values exist, source health reports `not_configured`; daily and
Sunday jobs continue normally and do not attempt a Facebook HTML request. Never
paste the token into `config/sources.yaml`, workflow YAML, an issue, or a log.

Safe source options, in preference order, remain:

1. A venue-owned public website, calendar, RSS feed, or newsletter.
2. A Meta-supported API integration authorized by the Page owner.
3. Manually verified event details supplied by the venue or an editor.

See [Meta's Automated Data Collection Terms](https://www.facebook.com/legal/automated_data_collection_terms).
Do not add a Facebook page URL to `config/sources.yaml` unless collection has
been expressly authorized and implemented through a supported interface.

## Historical validation

`historical_audit.py` reconstructs the previous two complete calendar months
from preserved official LibraryCalendar daily feeds, the Lake City Skiers iCal
feed, Wagon Wheel detail pages, and the Kosciusko County Fair archive. It compares
official record counts, field completeness, and a fixed set of exact historical
title/date/time checks without adding past events to the live dashboard.

This is a local-official-source validation set, not a claim that every regional
website preserves a complete public archive. Aggregators and several tourism/news
sites remove past listings, so they cannot be independently back-tested after the
fact. Their future collection remains covered by normal source health and output
tests.

Run the August 1, 2026 baseline with:

```powershell
python historical_audit.py --as-of 2026-08-01
```

The resulting evidence is written to `output/historical_validation.json` and
`output/historical_validation.md`.

## Free-tier usage estimate

The expanded crawl currently checks 22 web/calendar sources plus one optional
Facebook Graph API source. A measured full run completed in about 80 seconds and
produced more than 470 upcoming events. The schedule creates roughly 35 crawler
jobs and 35 Pages deployments per average month. Budgeting two minutes per crawler
plus one minute per deployment gives about **105 runner minutes per average month**
(108 in a 31-day month with five Sundays). With 7-day daily retention and 30-day
newsletter retention, steady compressed artifact storage should remain around
**10 MB**.

Standard GitHub-hosted runners are free for public repositories. See
[GitHub Actions billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions).

## Manual run

Open **Actions**, select a workflow, and choose **Run workflow**.

The first successful run establishes the baseline. Later runs detect new and changed events.
