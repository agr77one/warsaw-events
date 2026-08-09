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

## Authorization requirement

A Page access token acts on behalf of a Facebook Page that the signed-in user is
allowed to manage. If North Pointe Cinemas does not appear in the signed-in
account's managed Pages, stop: the app cannot mint a Page token for that Page.
Ask the Page owner to grant appropriate Facebook/Business access and authorize
the app, or complete Meta's required review for public Page-content access.

App ID and App Secret alone do not authorize reading another Page's events.
Development-mode tokens also work only for app roles and Pages those accounts
are permitted to manage.

## Obtain and verify the Page token

1. Open [Meta Graph API Explorer](https://developers.facebook.com/tools/explorer/908278155659688/).
2. Select the `localevents` app and API version `v26.0`.
3. Generate a **User access token**. Grant only permissions Meta requires for
   the request. Begin with `pages_show_list` and `pages_read_engagement`. Meta
   may additionally require `pages_read_user_content` or approved public Page
   access for the Page events edge.
4. Run this request in the Explorer:

   ```text
   me/accounts?fields=name,access_token,tasks
   ```

5. Find `North Pointe Cinemas` in the response. Copy its numeric `id` and its
   `access_token`. The returned access token is the Page access token.
6. Before saving anything in GitHub, test the exact endpoint used by the
   scraper:

   ```text
   PAGE_ID/events?fields=id,name,description,start_time,end_time,place,cover,ticket_uri,event_times,is_canceled&limit=5
   ```

7. Continue only if this returns a `data` array. If North Pointe is absent from
   `me/accounts`, or the events request reports an authorization error, the
   Page owner or Meta approval is still required.

Meta's official Facebook API collection documents both the managed-Page token
request and the direct Page-token lookup:

- [Get access tokens for Pages you manage](https://www.postman.com/meta/facebook/documentation/r56bjfd/facebook-api?entity=request-23987686-3140b0f8-248a-4107-87ae-f321759ac3c7)
- [Get a specific Page access token](https://www.postman.com/meta/facebook/request/tass6hw/get-specific-page-access-token)

## Add the values to GitHub

Open **Repository Settings -> Secrets and variables -> Actions -> Secrets** and
create these repository secrets:

| Name | Value |
| --- | --- |
| `FACEBOOK_PAGE_ACCESS_TOKEN` | The raw Page access token, with no quotes |
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
