# 🎤 Karaoke

A karaoke queue for DJs and singers, served at `https://karaoke.<domain>`.

- **Singers** log in with Google (and only Google), pick a DJ (sorted by distance when location is allowed), search YouTube for karaoke tracks, and see their spot in line. They get one virtual tip per night: it sends the DJ a 💲 and moves them up to #10 if they're further back. Every song they've picked is kept in a History tab. The singer UI is built for phones first.
- **DJs** log in with any email address using a 6-digit code emailed to them through Amazon SES, sign up with their name, a DJ nickname, and a venue address (Google Places autocomplete), and run the night's queue. Singers only ever see the nickname. The player popup plays the YouTube track, overlays the next 5 singers, and shows a 💲 when someone tips.

## Architecture

```
Route53 (karaoke.<domain>) ─▶ CloudFront ─┬─ /*        ─▶ S3 (frontend/, private, OAC)
                                          └─ /api/*, /auth/* ─▶ Lambda function URL (karaoke-api, IAM auth via OAC)
                                                                 ├─ DynamoDB: karaoke-dj, karaoke-singers, karaoke-requested-songs, karaoke-auth
                                                                 ├─ WorkOS User Management (singers: Google OAuth + PKCE; DJs: organization members)
                                                                 ├─ Amazon SES (DJs: emailed login codes)
                                                                 └─ YouTube Data API v3
Terraform ─▶ karaoke-workos-bootstrap Lambda ─▶ WorkOS: creates the organization + registers the callback redirect URI
```

| Path | What it is |
| --- | --- |
| `terraform/` | All infrastructure. Every resource name and label is prefixed `karaoke`. State: `s3://karaoke-tf-state`, region `us-west-2`. |
| `backend/api/` | `karaoke-api` Lambda (Python 3.12, no dependencies to install; boto3 ships with the runtime). |
| `backend/workos-bootstrap/` | Python 3.12 Lambda that Terraform invokes on deploy to create the WorkOS organization and register the callback redirect URI. |
| `backend/test/` | Unit tests for the queue, tip boost, night rollover, sessions, and title cleanup (`cd backend && python3.12 -m unittest discover -s test`). |
| `frontend/` | Static HTML/CSS/JS, no build step. |

### Data model

| Table | Key | Notes |
| --- | --- | --- |
| `karaoke-dj` | `djId` (`dj_<uuid>`; DJs from before email-code login keep their WorkOS user id) | `name` (real name, never shown to singers), `nickname` (shown to singers), `email`, `address`, `lat`, `lng`, `placeId` (Google Places). DJs from before nicknames have no `nickname`; their `name` is shown instead. |
| `karaoke-singers` | `singerId` (WorkOS user id) | `name`, `email`, `currentDjId` |
| `karaoke-auth` | `pk` | `user#<email>` locks an email to one role (`dj` or `singer`) and user id. `code#<email>` holds a DJ's pending login code as an HMAC, with `attempts`, `resendAfter`, and `expiresAt` (DynamoDB TTL). |
| `karaoke-requested-songs` | `singerId` + `date` | One item per singer per night, holding `requestId`, `djId`, `singerName`, `songs[]` (each with `songId`, `videoId`, `title`, `status`, `order`), and `tip`. GSI `djId-date-index` serves the DJ's queue. Items are never deleted, which is what powers the singer's all-time history. |

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
   - Create a browser key for the **Maps JavaScript API** and **Places API (New)**, restricted to the `https://karaoke.<domain>/*` referrer.
   - Create a server key for the **YouTube Data API v3**. The default quota of 10,000 units a day covers about 100 searches. The API debounces and caches searches, but a busy venue will need a quota increase.
6. **Amazon SES:** the deploy verifies `karaoke.<domain>` as an SES identity (DKIM records in Route53) and sends DJ codes from `no-reply@karaoke.<domain>`. New AWS accounts are in the SES sandbox and can only send to verified addresses, so request production access once (SES console → Account dashboard → Request production access, region us-west-2).
7. **GitHub repository settings:**

   | Kind | Name | Value |
   | --- | --- | --- |
   | Secret | `AWS_ACCESS_KEY_ID` | Deploy credentials |
   | Secret | `AWS_SECRET_ACCESS_KEY` | Deploy credentials |
   | Secret | `GOOGLE_MAPS_API_KEY` | Browser key from step 5 |
   | Secret | `YOUTUBE_API_KEY` | Server key from step 5 |

## Deploying

Pushing to `main` runs the tests, then `terraform plan` and `apply`, then invalidates CloudFront. Pull requests only run the tests and the plan.

To deploy by hand:

```sh
cd terraform
export TF_VAR_google_maps_api_key=... TF_VAR_youtube_api_key=...
terraform init && terraform apply
```

## How the flows work

- **Roles are locked:** every email belongs to exactly one role, fixed the first time it logs in (`karaoke-auth`). A singer's Google email can't be used to log in as a DJ, or the other way round, and there are no links to switch. Logged-in users who open the landing page or the other role's pages are sent back to their own page. Sessions are HMAC-signed, HttpOnly cookies that last 7 days.
- **Singer login:** `/auth/login` redirects to WorkOS with `provider=GoogleOAuth` and PKCE. `/auth/callback` exchanges the code, rejects any `authentication_method` other than `GoogleOAuth`, and adds the user to the WorkOS organization.
- **DJ login:** DJs are users in the WorkOS organization. `POST /auth/dj/code` first looks the email up in the organization; if it isn't there it returns `{"signup": true}` and the landing page shows a signup form (first and last name, DJ nickname, and venue address), which resends the request with those details. The API then sends a 6-digit code through SES (WorkOS sends no email). The WorkOS user is only created and added to the organization once `POST /auth/dj/verify` accepts the code, so nobody can sign up an email they don't control. Codes last 10 minutes, one can be requested per minute, and each code allows 5 guesses. `POST /auth/dj/verify` checks it, deletes it, saves the new DJ's profile from the signup details, and sets the session. A DJ who signed up before email-code login keeps their profile if their profile email matches.
- New users land on `/dj-signup.html` or `/singer-signup.html`. The profile email is always the login email.
- **Queue order:** each song carries a numeric `order` (the request timestamp). Your number is the position of your earliest queued song.
- **Tips:** one per singer per night, enforced with a DynamoDB condition. If you're below #10, your next song's `order` is set halfway between #9 and #10, so you land at exactly #10. The DJ UI polls every 4s and shows a 💲 for each new tip, including over the player. Tips are virtual; no payments are involved.
- **Switching DJs mid-night:** your queued songs move to the back of the new DJ's line.
