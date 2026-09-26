import json
import os
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import boto3

from lib.session import b64url_decode, b64url_encode

_dynamodb = boto3.resource('dynamodb')

DJ_TABLE = os.environ.get('DJ_TABLE')
SINGERS_TABLE = os.environ.get('SINGERS_TABLE')
SONGS_TABLE = os.environ.get('SONGS_TABLE')
SONGS_DJ_INDEX = os.environ.get('SONGS_DJ_INDEX')

_djs = _dynamodb.Table(DJ_TABLE) if DJ_TABLE else None
_singers = _dynamodb.Table(SINGERS_TABLE) if SINGERS_TABLE else None
_songs = _dynamodb.Table(SONGS_TABLE) if SONGS_TABLE else None


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def now_ms():
    return int(time.time() * 1000)


# DynamoDB wants Decimal instead of float and has no use for None, so drop those
# the way the JS DocumentClient's removeUndefinedValues did.
def _clean(value):
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_clean(v) for v in value]
    return value


# ---- DJs -------------------------------------------------------------------


def get_dj(dj_id):
    return _djs.get_item(Key={'djId': dj_id}).get('Item')


def put_dj(dj):
    existing = get_dj(dj['djId'])
    now = now_iso()
    item = _clean({**dj, 'createdAt': (existing or {}).get('createdAt') or now, 'updatedAt': now})
    _djs.put_item(Item=item)
    return item


def list_djs():
    djs = []
    kwargs = {
        'ProjectionExpression': 'djId, #n, address, lat, lng',
        'ExpressionAttributeNames': {'#n': 'name'},
    }
    while True:
        page = _djs.scan(**kwargs)
        djs.extend(page['Items'])
        if 'LastEvaluatedKey' not in page:
            return djs
        kwargs['ExclusiveStartKey'] = page['LastEvaluatedKey']


def get_dj_names(dj_ids):
    ids = list(dict.fromkeys(i for i in dj_ids if i))
    names = {}
    for i in range(0, len(ids), 100):
        request = {
            DJ_TABLE: {
                'Keys': [{'djId': dj_id} for dj_id in ids[i : i + 100]],
                'ProjectionExpression': 'djId, #n',
                'ExpressionAttributeNames': {'#n': 'name'},
            }
        }
        # BatchGetItem may hand back part of the request as UnprocessedKeys.
        while request:
            res = _dynamodb.batch_get_item(RequestItems=request)
            for dj in res['Responses'].get(DJ_TABLE, []):
                names[dj['djId']] = dj.get('name')
            request = res.get('UnprocessedKeys')
    return names


# ---- Singers ---------------------------------------------------------------


def get_singer(singer_id):
    return _singers.get_item(Key={'singerId': singer_id}).get('Item')


def upsert_singer(singer_id, name, email):
    _singers.update_item(
        Key={'singerId': singer_id},
        UpdateExpression='SET #n = :name, email = :email, updatedAt = :now, createdAt = if_not_exists(createdAt, :now)',
        ExpressionAttributeNames={'#n': 'name'},
        ExpressionAttributeValues={':name': name, ':email': email, ':now': now_iso()},
    )


def set_singer_dj(singer_id, dj_id):
    _singers.update_item(
        Key={'singerId': singer_id},
        UpdateExpression='SET currentDjId = :djId, updatedAt = :now',
        ExpressionAttributeValues={':djId': dj_id, ':now': now_iso()},
    )


# ---- Requested songs (one item per singer per night) ------------------------


def get_night(singer_id, date):
    return _songs.get_item(Key={'singerId': singer_id, 'date': date}).get('Item')


def query_dj_night(dj_id, date):
    items = []
    kwargs = {
        'IndexName': SONGS_DJ_INDEX,
        'KeyConditionExpression': 'djId = :djId AND #d = :date',
        'ExpressionAttributeNames': {'#d': 'date'},
        'ExpressionAttributeValues': {':djId': dj_id, ':date': date},
    }
    while True:
        page = _songs.query(**kwargs)
        items.extend(page['Items'])
        if 'LastEvaluatedKey' not in page:
            return items
        kwargs['ExclusiveStartKey'] = page['LastEvaluatedKey']


def append_song(singer_id, singer_name, date, dj_id, song):
    res = _songs.update_item(
        Key={'singerId': singer_id, 'date': date},
        UpdateExpression=(
            'SET songs = list_append(if_not_exists(songs, :empty), :song), djId = :djId, singerName = :name, '
            'requestId = if_not_exists(requestId, :rid), createdAt = if_not_exists(createdAt, :now), updatedAt = :now'
        ),
        ExpressionAttributeValues=_clean({
            ':empty': [],
            ':song': [song],
            ':djId': dj_id,
            ':name': singer_name,
            ':rid': str(uuid.uuid4()),
            ':now': now_iso(),
        }),
        ReturnValues='ALL_NEW',
    )
    return res['Attributes']


# Moves tonight's still-queued songs to a new DJ (at the back of their line).
def move_night_to_dj(item, dj_id):
    now = now_ms()
    songs = [
        {**song, 'djId': dj_id, 'order': now + i} if song.get('status') == 'queued' else song
        for i, song in enumerate(item.get('songs') or [])
    ]
    _songs.update_item(
        Key={'singerId': item['singerId'], 'date': item['date']},
        UpdateExpression='SET songs = :songs, djId = :djId, updatedAt = :now',
        ConditionExpression='updatedAt = :prev',
        ExpressionAttributeValues=_clean({':songs': songs, ':djId': dj_id, ':now': now_iso(), ':prev': item.get('updatedAt')}),
    )


def update_song(singer_id, date, index, song_id, fields):
    names = {}
    values = {':sid': song_id, ':now': now_iso()}
    sets = ['updatedAt = :now']
    for i, (key, value) in enumerate(fields.items()):
        names[f'#f{i}'] = key
        values[f':v{i}'] = value
        sets.append(f'songs[{int(index)}].#f{i} = :v{i}')
    _songs.update_item(
        Key={'singerId': singer_id, 'date': date},
        UpdateExpression=f"SET {', '.join(sets)}",
        ConditionExpression=f'songs[{int(index)}].songId = :sid',
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=_clean(values),
    )


# Records tonight's tip (once per night) and optionally moves a song to a new order.
def record_tip(singer_id, date, dj_id, boost=None):
    values = {':tip': {'djId': dj_id, 'at': now_iso()}, ':now': now_iso()}
    update = 'SET tip = :tip, updatedAt = :now'
    condition = 'attribute_exists(singerId) AND attribute_not_exists(tip)'
    kwargs = {}
    if boost:
        index = int(boost['index'])
        update += f', songs[{index}].#o = :order'
        condition += f' AND songs[{index}].songId = :sid'
        values[':order'] = boost['order']
        values[':sid'] = boost['songId']
        kwargs['ExpressionAttributeNames'] = {'#o': 'order'}
    _songs.update_item(
        Key={'singerId': singer_id, 'date': date},
        UpdateExpression=update,
        ConditionExpression=condition,
        ExpressionAttributeValues=_clean(values),
        **kwargs,
    )


class InvalidCursor(ValueError):
    pass


def history_page(singer_id, cursor=None, limit=20):
    kwargs = {
        'KeyConditionExpression': 'singerId = :s',
        'ExpressionAttributeValues': {':s': singer_id},
        'ScanIndexForward': False,
        'Limit': limit,
    }
    if cursor:
        try:
            start_key = json.loads(b64url_decode(cursor))
        except ValueError:
            start_key = None
        if not isinstance(start_key, dict) or start_key.get('singerId') != singer_id:
            raise InvalidCursor('Invalid history cursor')
        kwargs['ExclusiveStartKey'] = start_key
    res = _songs.query(**kwargs)
    last = res.get('LastEvaluatedKey')
    return {
        'items': res['Items'],
        'cursor': b64url_encode(json.dumps(last).encode()) if last else None,
    }
