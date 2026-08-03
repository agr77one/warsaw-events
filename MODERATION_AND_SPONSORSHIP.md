# Moderation and Sponsorship Policy

## Sponsored event placement

Sponsored event highlights are optional, not prohibited permanently.

The default configuration keeps sponsorship disabled. Enabling it requires an explicit administrative change in `config/site_policy.yaml`.

When sponsorship is enabled:

1. Every sponsored item must display a clear `Sponsored` label.
2. Sponsored status must not improve the event's organic ranking.
3. A visitor must be able to hide sponsored items.
4. No more than one sponsored item may appear per 20 organic events.
5. Sponsored events must pass the same source, safety, age-rating, and duplicate checks as free submissions.
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
| Lawful 13+ event | Allow with age label |
| Lawful 18+ event | Manual review, hidden by default |
| Lawful 21+ concert, comedy, or nightlife event | Allow with a clear 21+ label |
| Sexually explicit entertainment or explicit imagery | Reject |
| Malware, phishing, scam, or illegal activity | Reject |
| Hate or extremist promotion | Reject |
| Weapons or illegal drug sales | Reject |
| Gambling promotion | Manual review |
| Unclear or unverifiable source | Hold or reject |

## Moderator checklist

A moderator must confirm all of the following before approval:

- The event is real and publicly accessible.
- The source is the organizer, venue, ticket provider, tourism calendar, or another credible public listing.
- Date, time, location, and admission details agree across the source.
- The event is within the geographic coverage area.
- The event is not already present.
- Age restrictions and content warnings are accurately labeled.
- The link does not redirect to malware, phishing, illegal sales, or unrelated content.
- The event complies with the content policy.
- Any sponsored status is clearly labeled and does not affect organic ranking.

## Reviewer safety

Moderators should not open untrusted links directly in their primary browser session. The review tool should show a text-only preview and scan results first. Links requiring a manual visit should open in an isolated browser profile with downloads disabled.
