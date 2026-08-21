# Kosciusko Community Calendar V2

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

## Brand and editorial direction

The public-facing name is **Kosciusko Community Calendar**.

The guide is family-friendly and informed by Christian values of truthfulness, human dignity, family, service, stewardship, responsibility, and neighborliness. Editorial decisions are based on event content, age suitability, source credibility, legality, local relevance, and safety, not solely on the identity of organizers, performers, or attendees.

A dedicated `about.html` page explains the mission, inclusion priorities, excluded content, submission review, sponsorship policy, and source transparency.

## Advertising and sponsorship

Third-party advertising networks, behavioral analytics, and affiliate tracking are disabled.

Optional sponsored local event highlights are disabled by default. If enabled later, they must be clearly labeled, separated from organic ranking, frequency-limited, hideable by visitors, manually approved, and subject to the same family-friendly content and safety rules as every other event.

## Missing-event submissions

The interface includes a visible **Send missing event link** action. A visitor can provide:

- a public event URL
- an optional event name
- an age or audience rating
- optional verification notes

The current prototype creates a structured public GitHub issue. GitHub sign-in is required. This is acceptable for controlled testing but is not the final anonymous-public intake because an unsafe link would become public before review.

Production intake must use a private quarantine queue with URL safety checks, text-only metadata extraction, age and content classification, duplicate checks, source verification, and explicit moderator approval. Nothing is published automatically.

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
10. The current branding is narrower than the actual Kosciusko County and regional coverage.

## V2 behavior

- uses the Kosciusko Community Calendar name
- includes a dedicated About and editorial standards page
- loads the existing JSON export instead of embedding every event into HTML
- groups results by day
- provides date shortcuts for today, tomorrow, this weekend, 7 days, 30 days, and all dates
- filters by distance, category, admission, and source quality
- displays official, reported, and community source labels
- supports shareable query-string filters
- renders 36 events at a time to avoid an oversized DOM
- opens event details in a dialog while retaining the official source link
- accepts missing-event links through a structured review workflow
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
- About page: `/warsaw-events/v2/about.html`

## Recommended rollout

1. Deploy V2 beside the current portal.
2. Test event filtering and controlled missing-event submission on mobile and desktop.
3. Build the private moderation endpoint before anonymous submissions are promoted.
4. Correct any desired wording, default filters, or category behavior.
5. Promote V2 to the root URL only after validation.
6. Keep the original generated portal available temporarily as `/classic/` during the transition.
