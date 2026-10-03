# 🎤 Karaoke

A karaoke queue for DJs and singers, served at `https://karaoke.<domain>`.

- **Singers** log in with Google (and only Google), pick a DJ (sorted by distance when location is allowed), search for karaoke tracks (on KaraFun, then Stingray Karaoke, then YouTube), and see their spot in line. They get one virtual tip per night: it sends the DJ a 💲 and moves them up to #10 if they're further back. The Tonight tab lists only the songs they've requested that night; the Performances tab keeps every earlier night, one performance per date, each holding that date's set of songs. The singer UI is built for phones first.
- **DJs** log in with any email address using a 6-digit code emailed to them through Amazon SES, sign up with their name, a DJ nickname, and a venue address (Google Places autocomplete), and run the night's queue. Singers only ever see the nickname. The player popup plays the YouTube track (or, for a KaraFun or Stingray song, shows which service to play it on), overlays the next 5 singers, and shows a 💲 when someone tips.

## Architecture

```
Route53 (karaoke.<domain>) ─▶ CloudFront ─┬─ /*        ─▶ S3 (frontend/, private, OAC)
                                          ├─ /searches/* ─▶ S3: karaoke-searches-<account id> (private, OAC)
                                          └─ /api/*, /auth/* ─▶ Lambda function URL (karaoke-api, IAM auth via OAC)
                                                                 ├─ DynamoDB: karaoke-dj, karaoke-singers, karaoke-requested-songs, karaoke-auth
                                                                 ├─ S3: karaoke-performances-<account id> (<singerId>/<date>.json)
                                                                 ├─ WorkOS User Management (singers: Google OAuth + PKCE; DJs: organization members)
                                                                 ├─ Amazon SES (DJs: emailed login codes)
                                                                 └─ Step Functions: karaoke-song-search (one execution per song search)
karaoke-song-search ─▶ karaoke-search-karafun ─▶ karaoke-search-stingray ─▶ karaoke-search-youtube ─▶ karaoke-search-finish
                       (song list)               (Stingray Karaoke API       (YouTube Data API v3)     (settles a search
                                                  or song list)                                         nobody answered)
                       each Lambda updates searches/<searchId>.json; the first source with the song ends the search
Terraform ─▶ karaoke-workos-bootstrap Lambda ─▶ WorkOS: creates the organization + registers the callback redirect URI
```

| Path | What it is |
| --- | --- |
| `terraform/` | All infrastructure. Every resource name and label is prefixed `karaoke`. State: `s3://karaoke-tf-state`, region `us-west-2`. |
| `backend/api/` | `karaoke-api` Lambda (Python 3.12, no dependencies to install; boto3 ships with the runtime). The same zip also runs the four `karaoke-search-*` Lambdas, whose handlers are in `search.py`. |
| `backend/workos-bootstrap/` | Python 3.12 Lambda that Terraform invokes on deploy to create the WorkOS organization and register the callback redirect URI. |
| `backend/test/` | Unit tests for the queue, tip boost, night rollover, sessions, performance files, song lookup, and title cleanup (`cd backend && python3.12 -m unittest discover -s test`). |
| `frontend/` | Static HTML/CSS/JS, no build step. |

### Data model

| Table | Key | Notes |
| --- | --- | --- |
| `karaoke-dj` | `djId` (`dj_<uuid>`; DJs from before email-code login keep their WorkOS user id) | `name` (real name, never shown to singers), `nickname` (shown to singers), `email`, `address`, `lat`, `lng`, `placeId` (Google Places). DJs from before nicknames have no `nickname`; their `name` is shown instead. |
| `karaoke-singers` | `singerId` (WorkOS user id) | `name`, `email`, `currentDjId`, `performancesSyncedAt` (set once the singer's older nights have been copied to the performances bucket) |
| `karaoke-auth` | `pk` | `user#<email>` locks an email to one role (`dj` or `singer`) and user id. `code#<email>` holds a DJ's pending login code as an HMAC, with `attempts`, `resendAfter`, and `expiresAt` (DynamoDB TTL). |
| `karaoke-requested-songs` | `singerId` + `date` | One item per singer per night, holding `requestId`, `djId`, `singerName`, `songs[]` (each with `songId`, `source` (`karafun`, `stingray`, or `youtube`; songs from before the lookup have none and are YouTube), `sourceId`, `videoId` (YouTube only), `title`, `status`, `order`), and `tip`. GSI `djId-date-index` serves the DJ's queue. Items are never deleted. |

| Bucket | Key | Notes |
| --- | --- | --- |
| `karaoke-searches-<account id>` | `searches/<searchId>.json` | One file per song search: `searchId`, `query`, `status` (`searching`, then `found`, `not_found`, or `failed`), `source` (the source being searched, then the one that had the song), `results[]`, `steps[]` (one per source, each `pending`, `searching`, `found`, `not_found`, or `failed`), and `updatedAt`. Written by the API and the search Lambdas, read by the singer's page through CloudFront at `/searches/<searchId>.json`. Anyone who knows a search's random id can read it. Files expire after a day. |
| `karaoke-performances-<account id>` | `<singerId>/<date>.json` | One file per singer per night (a "performance"): `singerId`, `date`, `requestId`, `djId`, `singerName`, `tip`, and `songs[]` (each with `songId`, `source`, `sourceId`, `videoId`, `title`, `thumbnail`, `status`, `djId`, `requestedAt`, `startedAt`, `finishedAt`). Private; only the API Lambda reads and writes it. This is what the singer's Performances tab reads. |

A "night" is a calendar date in `night_timezone` (default `America/Los_Angeles`) that rolls over at 6am, so a 1am request still counts toward the previous evening.

## One-time setup

1. **State bucket:** make sure the S3 bucket `karaoke-tf-state` exists in `us-west-2`.
2. **WorkOS API key:** store it in SSM Parameter Store (us-west-2) as the SecureString `/karaoke/workos_api_key`, either as the raw `sk_...` string or as `{"api_key":"sk_..."}`:
   ```sh
   aws ssm put-parameter --region us-west-2 \
     --name /karaoke/workos_api_key --type SecureString --value 'sk_live_...'
   ```
3. **WorkOS client ID:** store your WorkOS **environment** client ID (Dashboard → API Keys, `client_...`) in SSM Parameter Store (us-west-2) as the SecureString `/karaoke/workos_client_id` (the deploy fails if it is any other type):
   ```sh
   aws ssm put-parameter --region us-west-2 --overwrite \
     --name /karaoke/workos_client_id --type SecureString --value 'client_...'
   ```
4. **WorkOS dashboard:** under Authentication, enable **Google OAuth** (add your own Google OAuth client for production). You can turn off every other method; only singers use WorkOS, and the API rejects any login that isn't Google. The deploy creates the organization `karaoke-<domain>` and registers `https://karaoke.<domain>/auth/callback` as a redirect URI, keeping any others already there (for example, local development callbacks).
5. **Google Cloud:**
   - Create a browser key for the **Maps JavaScript API** and **Places API (New)**, restricted to the `https://karaoke.<domain>/*` referrer, and store it in SSM Parameter Store (us-west-2) as `/karaoke/google_api_key`:
     ```sh
     aws ssm put-parameter --region us-west-2 \
       --name /karaoke/google_api_key --type SecureString --value 'AIza...'
     ```
   - Create a server key for the **YouTube Data API v3** and store it in SSM Parameter Store (us-west-2) as `/karaoke/youtube_api_key`. Only the `karaoke-search-youtube` Lambda gets it. The default quota is 100 searches a day. Searches are debounced and cached, and YouTube is only asked when KaraFun and Stingray don't have the song, but a busy venue will need a quota increase.
     ```sh
     aws ssm put-parameter --region us-west-2 \
       --name /karaoke/youtube_api_key --type SecureString --value 'AIza...'
     ```
6. **KaraFun and Stingray Karaoke** (optional; without them every search falls through to YouTube):
   - **KaraFun** only opens its search API to partners, but it publishes its whole catalog as a CSV. Download it in a browser from the [KaraFun song list page](https://www.karafun.com/karaoke-song-list.html) and save it as `backend/api/catalogs/karafun.csv`. No key is needed.
   - **Stingray Karaoke** has a [Karaoke API](https://karaoke-api-doc.stingray.com/). Ask Stingray Support for a client ID and client secret, store them in SSM Parameter Store (us-west-2), and deploy with `stingray_api = true` (`TF_VAR_stingray_api=true`). Only the `karaoke-search-stingray` Lambda gets them. Until then Stingray is searched through `backend/api/catalogs/stingray.csv`, if you add one.
     ```sh
     aws ssm put-parameter --region us-west-2 \
       --name /karaoke/stingray_client_id --type SecureString --value '...'
     aws ssm put-parameter --region us-west-2 \
       --name /karaoke/stingray_client_secret --type SecureString --value '...'
     ```
7. **Amazon SES:** the deploy verifies `karaoke.<domain>` as an SES identity (DKIM records in Route53) and sends DJ codes from `no-reply@karaoke.<domain>`. New AWS accounts are in the SES sandbox and can only send to verified addresses, so request production access once (SES console → Account dashboard → Request production access, region us-west-2).
8. **GitHub repository settings:**

   | Kind | Name | Value |
   | --- | --- | --- |
   | Secret | `AWS_ACCESS_KEY_ID` | Deploy credentials |
   | Secret | `AWS_SECRET_ACCESS_KEY` | Deploy credentials |

## Deploying

Pushing to `main` runs the tests, then `terraform plan` and `apply`, then invalidates CloudFront. Pull requests only run the tests and the plan.

To deploy by hand:

```sh
cd terraform
terraform init && terraform apply
```

## How the flows work

- **Roles are locked:** every email belongs to exactly one role, fixed the first time it logs in (`karaoke-auth`). A singer's Google email can't be used to log in as a DJ, or the other way round, and there are no links to switch. Logged-in users who open the landing page or the other role's pages are sent back to their own page. Sessions are HMAC-signed, HttpOnly cookies that last 7 days.
- **Singer login:** `/auth/login` redirects to WorkOS with `provider=GoogleOAuth` and PKCE. `/auth/callback` exchanges the code, rejects any `authentication_method` other than `GoogleOAuth`, and adds the user to the WorkOS organization.
- **DJ login:** DJs are users in the WorkOS organization. `POST /auth/dj/code` first looks the email up in the organization; if it isn't there it returns `{"signup": true}` and the landing page shows a signup form (first and last name, DJ nickname, and venue address), which resends the request with those details. The API then sends a 6-digit code through SES (WorkOS sends no email). The WorkOS user is only created and added to the organization once `POST /auth/dj/verify` accepts the code, so nobody can sign up an email they don't control. Codes last 10 minutes, one can be requested per minute, and each code allows 5 guesses. `POST /auth/dj/verify` checks it, deletes it, saves the new DJ's profile from the signup details, and sets the session. A DJ who signed up before email-code login keeps their profile if their profile email matches.
- New users land on `/dj-signup.html` or `/singer-signup.html`. The profile email is always the login email.
- **Song lookup:** as a singer types, `POST /api/songs/search` writes a new search file (`searches/<searchId>.json`, status `searching`) to the searches bucket, starts an execution of the `karaoke-song-search` state machine, and answers with the file's URL. The state machine runs one Lambda per source in order (KaraFun, then Stingray Karaoke, then YouTube). Each Lambda first marks its source as the one being searched, then saves what it found. The first source with any results sets the status to `found` and ends the search; later sources are not searched. A source that has nothing, fails, or times out falls through to the next one, and after the last a `finish` Lambda sets the status to `not_found`, or to `failed` if a source failed and none had the song. The singer's page re-reads the file every 400ms until the status is no longer `searching` (giving up after 20 seconds), showing a note bouncing inside a circle and the name of the source being searched, then lists the results.
- **How each source is searched:** KaraFun through the song list in `backend/api/catalogs/karafun.csv` (see the README there), because its search API is for partners only. Stingray through its Karaoke API (`GET /api/v3/search`, with a token from its device login) when the deployment has credentials, otherwise through `catalogs/stingray.csv`. YouTube through `search.list` of the Data API v3, limited to embeddable videos. Until a list or credentials are added, that source has no songs and lookups fall through to YouTube. Only YouTube songs have a video: for the others the DJ's queue shows the service's name and the player shows which service to play the song on.
- **Queue order:** each song carries a numeric `order` (the request timestamp). Your number is the position of your earliest queued song.
- **Tips:** one per singer per night, enforced with a DynamoDB condition. If you're below #10, your next song's `order` is set halfway between #9 and #10, so you land at exactly #10. The DJ UI polls every 4s and shows a 💲 for each new tip, including over the player. Tips are virtual; no payments are involved.
- **Performances:** every change to a singer's night (a request, a removal, a tip, a DJ switch, or the DJ starting, finishing, or skipping a song) rewrites that night's file in the performances bucket from the DynamoDB item, so the file always holds the whole set for that date. `GET /api/singer/performances` lists the singer's files newest first, 10 at a time. The first time a singer opens the tab, nights from before the bucket existed are copied over from DynamoDB. If a write to S3 fails, the queue change still succeeds and the file catches up on that night's next change.
- **Switching DJs mid-night:** your queued songs move to the back of the new DJ's line.
