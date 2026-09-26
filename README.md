# 🎤 Karaoke

A karaoke queue for DJs and singers, served at `https://karaoke.<domain>`.

- **Singers** sign up or log in, pick a DJ (sorted by distance when location is allowed), search YouTube for karaoke tracks, and see their spot in line. They get one virtual tip per night: it sends the DJ a 💲 and moves them up to #10 if they're further back. Every song they've picked is kept in a History tab. The singer UI is built for phones first.
- **DJs** sign up with a name, email, and venue address (Google Places autocomplete) and run the night's queue. The player popup plays the YouTube track, overlays the next 5 singers, and shows a 💲 when someone tips.

## Architecture

```
Route53 (karaoke.<domain>) ─▶ CloudFront ─┬─ /*        ─▶ S3 (frontend/, private, OAC)
                                          └─ /api/*, /auth/* ─▶ Lambda function URL (karaoke-api, IAM auth via OAC)
                                                                 ├─ DynamoDB: karaoke-dj, karaoke-singers, karaoke-requested-songs
                                                                 ├─ WorkOS AuthKit (OAuth + PKCE)
                                                                 └─ YouTube Data API v3
Terraform ─▶ karaoke-workos-bootstrap Lambda ─▶ WorkOS: creates the organization + registers the callback URL
```

| Path | What it is |
| --- | --- |
| `terraform/` | All infrastructure. Every resource name and label is prefixed `karaoke`. State: `s3://karaoke-tf-state`, region `us-west-2`. |
| `backend/api/` | `karaoke-api` Lambda (Python 3.12, no dependencies to install; boto3 ships with the runtime). |
| `backend/workos-bootstrap/` | Python 3.12 Lambda that Terraform invokes on deploy to create or update the WorkOS organization and application. |
| `backend/test/` | Unit tests for the queue, tip boost, night rollover, sessions, and title cleanup (`cd backend && python3.12 -m unittest discover -s test`). |
| `frontend/` | Static HTML/CSS/JS, no build step. |

### Data model

| Table | Key | Notes |
| --- | --- | --- |
| `karaoke-dj` | `djId` (WorkOS user id) | `name`, `email`, `address`, `lat`, `lng`, `placeId` |
| `karaoke-singers` | `singerId` | `name`, `email`, `currentDjId` |
| `karaoke-requested-songs` | `singerId` + `date` | One item per singer per night, holding `requestId`, `djId`, `singerName`, `songs[]` (each with `songId`, `videoId`, `title`, `status`, `order`), and `tip`. GSI `djId-date-index` serves the DJ's queue. Items are never deleted, which is what powers the singer's all-time history. |

A "night" is a calendar date in `night_timezone` (default `America/Los_Angeles`) that rolls over at 6am, so a 1am request still counts toward the previous evening.

## One-time setup

1. **State bucket:** make sure the S3 bucket `karaoke-tf-state` exists in `us-west-2`.
2. **WorkOS API key:** store it in SSM Parameter Store (us-west-2) as the SecureString `/karaoke/workos_api_key`, either as the raw `sk_...` string or as `{"api_key":"sk_..."}`:
   ```sh
   aws ssm put-parameter --region us-west-2 \
     --name /karaoke/workos_api_key --type SecureString --value 'sk_live_...'
   ```
3. **WorkOS client ID:** store it in SSM Parameter Store (us-west-2) as the SecureString `/karaoke/workos_client_id` (the deploy fails if it is any other type). It must be the client ID of a WorkOS Connect OAuth application (Dashboard → Applications) in the same environment as the API key. The API uses it as the OAuth `client_id` for AuthKit:
   ```sh
   aws ssm put-parameter --region us-west-2 \
     --name /karaoke/workos_client_id --type SecureString --value 'client_...'
   ```
4. **WorkOS dashboard:** enable AuthKit with email sign-up allowed, and copy your AuthKit domain (Dashboard → Domains, e.g. `your-app.authkit.app`). The deploy creates the organization `karaoke-<domain>`. It also sets `https://karaoke.<domain>/auth/callback` as the default redirect URI on the application that owns `/karaoke/workos_client_id`, and keeps any other redirect URIs already on that application (for example, local development callbacks). If the client ID doesn't match a Connect OAuth application, the deploy fails.
5. **Google Cloud:**
   - Create a browser key for the **Maps JavaScript API** and **Places API (New)**, restricted to the `https://karaoke.<domain>/*` referrer.
   - Create a server key for the **YouTube Data API v3**. The default quota of 10,000 units a day covers about 100 searches. The API debounces and caches searches, but a busy venue will need a quota increase.
6. **GitHub repository settings:**

   | Kind | Name | Value |
   | --- | --- | --- |
   | Secret | `AWS_ACCESS_KEY_ID` | Deploy credentials |
   | Secret | `AWS_SECRET_ACCESS_KEY` | Deploy credentials |
   | Secret | `GOOGLE_MAPS_API_KEY` | Browser key from step 5 |
   | Secret | `YOUTUBE_API_KEY` | Server key from step 5 |
   | Variable | `WORKOS_AUTHKIT_DOMAIN` | e.g. `your-app.authkit.app` |

## Deploying

Pushing to `main` runs the tests, then `terraform plan` and `apply`, then invalidates CloudFront. Pull requests only run the tests and the plan.

To deploy by hand:

```sh
cd terraform
export TF_VAR_workos_authkit_domain=your-app.authkit.app \
       TF_VAR_google_maps_api_key=... TF_VAR_youtube_api_key=...
terraform init && terraform apply
```

## How the flows work

- **Login and sign-up:** `/auth/login?role=singer|dj[&signup=1]` redirects to AuthKit using PKCE. `/auth/callback` exchanges the code, adds the user to the WorkOS organization, and sets an HMAC-signed, HttpOnly session cookie that lasts 7 days. DJs without a profile land on `/dj-signup.html`.
- **Queue order:** each song carries a numeric `order` (the request timestamp). Your number is the position of your earliest queued song.
- **Tips:** one per singer per night, enforced with a DynamoDB condition. If you're below #10, your next song's `order` is set halfway between #9 and #10, so you land at exactly #10. The DJ UI polls every 4s and shows a 💲 for each new tip, including over the player. Tips are virtual; no payments are involved.
- **Switching DJs mid-night:** your queued songs move to the back of the new DJ's line.
