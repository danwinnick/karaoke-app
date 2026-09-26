import logging
import math
import os
import re
import secrets
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
from lib.workos import authorize_url, decode_jwt, ensure_membership, exchange_code, get_user, pkce_pair
from lib.youtube import search_karaoke

logger = logging.getLogger()
logger.setLevel(logging.INFO)

SECRET = os.environ.get('SESSION_SECRET', '')
REDIRECT_URI = f"{os.environ.get('PUBLIC_URL', '')}/auth/callback"
SESSION_COOKIE = 'karaoke_session'
OAUTH_COOKIE = 'karaoke_oauth'
SESSION_TTL = 7 * 24 * 3600
OAUTH_TTL = 600
MAX_QUEUED_PER_SINGER = 3


def tonight():
    return night_date(time_zone=os.environ.get('NIGHT_TIMEZONE', 'America/Los_Angeles'))


def query_params(event):
    return event.get('queryStringParameters') or {}


# ---- auth helpers ----------------------------------------------------------


def get_session(event):
    return verify_token(parse_cookies(event).get(SESSION_COOKIE), SECRET)


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


# ---- /auth -----------------------------------------------------------------


def login(event):
    q = query_params(event)
    role = 'dj' if q.get('role') == 'dj' else 'singer'
    verifier, challenge = pkce_pair()
    state = b64url_encode(secrets.token_bytes(16))
    oauth_cookie = sign_token({'state': state, 'verifier': verifier, 'role': role}, SECRET, OAUTH_TTL)
    url = authorize_url(state, challenge, REDIRECT_URI, signup=q.get('signup') == '1')
    return redirect(url, [set_cookie(OAUTH_COOKIE, oauth_cookie, OAUTH_TTL)])


def callback(event):
    q = query_params(event)
    if q.get('error'):
        return redirect(f"/?error={quote(q.get('error_description') or q['error'], safe='')}")

    oauth = verify_token(parse_cookies(event).get(OAUTH_COOKIE), SECRET)
    if not oauth or not q.get('code') or oauth.get('state') != q.get('state'):
        return redirect(f"/?error={quote('Your login expired, please try again', safe='')}")

    tokens = exchange_code(q['code'], oauth['verifier'], REDIRECT_URI)
    claims = decode_jwt(tokens.get('id_token') or tokens['access_token'])
    user_id = claims['sub']

    user = None
    try:
        user = get_user(user_id)
    except Exception as err:
        logger.warning('Could not load WorkOS user: %s', err)
    try:
        ensure_membership(user_id)
    except Exception as err:
        logger.warning('Could not add organization membership: %s', err)

    user = user or {}
    email = user.get('email') or claims.get('email') or ''
    name = (
        ' '.join(filter(None, [user.get('first_name'), user.get('last_name')]))
        or claims.get('name')
        or email.split('@')[0]
        or 'Singer'
    )

    if oauth['role'] == 'dj':
        destination = '/dj.html' if db.get_dj(user_id) else '/dj-signup.html'
    else:
        db.upsert_singer(user_id, name, email)
        destination = '/singer.html'

    session = sign_token({'sub': user_id, 'email': email, 'name': name, 'role': oauth['role']}, SECRET, SESSION_TTL)
    return redirect(destination, [set_cookie(SESSION_COOKIE, session, SESSION_TTL), clear_cookie(OAUTH_COOKIE)])


def logout(event):
    return redirect('/', [clear_cookie(SESSION_COOKIE)])


# ---- shared ----------------------------------------------------------------


def config(event):
    return json_response(200, {'googleMapsApiKey': os.environ.get('GOOGLE_MAPS_API_KEY')})


def me(event):
    session = require_role(event)
    dj = db.get_dj(session['sub']) if session.get('role') == 'dj' else None
    return json_response(200, {
        'user': {'id': session['sub'], 'name': session.get('name'), 'email': session.get('email')},
        'role': session.get('role'),
        'dj': dj,
    })


def list_djs(event):
    require_role(event)
    djs = sorted(db.list_djs(), key=lambda d: (d.get('name') or '').casefold())
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
    body = parse_body(event)
    lat = _finite(body.get('lat'))
    lng = _finite(body.get('lng'))
    if lat is None or lng is None:
        raise HttpError(400, 'Pick your address from the suggestions')
    email = require_string(body.get('email'), 'Email', 254)
    if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email):
        raise HttpError(400, 'Enter a valid email')

    place_id = body.get('placeId')
    dj = db.put_dj({
        'djId': session['sub'],
        'name': require_string(body.get('name'), 'Name', 100),
        'email': email,
        'address': require_string(body.get('address'), 'Address', 300),
        'placeId': place_id[:300] if isinstance(place_id, str) else None,
        'lat': lat,
        'lng': lng,
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
        'dj': dj and {'djId': dj['djId'], 'name': dj.get('name'), 'address': dj.get('address')},
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


def get_status(event):
    session = require_role(event, 'singer')
    return json_response(200, singer_status(session['sub']))


def choose_dj(event):
    session = require_role(event, 'singer')
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
    session = require_role(event, 'singer')
    body = parse_body(event)
    video_id = require_string(body.get('videoId'), 'Video', 20)
    if not re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id):
        raise HttpError(400, 'Invalid video')
    title = require_string(body.get('title'), 'Song name', 200)
    thumbnail = body.get('thumbnail')
    if not (isinstance(thumbnail, str) and re.match(r'https://i[0-9]?\.ytimg\.com/', thumbnail)):
        thumbnail = None

    singer = db.get_singer(session['sub'])
    dj_id = (singer or {}).get('currentDjId')
    if not dj_id:
        raise HttpError(400, 'Pick a DJ first')

    date = tonight()
    mine = db.get_night(session['sub'], date)
    if mine and mine.get('djId') != dj_id:
        db.move_night_to_dj(mine, dj_id)

    queued = sum(1 for s in (mine or {}).get('songs') or [] if s.get('status') == 'queued')
    if queued >= MAX_QUEUED_PER_SINGER:
        raise HttpError(400, f'You can have up to {MAX_QUEUED_PER_SINGER} songs in line at once')

    db.append_song(session['sub'], session.get('name'), date, dj_id, {
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
    session = require_role(event, 'singer')
    song_id = require_string(parse_body(event).get('songId'), 'Song', 100)
    date = tonight()
    songs = (db.get_night(session['sub'], date) or {}).get('songs') or []
    index = next((i for i, s in enumerate(songs) if s.get('songId') == song_id), -1)
    if index < 0 or songs[index].get('status') != 'queued':
        raise HttpError(400, 'That song can no longer be removed')
    db.update_song(session['sub'], date, index, song_id, {'status': 'cancelled'})
    return json_response(200, singer_status(session['sub']))


def tip(event):
    session = require_role(event, 'singer')
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
    session = require_role(event, 'singer')
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
    'GET /api/config': config,
    'GET /api/me': me,
    'GET /api/djs': list_djs,
    'POST /api/dj/profile': dj_profile,
    'GET /api/dj/queue': dj_queue,
    'POST /api/dj/next': dj_next,
    'POST /api/dj/status': dj_song_status,
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
    except ClientError as err:
        if err.response.get('Error', {}).get('Code') == 'ConditionalCheckFailedException':
            return json_response(409, {'error': 'The queue changed while you were looking. Refresh and try again.'})
        logger.exception('Unhandled error')
        return json_response(500, {'error': 'Something went wrong'})
    except Exception:
        logger.exception('Unhandled error')
        return json_response(500, {'error': 'Something went wrong'})
