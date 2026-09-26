import json
import re
import time
import urllib.error
import urllib.request
from urllib.parse import urlencode

_cache = {}
CACHE_TTL_SECONDS = 60 * 60

ENTITIES = {'amp': '&', 'lt': '<', 'gt': '>', 'quot': '"', 'apos': "'", '#39': "'", '#34': '"'}


def _entity(m):
    name = m.group(1)
    if name in ENTITIES:
        return ENTITIES[name]
    try:
        if name.startswith('#x'):
            return chr(int(name[2:], 16))
        if name.startswith('#'):
            return chr(int(name[1:], 10))
    except ValueError:
        pass
    return m.group(0)


def decode_entities(s):
    return re.sub(r'&(#?\w+);', _entity, s, flags=re.ASCII)


NOISE = re.compile(
    r'\b(karaoke|instrumental|lyrics?|version|backing track|sing along|official|hd|hq|4k|no vocals?|with vocals?|key of \w+|in the style of)\b',
    re.IGNORECASE | re.ASCII,
)


# "Adele - Someone Like You (Karaoke Version) | Sing King" -> "Adele - Someone Like You"
def clean_title(raw):
    title = re.split(r'\s[|•]\s', decode_entities(raw))[0]
    title = re.sub(r'[(\[{【][^)\]}】]*[)\]}】]', lambda m: '' if NOISE.search(m.group(0)) else m.group(0), title)
    title = re.sub(r'\b(karaoke version|karaoke|instrumental version|with lyrics)\b', '', title, flags=re.IGNORECASE | re.ASCII)
    title = re.sub(r'\s{2,}', ' ', title)
    title = re.sub(r'[\s\-–—:]+$', '', title)
    title = re.sub(r'^[\s\-–—:]+', '', title)
    return title.strip() or decode_entities(raw)


def search_karaoke(query, api_key):
    key = query.lower()
    hit = _cache.get(key)
    if hit and hit['expires'] > time.time():
        return hit['results']

    params = urlencode({
        'part': 'snippet',
        'type': 'video',
        'videoEmbeddable': 'true',
        'maxResults': '8',
        'q': f'{query} karaoke',
        'key': api_key,
    })
    req = urllib.request.Request(f'https://www.googleapis.com/youtube/v3/search?{params}')
    try:
        with urllib.request.urlopen(req, timeout=10) as res:
            data = json.load(res)
    except urllib.error.HTTPError as err:
        raise RuntimeError(f'YouTube search failed: {err.code} {err.read().decode(errors="replace")}') from None

    results = []
    for item in data.get('items') or []:
        snippet = item['snippet']
        thumbs = snippet.get('thumbnails') or {}
        results.append({
            'videoId': item['id']['videoId'],
            'title': clean_title(snippet['title']),
            'rawTitle': decode_entities(snippet['title']),
            'channel': decode_entities(snippet.get('channelTitle') or ''),
            'thumbnail': (thumbs.get('medium') or {}).get('url') or (thumbs.get('default') or {}).get('url'),
        })

    if len(_cache) > 500:
        _cache.clear()
    _cache[key] = {'results': results, 'expires': time.time() + CACHE_TTL_SECONDS}
    return results
