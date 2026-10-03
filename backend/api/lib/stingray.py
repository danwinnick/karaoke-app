import base64
import json
import time
import urllib.error
import urllib.request
from urllib.parse import urlencode

# Stingray's Karaoke API (https://karaoke-api-doc.stingray.com). Stingray Support issues a
# client ID and secret, which are traded for a token that lasts a day; searches are made with
# that token. The API is for backends only (CORS is off) and allows one call per 100ms.

LOGIN_URL = 'https://login.stingray.com/loginapi/oauth/deviceLogin'
API = 'https://karaoke-api-service-prod.stingray.com'
DEVICE_ID = 'karaoke-api'
LABEL = 'Stingray Karaoke'
MAX_RESULTS = 8
# The API rejects search text outside 3-40 characters.
MIN_QUERY, MAX_QUERY = 3, 40

_token = {'value': None, 'expires': 0}


def _request(url, headers, body=None):
    req = urllib.request.Request(url, data=body, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as res:
            return res.status, json.load(res)
    except urllib.error.HTTPError as err:
        return err.code, err.read().decode(errors='replace')


def _get_token(client_id, client_secret):
    if _token['value'] and _token['expires'] > time.time():
        return _token['value']
    credentials = base64.b64encode(f'{client_id}:{client_secret}'.encode()).decode()
    status, data = _request(
        LOGIN_URL,
        {'authorization': f'Basic {credentials}', 'content-type': 'application/x-www-form-urlencoded'},
        urlencode({'deviceId': DEVICE_ID}).encode(),
    )
    if status != 200:
        raise RuntimeError(f'Stingray login failed: {status} {data}')
    # Renewed a minute early so a search never goes out with a token about to expire.
    _token.update(value=data['access_token'], expires=time.time() + int(data.get('expires_in') or 0) - 60)
    return _token['value']


# The text to send for a singer's query, or None when it is too short for the API to accept.
def search_text(query):
    text = ' '.join(query.split())[:MAX_QUERY].strip()
    return text if len(text) >= MIN_QUERY else None


# The API's SearchResults as this app's results. Stingray songs have no video here: the DJ
# plays them in Stingray's own player.
def to_results(data):
    results = []
    for song in (data or {}).get('songs') or []:
        title = (song.get('title') or '').strip()
        if not title or song.get('is_blank'):
            continue
        artist = (song.get('artist_display_name') or '').strip()
        results.append({
            'sourceId': song.get('id'),
            'title': f'{artist} - {title}' if artist else title,
            'channel': LABEL,
            'videoId': None,
            'thumbnail': None,
        })
    return results[:MAX_RESULTS]


def search(query, client_id, client_secret):
    text = search_text(query)
    if not text:
        return []
    params = urlencode({'query': text, 'searchAssetTypes': 'SONG', 'limit': MAX_RESULTS})
    # A rejected token may have been revoked early, so it is worth one fresh login.
    for attempt in range(2):
        token = _get_token(client_id, client_secret)
        status, data = _request(f'{API}/api/v3/search?{params}', {'authorization': f'Bearer {token}'})
        if status == 401 and attempt == 0:
            _token.update(value=None, expires=0)
            continue
        if status != 200:
            raise RuntimeError(f'Stingray search failed: {status} {data}')
        return to_results(data)
