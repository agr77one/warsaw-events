# Warsaw Events V2

## Decision

Keep the GitHub repository as the data and automation platform. Use a ChatGPT Sites-style frontend as the presentation layer.

The existing repository already provides the difficult infrastructure:

- scheduled event discovery
- source validation and confidence grades
- deduplication
- SQLite tracking
- JSON and CSV exports
- email newsletter generation
- GitHub Pages deployment

Replacing that system with a standalone visual site would reduce capability. V2 therefore preserves the pipeline and adds a separate frontend at `/v2/`.

## Non-negotiable product rules

### No advertising

V2 contains no:

- advertising network or ad slot
- sponsored placement
- paid ranking
- affiliate link
- third-party analytics script
- external form or tracking service

A restrictive content-security policy permits only local scripts and styles. Event images may still contain organizer or sponsor artwork because those images are part of the original public event listing, not paid placement on this site.

### Missing-event submissions

The interface includes a visible **Send missing event link** action. A visitor can provide:

- a public event URL
- an optional event name
- optional verification notes

The browser creates a structured GitHub issue in this repository. This keeps submissions public, reviewable, and free from an ad-supported form provider. GitHub sign-in is required to finish submission, and the formatted content is copied to the clipboard as a fallback when browser permissions allow it.

Submissions do not bypass validation, deduplication, source-quality rules, or editorial review.

## Problems with the current portal

1. Hundreds of events are rendered into one large HTML document.
2. The first decision is location section, not the date the user is planning for.
3. Search and filters work, but there is no fast path for today, tomorrow, or the weekend.
4. Cards use substantial vertical space, making comparison slow.
5. Source confidence exists but is visually secondary.
6. Every filter change processes a large pre-rendered DOM.
7. Filter choices cannot be shared as a URL.
8. Event details require leaving the site.
9. There is no structured path for visitors to report a missing event.

## V2 behavior

- loads the existing JSON export instead of embedding every event into HTML
- groups results by day
- provides date shortcuts for today, tomorrow, this weekend, 7 days, 30 days, and all dates
- filters by distance, category, admission, and source quality
- displays official, reported, and community source labels
- supports shareable query-string filters
- renders 36 events at a time to avoid an oversized DOM
- opens event details in a dialog while retaining the official source link
- accepts missing-event links through a structured GitHub issue workflow
- includes a responsive layout and installable web app manifest
- keeps the existing portal available for direct comparison
- omits the specifically excluded combined “Picnic in the Park + Family Movie Night” listing

## Deployment

The Pages workflow copies these generated files into the deployed artifact:

- `output/events.json` to `docs/data/events.json`
- `output/source_health.json` to `docs/data/source_health.json`

After deployment:

- current site: `/warsaw-events/`
- V2 comparison site: `/warsaw-events/v2/`

## Recommended rollout

1. Deploy V2 beside the current portal.
2. Test event filtering and missing-event submission on mobile and desktop.
3. Correct any desired wording, default filters, or category behavior.
4. Promote V2 to the root URL only after validation.
5. Keep the original generated portal available temporarily as `/classic/` during the transition.
