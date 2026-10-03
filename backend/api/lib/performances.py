import json
import os
import re
from concurrent.futures import ThreadPoolExecutor

# A performance is one singer's night: the set of songs they picked on one date. Each one is a
# JSON file in the performances bucket, named <singerId>/<date>.json.

BUCKET = os.environ.get('PERFORMANCES_BUCKET')
DATE_RE = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}')
SONG_FIELDS = (
    'songId', 'source', 'sourceId', 'videoId', 'title', 'thumbnail', 'status', 'djId',
    'requestedAt', 'startedAt', 'finishedAt',
)

_s3 = None


def _client():
    global _s3
    if _s3 is None:
        import boto3  # imported lazily so the helpers below can be unit tested without boto3

        _s3 = boto3.client('s3')
    return _s3


def performance_key(singer_id, date):
    return f'{singer_id}/{date}.json'


# The file's contents, built from the singer's karaoke-requested-songs item for that night.
def to_performance(item):
    return {
        'singerId': item['singerId'],
        'date': item['date'],
        'requestId': item.get('requestId'),
        'djId': item.get('djId'),
        'singerName': item.get('singerName'),
        'tip': item.get('tip'),
        'songs': [{field: song.get(field) for field in SONG_FIELDS} for song in item.get('songs') or []],
        'updatedAt': item.get('updatedAt'),
    }


# The dates a singer has files for, newest first, optionally only those earlier than `before`.
def dates_from_keys(singer_id, keys, before=None):
    prefix = f'{singer_id}/'
    dates = []
    for key in keys:
        name = key[len(prefix) :]
        if key.startswith(prefix) and name.endswith('.json') and DATE_RE.fullmatch(name[:-5]):
            dates.append(name[:-5])
    return sorted((d for d in dates if not before or d < before), reverse=True)


def save(item):
    performance = to_performance(item)
    _client().put_object(
        Bucket=BUCKET,
        Key=performance_key(performance['singerId'], performance['date']),
        Body=json.dumps(performance).encode(),
        ContentType='application/json',
    )


def _load(key):
    return json.loads(_client().get_object(Bucket=BUCKET, Key=key)['Body'].read())


# One page of a singer's performances, newest first. The cursor is the last date on the page.
def page(singer_id, before=None, limit=10):
    keys = []
    for listing in _client().get_paginator('list_objects_v2').paginate(Bucket=BUCKET, Prefix=f'{singer_id}/'):
        keys.extend(obj['Key'] for obj in listing.get('Contents', []))
    dates = dates_from_keys(singer_id, keys, before)
    chosen = dates[:limit]
    with ThreadPoolExecutor(max_workers=limit) as pool:
        performances = list(pool.map(lambda date: _load(performance_key(singer_id, date)), chosen))
    return {'performances': performances, 'cursor': chosen[-1] if len(dates) > limit else None}
