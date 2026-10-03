import csv
import re
import unicodedata
from pathlib import Path

# A source without a search API this app can call is searched through its song list: a CSV
# file at catalogs/<source>.csv with Title and Artist columns (and optionally Id), separated by
# commas or semicolons. KaraFun only opens its API to partners but publishes its catalog as a
# CSV (Id;Title;Artist;Year;Duo;Explicit;Date Added;Styles;Languages), which works as is.
# Stingray is searched this way only while the deployment has no credentials for its API (see
# stingray.py). A source with no file has no songs.

CATALOG_DIR = Path(__file__).resolve().parent.parent / 'catalogs'
LABELS = {'karafun': 'KaraFun', 'stingray': 'Stingray Karaoke'}
MAX_RESULTS = 8

_catalogs = {}


# "Beyoncé - Don't Stop!" -> "beyonce dont stop"
def normalize(text):
    text = unicodedata.normalize('NFKD', text)
    text = ''.join(c for c in text if not unicodedata.combining(c)).casefold()
    text = re.sub(r"['’`]", '', text)
    return ' '.join(re.sub(r'[^\w]+', ' ', text).split())


def parse_catalog(lines):
    lines = iter(lines)
    header = next(lines, '')
    delimiter = ';' if header.count(';') > header.count(',') else ','
    columns = [normalize(name) for name in next(csv.reader([header], delimiter=delimiter), [])]
    if 'title' not in columns or 'artist' not in columns:
        return []
    title_at, artist_at = columns.index('title'), columns.index('artist')
    id_at = columns.index('id') if 'id' in columns else None

    songs = []
    for row in csv.reader(lines, delimiter=delimiter):
        if len(row) <= max(title_at, artist_at):
            continue
        title, artist = row[title_at].strip(), row[artist_at].strip()
        if not title:
            continue
        songs.append({
            'id': row[id_at].strip() if id_at is not None and len(row) > id_at else None,
            'title': f'{artist} - {title}' if artist else title,
            'titleKey': normalize(title),
            # Leading space so a query word can be matched against the start of any word.
            'words': f' {normalize(artist)} {normalize(title)}',
        })
    return songs


def _load(source):
    if source not in _catalogs:
        try:
            with open(CATALOG_DIR / f'{source}.csv', newline='', encoding='utf-8-sig') as f:
                _catalogs[source] = parse_catalog(f)
        except FileNotFoundError:
            _catalogs[source] = []
    return _catalogs[source]


# Songs where every word of the query starts a word of the artist or title, best matches first.
def search_songs(songs, query, label, limit=MAX_RESULTS):
    key = normalize(query)
    words = key.split()
    if not words:
        return []
    matches = [song for song in songs if all(f' {word}' in song['words'] for word in words)]
    matches.sort(key=lambda song: 0 if song['titleKey'] == key else 1 if song['titleKey'].startswith(key) else 2)
    return [
        {'sourceId': song['id'], 'title': song['title'], 'channel': label, 'videoId': None, 'thumbnail': None}
        for song in matches[:limit]
    ]


def search(source, query):
    return search_songs(_load(source), query, LABELS[source])
