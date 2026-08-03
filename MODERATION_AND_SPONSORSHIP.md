# Moderation, Editorial, and Sponsorship Policy

## Editorial foundation

Kosciusko Community Calendar is a family-friendly local event guide informed by Christian values of truthfulness, human dignity, family, service, stewardship, responsibility, and neighborliness.

Events are evaluated according to their content, age suitability, source credibility, legality, local relevance, and safety. An otherwise eligible event is not excluded solely because of the personal identity of its organizers, performers, or attendees.

The calendar prioritizes:

- family activities, festivals, markets, concerts, sports, classes, and community celebrations
- church events, worship gatherings, service projects, charity events, and faith-based community programs
- youth programs, educational events, local arts, civic events, and public-interest activities
- verified details, clear age labels, and direct source links

The calendar excludes:

- pornography, graphic sexual material, sexually explicit performances, and adult-entertainment content
- illegal activity, scams, malware, phishing, or unsafe sales
- hate promotion, targeted harassment, or credible encouragement of violence
- private gatherings, unverifiable listings, duplicates, and events outside the coverage area

## Sponsored event placement

Sponsored event highlights are optional, not prohibited permanently.

The default configuration keeps sponsorship disabled. Enabling it requires an explicit administrative change in `config/site_policy.yaml`.

When sponsorship is enabled:

1. Every sponsored item must display a clear `Sponsored` label.
2. Sponsored status must not improve the event's organic ranking.
3. A visitor must be able to hide sponsored items.
4. No more than one sponsored item may appear per 20 organic events.
5. Sponsored events must pass the same source, safety, age-rating, content, and duplicate checks as free submissions.
6. Payment never guarantees publication.
7. Sponsorship expires when the event ends.
8. Third-party ad networks, behavioral tracking, and affiliate tracking remain prohibited.

## Missing-event intake

### Current prototype

The V2 prototype creates a public GitHub issue. It is useful for controlled testing, but it is not the recommended production intake for anonymous public submissions because a malicious URL or explicit description becomes public before review.

### Required production model

Production intake should use a private quarantine queue:

1. The visitor submits a URL, event name, audience rating, and optional notes.
2. A server-side endpoint validates that the URL uses HTTP or HTTPS.
3. The endpoint blocks local addresses, private network targets, excessive redirects, oversized responses, and unsupported file types.
4. The URL is checked for malware, phishing, scam, and domain-reputation signals.
5. Only text metadata is extracted for classification. Untrusted scripts, embeds, and downloads are never executed.
6. The submission is classified for age restriction and content risk.
7. A moderator sees a safe text preview, source details, and risk flags.
8. Only a moderator can approve publication.
9. Approved events enter the normal deduplication and event-validation pipeline.
10. Rejected submissions remain out of the public event data and newsletter.

## Review statuses

- `pending`: received but not checked
- `auto-rejected`: clear malware, phishing, invalid protocol, or blocked content
- `needs-safety-review`: adult, violent, gambling, weapons, drugs, hate, or unclear content
- `needs-source-review`: source ownership or event legitimacy is unclear
- `duplicate`: the event already exists
- `approved`: verified and safe for publication
- `rejected`: not suitable for the guide

## Content decisions

| Submission type | Decision |
|---|---|
| All-ages community event with an official source | Allow after verification |
| Church, worship, charity, or faith-based community event | Allow after verification |
| Lawful 13+ event | Allow with age label |
| Lawful 18+ event without explicit content | Manual review, hidden by default |
| Lawful 21+ concert, comedy, or nightlife event without explicit content | Allow with a clear 21+ label |
| Pornography, sexually explicit entertainment, or graphic sexual imagery | Reject |
| Malware, phishing, scam, or illegal activity | Reject |
| Hate promotion, targeted harassment, or credible encouragement of violence | Reject |
| Weapons or illegal drug sales | Reject |
| Gambling promotion | Manual review |
| Unclear or unverifiable source | Hold or reject |
| Event associated with any identity group | Apply the same content, age, source, legality, locality, and safety rules |

## Moderator checklist

A moderator must confirm all of the following before approval:

- The event is real and publicly accessible.
- The source is the organizer, venue, ticket provider, tourism calendar, or another credible public listing.
- Date, time, location, and admission details agree across the source.
- The event is within the geographic coverage area.
- The event is not already present.
- Age restrictions and content warnings are accurately labeled.
- The link does not redirect to malware, phishing, illegal sales, or unrelated content.
- The event complies with the family-friendly content policy.
- The decision is based on event content and safety rather than the personal identity of participants.
- Any sponsored status is clearly labeled and does not affect organic ranking.

## Reviewer safety

Moderators should not open untrusted links directly in their primary browser session. The review tool should show a text-only preview and scan results first. Links requiring a manual visit should open in an isolated browser profile with downloads disabled.
