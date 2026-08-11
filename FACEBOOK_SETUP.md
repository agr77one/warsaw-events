# Facebook Graph API setup

Warsaw Weekend does **not** scrape Facebook HTML. The optional North Pointe
Cinemas integration uses Meta's supported Graph API and remains disabled until
the repository has an authorized Page access token and Page ID.

## What is already configured

- Meta app: `localevents`
- App ID: `908278155659688`
- Graph API version: `v26.0`
- Repository variable: `FACEBOOK_GRAPH_API_VERSION=v26.0`
- Source: `North Pointe Cinemas Facebook` in `config/sources.yaml`
- Runtime extractor: `extract_facebook_graph` in `pipeline.py`

The app secret is not used by the scheduled scraper and must not be added to the
repository.

## Tested client token result

The supplied Meta **Client Token** was tested in Graph API Explorer on August
11, 2026. The raw token is not a complete OAuth access token and returned error
`190` (`Cannot parse access token`). In Meta's `APP_ID|CLIENT_TOKEN` format, it
successfully identified the `localevents` app but still could not read North
Pointe Cinemas. Meta returned error `100` and required
`pages_read_engagement`, Page Public Content Access, or Page Public Metadata
Access.

A client token therefore cannot be stored as `FACEBOOK_PAGE_ACCESS_TOKEN` and
cannot authorize the event scraper. It is intentionally not saved in GitHub,
the repository, workflow logs, or generated output.

## Authorization requirement

Warsaw Weekend does **not** own or manage North Pointe Cinemas or any other
Facebook Page. A managed-Page token flow therefore does not apply to this
project.

The supported Facebook API route is to submit the `localevents` app for Meta
review for the public Page-content feature and permissions required by the
`/{PAGE_ID}/events` endpoint. Approval is controlled by Meta and is not replaced
by an app ID, app secret, client token, or an ordinary Facebook login.

Until Meta approves that access, keep the Facebook source disabled and use a
venue-owned website, calendar, RSS/iCalendar feed, newsletter, or the public
source-submission form. Automated Facebook HTML scraping is not an approved
fallback for this project.

## Apply for and verify public Page access

1. In the Meta App Dashboard, request App Review for the public Page-content
   feature and every permission Meta requires for reading public Page events.
2. Complete Meta's requested use-case description, screencast, test steps,
   privacy-policy information, and data-handling disclosures.
3. After approval, open [Meta Graph API Explorer](https://developers.facebook.com/tools/explorer/908278155659688/),
   select `localevents` and API version `v26.0`, and use the approved runtime
   token type specified by Meta.
4. Resolve North Pointe Cinemas to its numeric Page ID, then test the exact
   endpoint used by the scraper:

   ```text
   PAGE_ID/events?fields=id,name,description,start_time,end_time,place,cover,ticket_uri,event_times,is_canceled&limit=5
   ```

5. Continue only if this returns a `data` array. An authorization error means
   the approved feature, permission, token type, or app mode is still
   insufficient for this endpoint.

Use Meta's App Review documentation for the current requirements:

- [Page Public Content Access](https://developers.facebook.com/docs/apps/review/feature#reference-PAGES_ACCESS)
- [Facebook Login permissions](https://developers.facebook.com/docs/apps/review/login-permissions)

## Add the values to GitHub

Open **Repository Settings -> Secrets and variables -> Actions -> Secrets** and
create these repository secrets:

| Name | Value |
| --- | --- |
| `FACEBOOK_PAGE_ACCESS_TOKEN` | The Meta-approved runtime access token, with no quotes (the secret name is retained for compatibility) |
| `FACEBOOK_NORTHPOINTE_PAGE_ID` | The numeric North Pointe Page ID, with no quotes |

Do not place either value in YAML, Markdown, a commit, an issue, a pull request,
or an Actions log. GitHub will encrypt the secret values and will not display
them again after saving.

The non-secret repository variable `FACEBOOK_GRAPH_API_VERSION` is already set
to `v26.0`.

## Test the scraper

Run **Actions -> Daily Event Watch -> Run workflow**. Then inspect
`output/source_health.json`:

- `ok`: Graph API returned at least one accepted upcoming event.
- `no_upcoming` or `empty`: authorization worked, but no usable upcoming event
  was returned.
- `not_configured`: one of the two secrets or the API-version variable is
  missing.
- `failed`: inspect the safe Meta error message in source health. Tokens are not
  included in error output.

For an optional local test in PowerShell, set values only for that terminal
process:

```powershell
$env:FACEBOOK_PAGE_ACCESS_TOKEN = "PAGE_TOKEN"
$env:FACEBOOK_NORTHPOINTE_PAGE_ID = "PAGE_ID"
$env:FACEBOOK_GRAPH_API_VERSION = "v26.0"
python pipeline.py --mode manual
```

Remove those temporary environment values when finished:

```powershell
Remove-Item Env:FACEBOOK_PAGE_ACCESS_TOKEN
Remove-Item Env:FACEBOOK_NORTHPOINTE_PAGE_ID
Remove-Item Env:FACEBOOK_GRAPH_API_VERSION
```

## What the extractor does

The extractor requests up to three Graph API pages, validates every response,
skips malformed events, maps recurring dates, venue/address, description, cover
image, cancellation status, and the canonical Facebook event URL. Results then
pass through the normal Warsaw-distance scoring and duplicate consolidation.
Official Facebook data wins over aggregator copies while useful missing details
such as admission or image can still be retained.

If supported Page authorization is not available, use the public correction and
source form or a venue-owned website/calendar. Do not replace this integration
with automated Facebook HTML collection.
