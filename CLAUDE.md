# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

`README.md` is the source of truth for product behavior, the data model, the auth flows, and one-time setup. Read it before changing queue, tip, night, or login logic, and keep it updated when behavior changes.

## Commands

```sh
# All backend tests (what CI runs)
cd backend && python3.12 -m unittest discover -s test -v

# A single test file / class / method
cd backend && python3.12 -m unittest test.test_queue
cd backend && python3.12 -m unittest test.test_queue.QueueTest.test_boost_moves_a_singer_from_15_to_exactly_10

# Terraform (CI fails on fmt, so run it before committing .tf changes)
cd terraform && terraform fmt -recursive && terraform validate
cd terraform && terraform plan
```

There is no build step, bundler, linter, or package manager for either the frontend or backend. Pushing to `main` runs tests, then `terraform apply`, then a CloudFront invalidation (`.github/workflows/deploy.yml`); PRs run tests and plan only.

## Architecture

Three pieces, all deployed by Terraform (`terraform/`, state in `s3://karaoke-tf-state`, us-west-2):

- **`frontend/`** — plain HTML + ES modules served from S3 via CloudFront. Terraform uploads every file with `fileset` (`terraform/s3.tf`), so adding a file needs no config unless it has a new extension (add it to `content_types`). One HTML page + one JS module per screen (`index`, `dj`, `dj-signup`, `singer`, `singer-signup`); shared helpers (`api()`, `homeFor()`, `handleAuthError()`, `el()` DOM builder) live in `frontend/js/api.js`. Client-side config such as the Google Maps key comes from `GET /api/config`, not from files.
- **`backend/api/`** — the API Lambda (`karaoke-api`, Python 3.12, stdlib + boto3 only — no third-party packages; `requirements.txt` does not exist and nothing is vendored). Terraform zips the directory directly. `index.py` holds every handler and a `ROUTES` dict keyed by `"METHOD /path"`; `handler()` dispatches, enforces `application/json` on POSTs, and maps `HttpError`/`WorkOSError`/DynamoDB conditional failures to JSON errors. Pure logic lives in `lib/` (`queue.py`, `night.py`, `session.py`, `login_code.py`, `catalog.py`, the document helpers in `songs.py` and `performances.py`) and is what the tests cover; `db.py`, `performances.py` (S3), `songs.py` (S3 + Step Functions), `workos.py`, `youtube.py`, `stingray.py` wrap external services. The same zip also runs the four `karaoke-search-*` Lambdas (`terraform/search.tf`), whose handlers are in `search.py`. Tests run without boto3 installed, so modules they import must load boto3 lazily. All config arrives as Lambda env vars defined in `terraform/lambda.tf`.
- **`backend/workos-bootstrap/`** — a Lambda Terraform invokes on deploy to create the WorkOS organization and register the callback URI; its output feeds `WORKOS_ORG_ID`.

Request path: CloudFront routes `/api/*` and `/auth/*` to the Lambda function URL using OAC (SigV4). Because of that, **every non-GET request from the browser must send an `x-amz-content-sha256` header** with the body's SHA-256 — `api()` in `frontend/js/api.js` does this; don't bypass it with raw `fetch` for POSTs.

### Things that span files

- **Roles are locked per email** (`karaoke-auth` table, `user#<email>`). Singers = WorkOS Google OAuth only; DJs = SES-emailed 6-digit codes, with the WorkOS user created only after verification. Role checks happen both server-side (`require_role`/`require_dj`/`require_singer` in `index.py`) and client-side (`homeFor`/`handleAuthError` redirect to the right page). Changes to login rules should bump `SESSION_VERSION` in `index.py` to invalidate old sessions.
- **Sessions** are HMAC-signed cookies (`lib/session.py`) using `SESSION_SECRET`, not JWTs from WorkOS.
- **Legacy compatibility paths** exist for DJs/singers created before role-locking, email-code login, and nicknames (e.g. DJ ids that are WorkOS user ids, DJs with no `nickname`, singers with no login record). Preserve these when refactoring auth or profile code.
- **"Tonight"** is computed by `lib/night.py` (6am rollover in `NIGHT_TIMEZONE`) and is part of the `karaoke-requested-songs` key; queue ordering and tip boosts (`lib/queue.py`) operate on the numeric `order` field. Song items are never deleted.
- **Performances** (a singer's history, one per night) are JSON files in the `karaoke-performances-<account id>` bucket at `<singerId>/<date>.json`. Every write to a `karaoke-requested-songs` item must go through `_update_night` in `lib/db.py`, which rewrites that file; the singer's Performances tab reads only from S3.
- **Song lookup** is asynchronous. `POST /api/songs/search` writes `searches/<searchId>.json` (the search document, `lib/songs.py`) to the `karaoke-searches-<account id>` bucket and starts the `karaoke-song-search` Step Functions state machine (`terraform/search.tf`), which runs one Lambda per source (KaraFun, then Stingray, then YouTube; handlers in `backend/api/search.py`) and stops at the first with results; a `finish` Lambda settles a search nobody answered. Each Lambda updates the document, and `frontend/js/singer.js` re-reads it at `/searches/<searchId>.json` (a CloudFront path to the bucket) until its status is no longer `searching`. The source order lives in both `SOURCES` (`lib/songs.py`) and `local.search_sources` (`search.tf`). KaraFun is searched through a CSV song list in `backend/api/catalogs/` (`lib/catalog.py`), which ships inside the Lambda zip; Stingray through its Karaoke API (`lib/stingray.py`) when `var.stingray_api` is on, otherwise a CSV list too. Only YouTube songs have a `videoId`; songs carry a `source`, and ones without it predate the lookup and are YouTube. The DJ player (`showTrack` in `frontend/js/dj.js`) must handle songs with no video.
- The DJ UI polls the API (every 4s) rather than using websockets.

### Secrets

WorkOS API key, WorkOS client ID, Google Maps key, YouTube key, and (when `var.stingray_api` is on) the Stingray client ID and secret are read from SSM (`/karaoke/...`) by Terraform. Each song source's key goes only to that source's search Lambda as an environment variable. All AWS resource names are prefixed `karaoke`.
