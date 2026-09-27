import logging
import math
import os
import re
import secrets
import time
import uuid
from urllib.parse import quote

from botocore.exceptions import ClientError

from lib import db
from lib.http_utils import (
    HttpError,
    clear_cookie,
    json_response,
    parse_body,
    parse_cookies,
    redirect,
    require_string,
    set_cookie,
)
from lib.night import night_date
from lib.queue import TIP_POSITION, boost_order, build_queue, singer_position, tips_for_dj
from lib.session import b64url_encode, sign_token, verify_token
from lib.login_code import code_matches, hash_code, new_code, normalize_email, send_code
from lib.workos import (
    WorkOSError,
    authenticate_code,
    authorize_url,
    create_org_user,
    ensure_membership,
    find_org_user,
    pkce_pair,
)
from lib.youtube import search_karaoke

logger = logging.getLogger()
logger.setLevel(logging.INFO)

SECRET = os.environ.get('SESSION_SECRET', '')
REDIRECT_URI = f"{os.environ.get('PUBLIC_URL', '')}/auth/callback"
SESSION_COOKIE = 'karaoke_session'
OAUTH_COOKIE = 'karaoke_oauth'
SESSION_TTL = 7 * 24 * 3600
OAUTH_TTL = 600
# Bumped whenever login rules change, so sessions issued under the old rules stop working.
SESSION_VERSION = 2
CODE_TTL = 600
CODE_RESEND = 60
CODE_MAX_ATTEMPTS = 5
MAX_QUEUED_PER_SINGER = 3


def tonight():
    return night_date(time_zone=os.environ.get('NIGHT_TIMEZONE', 'America/Los_Angeles'))


def query_params(event):
    return event.get('queryStringParameters') or {}


# ---- auth helpers ----------------------------------------------------------


def get_session(event):
    session = verify_token(parse_cookies(event).get(SESSION_COOKIE), SECRET)
    return session if session and session.get('v') == SESSION_VERSION else None


def home_for(role, user_id):
    if role == 'dj':
        return '/dj.html' if db.get_dj(user_id) else '/dj-signup.html'
    return '/singer.html' if db.get_singer(user_id) else '/singer-signup.html'


def session_cookie(user_id, email, name, role):
    token = sign_token(
        {'sub': user_id, 'email': email, 'name': name, 'role': role, 'v': SESSION_VERSION}, SECRET, SESSION_TTL
    )
    return set_cookie(SESSION_COOKIE, token, SESSION_TTL)


def login_error(message):
    return redirect(f"/?error={quote(message, safe='')}", [clear_cookie(OAUTH_COOKIE)])


def require_role(event, role=None):
    session = get_session(event)
    if not session:
        raise HttpError(401, 'Please log in')
    if role and session.get('role') != role:
        raise HttpError(403, f'Log in as a {role} to do that')
    return session


def require_dj(event):
    session = require_role(event, 'dj')
    dj = db.get_dj(session['sub'])
    if not dj:
        raise HttpError(403, 'Finish DJ signup first')
    return session, dj


def require_singer(event):
    session = require_role(event, 'singer')
    singer = db.get_singer(session['sub'])
    if not singer:
        raise HttpError(403, 'Finish singer signup first')
    return session, singer


# ---- /auth -----------------------------------------------------------------
# Each email belongs to exactly one role, fixed the first time it logs in. Singers log in
# with Google (via WorkOS); DJs are WorkOS organization members who log in with a one-time
# code emailed through SES.


def login(event):
    session = get_session(event)
    if session:
        return redirect(home_for(session['role'], session['sub']))
    verifier, challenge = pkce_pair()
    state = b64url_encode(secrets.token_bytes(16))
    oauth_cookie = sign_token({'state': state, 'verifier': verifier}, SECRET, OAUTH_TTL)
    return redirect(authorize_url(state, challenge, REDIRECT_URI), [set_cookie(OAUTH_COOKIE, oauth_cookie, OAUTH_TTL)])


def callback(event):
    q = query_params(event)
    if q.get('error'):
        return login_error(q.get('error_description') or q['error'])

    oauth = verify_token(parse_cookies(event).get(OAUTH_COOKIE), SECRET)
    if not oauth or not q.get('code') or oauth.get('state') != q.get('state'):
        return login_error('Your login expired, please try again')

    auth = authenticate_code(q['code'], oauth['verifier'])
    if auth.get('authentication_method') != 'GoogleOAuth':
        return login_error('Singers log in with Google')
    user = auth['user']
    email = normalize_email(user.get('email'))
    if not email:
        return login_error('Your Google account has no email address')

    login_record = db.get_login(email)
    if not login_record:
        # Accounts from before roles were locked: a DJ who used Google stays a DJ.
        if db.get_dj(user['id']):
            return login_error('This email belongs to a DJ account. DJs log in with an email code.')
        login_record = db.create_login(email, 'singer', user['id'])
    if login_record['role'] != 'singer':
        return login_error('This email belongs to a DJ account. DJs log in with an email code.')

    try:
        ensure_membership(user['id'])
    except Exception as err:
        logger.warning('Could not add organization membership: %s', err)

    user_id = login_record['userId']
    name = ' '.join(filter(None, [user.get('first_name'), user.get('last_name')])) or email.split('@')[0]
    return redirect(
        home_for('singer', user_id),
        [session_cookie(user_id, email, name, 'singer'), clear_cookie(OAUTH_COOKIE)],
    )


def require_dj_email(email):
    if not email:
        raise HttpError(400, 'Enter a valid email')
    login_record = db.get_login(email)
    # Singers from before roles were locked have no login record yet.
    if (login_record and login_record['role'] != 'dj') or (not login_record and db.find_singer_by_email(email)):
        raise HttpError(409, 'This email belongs to a singer account. Singers log in with Google.')


# The DJ's nickname and venue, as picked from Google Places autocomplete.
def dj_details(body):
    lat = _finite(body.get('lat'))
    lng = _finite(body.get('lng'))
    if lat is None or lng is None:
        raise HttpError(400, 'Pick your address from the suggestions')
    place_id = body.get('placeId')
    return {
        'nickname': require_string(body.get('nickname'), 'DJ nickname', 100),
        'address': require_string(body.get('address'), 'Address', 300),
        'placeId': place_id[:300] if isinstance(place_id, str) else None,
        'lat': lat,
        'lng': lng,
    }


# Names and DJ details from the signup form, or None when the form wasn't sent.
def signup_profile(body):
    if body.get('firstName') is None:
        return None
    last = body.get('lastName')
    return {
        'firstName': require_string(body.get('firstName'), 'First name', 100),
        'lastName': last.strip()[:100] if isinstance(last, str) else '',
        **dj_details(body),
    }


# DJs are users in the deployment's WorkOS organization. An email that isn't in it yet gets
# {'signup': True} back, and the client resends it with the signup form's details.
def dj_request_code(event):
    body = parse_body(event)
    email = normalize_email(body.get('email'))
    require_dj_email(email)
    if not find_org_user(email) and not signup_profile(body):
        return json_response(200, {'signup': True})

    code = new_code()
    if not db.put_login_code(email, hash_code(SECRET, email, code), CODE_TTL, CODE_RESEND):
        raise HttpError(429, 'We just sent you a code. Wait a minute before asking for another.')
    try:
        send_code(os.environ['SES_FROM_ADDRESS'], email, code, CODE_TTL // 60)
    except ClientError:
        logger.exception('Could not send login code')
        db.consume_login_code(email)
        raise HttpError(502, "We couldn't email a code to that address. Try again later.")
    return json_response(200, {'sent': True})


def dj_verify_code(event):
    body = parse_body(event)
    email = normalize_email(body.get('email'))
    code = body.get('code').strip() if isinstance(body.get('code'), str) else ''
    if not email or not re.fullmatch(r'[0-9]{6}', code):
        raise HttpError(400, 'Enter the 6-digit code from your email')

    # Looked up before the code is used, so a missing signup doesn't burn it.
    workos_user = find_org_user(email)
    profile = None if workos_user else signup_profile(body)
    if not workos_user and not profile:
        raise HttpError(400, 'Sign up with your name first.')

    record = db.attempt_login_code(email)
    if not record or record['expiresAt'] <= time.time():
        raise HttpError(400, 'That code has expired. Request a new one.')
    if record['attempts'] > CODE_MAX_ATTEMPTS:
        raise HttpError(429, 'Too many tries. Request a new code.')
    if not code_matches(SECRET, email, code, record.get('codeHash')) or not db.consume_login_code(email):
        raise HttpError(400, 'That code is not right. Check your email and try again.')

    if not workos_user:
        workos_user = create_org_user(email, profile['firstName'], profile['lastName'])

    login_record = db.get_login(email)
    if not login_record:
        legacy = db.find_dj_by_email(email)
        login_record = db.create_login(email, 'dj', legacy['djId'] if legacy else workos_user['id'])
    if login_record['role'] != 'dj':
        raise HttpError(409, 'This email belongs to a singer account. Singers log in with Google.')

    user_id = login_record['userId']
    dj = db.get_dj(user_id)
    full_name = ' '.join(filter(None, [workos_user.get('first_name'), workos_user.get('last_name')]))
    name = (dj or {}).get('name') or full_name or email.split('@')[0]
    if not dj and profile:
        details = {k: v for k, v in profile.items() if k not in ('firstName', 'lastName')}
        dj = db.put_dj({'djId': user_id, 'name': name, 'email': email, **details})
    return json_response(
        200,
        {'redirect': '/dj.html' if dj else '/dj-signup.html'},
        [session_cookie(user_id, email, name, 'dj')],
    )


def logout(event):
    return redirect('/', [clear_cookie(SESSION_COOKIE)])


# ---- shared ----------------------------------------------------------------


def config(event):
    return json_response(200, {'googleMapsApiKey': os.environ.get('GOOGLE_MAPS_API_KEY')})


def me(event):
    session = require_role(event)
    role = session.get('role')
    return json_response(200, {
        'user': {'id': session['sub'], 'name': session.get('name'), 'email': session.get('email')},
        'role': role,
        'dj': db.get_dj(session['sub']) if role == 'dj' else None,
        'singer': db.get_singer(session['sub']) if role == 'singer' else None,
    })


def list_djs(event):
    require_role(event)
    djs = [
        {'djId': d['djId'], 'name': db.dj_public_name(d), 'address': d.get('address'), 'lat': d.get('lat'), 'lng': d.get('lng')}
        for d in db.list_djs()
    ]
    djs.sort(key=lambda d: (d['name'] or '').casefold())
    return json_response(200, {'djs': djs})


# ---- DJ --------------------------------------------------------------------


def _finite(value):
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def dj_profile(event):
    session = require_role(event, 'dj')
    details = dj_details(parse_body(event))
    existing = db.get_dj(session['sub']) or {}
    dj = db.put_dj({
        'djId': session['sub'],
        'name': existing.get('name') or session.get('name'),
        'email': session['email'],
        **details,
    })
    return json_response(200, {'dj': dj})


def dj_state(dj_id):
    date = tonight()
    items = db.query_dj_night(dj_id, date)
    now_playing, queue = build_queue(items, dj_id)
    return {'date': date, 'nowPlaying': now_playing, 'queue': queue, 'tips': tips_for_dj(items, dj_id)}


def set_song_status(entry, date, status):
    now = db.now_iso()
    fields = {'status': status}
    if status == 'playing':
        fields['startedAt'] = now
    if status in ('done', 'skipped'):
        fields['finishedAt'] = now
    db.update_song(entry['singerId'], date, entry['index'], entry['songId'], fields)


def dj_queue(event):
    _, dj = require_dj(event)
    return json_response(200, {'dj': dj, **dj_state(dj['djId'])})


def dj_next(event):
    _, dj = require_dj(event)
    state = dj_state(dj['djId'])
    if state['nowPlaying']:
        set_song_status(state['nowPlaying'], state['date'], 'done')
    if state['queue']:
        set_song_status(state['queue'][0], state['date'], 'playing')
    return json_response(200, {'dj': dj, **dj_state(dj['djId'])})


def dj_song_status(event):
    _, dj = require_dj(event)
    body = parse_body(event)
    status = body.get('status')
    if status not in ('playing', 'done', 'skipped'):
        raise HttpError(400, 'Invalid status')

    state = dj_state(dj['djId'])
    now_playing = state['nowPlaying']
    candidates = state['queue'] + ([now_playing] if now_playing else [])
    entry = next(
        (e for e in candidates if e['songId'] == body.get('songId') and e['singerId'] == body.get('singerId')),
        None,
    )
    if not entry:
        raise HttpError(404, 'That song is no longer in your queue')

    if status == 'playing' and now_playing and now_playing['songId'] != entry['songId']:
        set_song_status(now_playing, state['date'], 'done')
    set_song_status(entry, state['date'], status)
    return json_response(200, {'dj': dj, **dj_state(dj['djId'])})


# ---- Singer ----------------------------------------------------------------


def singer_status(singer_id):
    date = tonight()
    singer = db.get_singer(singer_id)
    current_dj_id = (singer or {}).get('currentDjId')
    dj = db.get_dj(current_dj_id) if current_dj_id else None
    mine = db.get_night(singer_id, date) or {}

    queue, now_playing = [], None
    if dj:
        now_playing, queue = build_queue(db.query_dj_night(dj['djId'], date), dj['djId'])

    positions = {e['songId']: e['position'] for e in queue}
    position = singer_position(queue, singer_id)
    return {
        'date': date,
        'dj': dj and {'djId': dj['djId'], 'name': db.dj_public_name(dj), 'address': dj.get('address')},
        'position': position,
        'queueLength': len(queue),
        'nowPlaying': now_playing and {
            'singerName': now_playing['singerName'],
            'title': now_playing['title'],
            'isMe': now_playing['singerId'] == singer_id,
        },
        'songs': [
            {
                'songId': song.get('songId'),
                'title': song.get('title'),
                'videoId': song.get('videoId'),
                'thumbnail': song.get('thumbnail'),
                'status': song.get('status'),
                'requestedAt': song.get('requestedAt'),
                'position': positions.get(song.get('songId')),
                'withCurrentDj': song.get('djId') == (dj or {}).get('djId'),
            }
            for song in mine.get('songs') or []
        ],
        'tipUsed': bool(mine.get('tip')),
        'tipPosition': TIP_POSITION,
        'canBoost': position is not None and position > TIP_POSITION,
    }


def singer_profile(event):
    session = require_role(event, 'singer')
    body = parse_body(event)
    name = require_string(body.get('name'), 'Name', 100)
    db.upsert_singer(session['sub'], name, session['email'])
    return json_response(200, {'singer': db.get_singer(session['sub'])})


def get_status(event):
    session, _ = require_singer(event)
    return json_response(200, singer_status(session['sub']))


def choose_dj(event):
    session, _ = require_singer(event)
    dj_id = require_string(parse_body(event).get('djId'), 'DJ', 200)
    if not db.get_dj(dj_id):
        raise HttpError(404, 'DJ not found')

    db.set_singer_dj(session['sub'], dj_id)
    mine = db.get_night(session['sub'], tonight())
    if mine and mine.get('djId') != dj_id:
        db.move_night_to_dj(mine, dj_id)
    return json_response(200, singer_status(session['sub']))


def youtube_search(event):
    require_role(event)
    q = (query_params(event).get('q') or '').strip()
    if len(q) < 2 or len(q) > 100:
        raise HttpError(400, 'Search must be 2-100 characters')
    return json_response(200, {'results': search_karaoke(q, os.environ.get('YOUTUBE_API_KEY'))})


def request_song(event):
    session, singer = require_singer(event)
    body = parse_body(event)
    video_id = require_string(body.get('videoId'), 'Video', 20)
    if not re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id):
        raise HttpError(400, 'Invalid video')
    title = require_string(body.get('title'), 'Song name', 200)
    thumbnail = body.get('thumbnail')
    if not (isinstance(thumbnail, str) and re.match(r'https://i[0-9]?\.ytimg\.com/', thumbnail)):
        thumbnail = None

    dj_id = singer.get('currentDjId')
    if not dj_id:
        raise HttpError(400, 'Pick a DJ first')

    date = tonight()
    mine = db.get_night(session['sub'], date)
    if mine and mine.get('djId') != dj_id:
        db.move_night_to_dj(mine, dj_id)

    queued = sum(1 for s in (mine or {}).get('songs') or [] if s.get('status') == 'queued')
    if queued >= MAX_QUEUED_PER_SINGER:
        raise HttpError(400, f'You can have up to {MAX_QUEUED_PER_SINGER} songs in line at once')

    db.append_song(session['sub'], singer.get('name'), date, dj_id, {
        'songId': str(uuid.uuid4()),
        'videoId': video_id,
        'title': title,
        'thumbnail': thumbnail,
        'djId': dj_id,
        'status': 'queued',
        'order': db.now_ms(),
        'requestedAt': db.now_iso(),
    })
    return json_response(200, singer_status(session['sub']))


def cancel_song(event):
    session, _ = require_singer(event)
    song_id = require_string(parse_body(event).get('songId'), 'Song', 100)
    date = tonight()
    songs = (db.get_night(session['sub'], date) or {}).get('songs') or []
    index = next((i for i, s in enumerate(songs) if s.get('songId') == song_id), -1)
    if index < 0 or songs[index].get('status') != 'queued':
        raise HttpError(400, 'That song can no longer be removed')
    db.update_song(session['sub'], date, index, song_id, {'status': 'cancelled'})
    return json_response(200, singer_status(session['sub']))


def tip(event):
    session, _ = require_singer(event)
    date = tonight()
    mine = db.get_night(session['sub'], date)
    if not mine:
        raise HttpError(400, 'Request a song before tipping')
    if mine.get('tip'):
        raise HttpError(409, 'You already tipped tonight')

    _, queue = build_queue(db.query_dj_night(mine['djId'], date), mine['djId'])
    try:
        boost = boost_order(queue, session['sub'])
    except ValueError as err:
        raise HttpError(400, str(err))

    db.record_tip(
        session['sub'],
        date,
        mine['djId'],
        boost and {'index': boost['entry']['index'], 'songId': boost['entry']['songId'], 'order': boost['order']},
    )
    return json_response(200, {'boosted': bool(boost), **singer_status(session['sub'])})


def history(event):
    session, _ = require_singer(event)
    try:
        page = db.history_page(session['sub'], query_params(event).get('cursor'))
    except db.InvalidCursor as err:
        raise HttpError(400, str(err))
    dj_names = db.get_dj_names([i.get('djId') for i in page['items']])
    return json_response(200, {
        'cursor': page['cursor'],
        'nights': [
            {
                'date': item['date'],
                'requestId': item.get('requestId'),
                'djId': item.get('djId'),
                'djName': dj_names.get(item.get('djId'), 'Unknown DJ'),
                'tipped': bool(item.get('tip')),
                'songs': [
                    {
                        'title': s.get('title'),
                        'videoId': s.get('videoId'),
                        'thumbnail': s.get('thumbnail'),
                        'status': s.get('status'),
                        'requestedAt': s.get('requestedAt'),
                        'djName': dj_names.get(s.get('djId'), dj_names.get(item.get('djId'))),
                    }
                    for s in item.get('songs') or []
                ],
            }
            for item in page['items']
        ],
    })


# ---- router ----------------------------------------------------------------

ROUTES = {
    'GET /auth/login': login,
    'GET /auth/callback': callback,
    'GET /auth/logout': logout,
    'POST /auth/dj/code': dj_request_code,
    'POST /auth/dj/verify': dj_verify_code,
    'GET /api/config': config,
    'GET /api/me': me,
    'GET /api/djs': list_djs,
    'POST /api/dj/profile': dj_profile,
    'GET /api/dj/queue': dj_queue,
    'POST /api/dj/next': dj_next,
    'POST /api/dj/status': dj_song_status,
    'POST /api/singer/profile': singer_profile,
    'GET /api/singer/status': get_status,
    'POST /api/singer/dj': choose_dj,
    'GET /api/youtube/search': youtube_search,
    'POST /api/singer/requests': request_song,
    'POST /api/singer/requests/cancel': cancel_song,
    'POST /api/singer/tip': tip,
    'GET /api/singer/history': history,
}


def handler(event, context):
    method = event['requestContext']['http']['method']
    route = ROUTES.get(f"{method} {event['rawPath']}")
    if not route:
        return json_response(404, {'error': 'Not found'})

    if method == 'POST' and 'application/json' not in (event.get('headers') or {}).get('content-type', ''):
        return json_response(415, {'error': 'Expected application/json'})

    try:
        return route(event)
    except HttpError as err:
        return json_response(err.status, {'error': str(err)})
    except WorkOSError:
        logger.exception('WorkOS request failed')
        return json_response(502, {'error': "We couldn't reach the login service. Try again."})
    except ClientError as err:
        if err.response.get('Error', {}).get('Code') == 'ConditionalCheckFailedException':
            return json_response(409, {'error': 'The queue changed while you were looking. Refresh and try again.'})
        logger.exception('Unhandled error')
        return json_response(500, {'error': 'Something went wrong'})
    except Exception:
        logger.exception('Unhandled error')
        return json_response(500, {'error': 'Something went wrong'})
