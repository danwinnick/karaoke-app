import logging
import os
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Attr
from botocore.exceptions import ClientError

from lib import performances

logger = logging.getLogger()

_dynamodb = boto3.resource('dynamodb')

DJ_TABLE = os.environ.get('DJ_TABLE')
SINGERS_TABLE = os.environ.get('SINGERS_TABLE')
SONGS_TABLE = os.environ.get('SONGS_TABLE')
SONGS_DJ_INDEX = os.environ.get('SONGS_DJ_INDEX')
AUTH_TABLE = os.environ.get('AUTH_TABLE')

_djs = _dynamodb.Table(DJ_TABLE) if DJ_TABLE else None
_singers = _dynamodb.Table(SINGERS_TABLE) if SINGERS_TABLE else None
_songs = _dynamodb.Table(SONGS_TABLE) if SONGS_TABLE else None
_auth = _dynamodb.Table(AUTH_TABLE) if AUTH_TABLE else None


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


def _condition_failed(err):
    return err.response.get('Error', {}).get('Code') == 'ConditionalCheckFailedException'


def _scan_first(table, **kwargs):
    while True:
        page = table.scan(**kwargs)
        if page['Items']:
            return page['Items'][0]
        if 'LastEvaluatedKey' not in page:
            return None
        kwargs['ExclusiveStartKey'] = page['LastEvaluatedKey']


# ---- Logins (one per email, locked to a role) + DJ login codes ---------------


def get_login(email):
    return _auth.get_item(Key={'pk': f'user#{email}'}).get('Item')


# Claims the email for a role. If someone else claimed it first, returns their login.
def create_login(email, role, user_id):
    item = {'pk': f'user#{email}', 'email': email, 'role': role, 'userId': user_id, 'createdAt': now_iso()}
    try:
        _auth.put_item(Item=item, ConditionExpression='attribute_not_exists(pk)')
        return item
    except ClientError as err:
        if not _condition_failed(err):
            raise
    return get_login(email)


# Stores a new code unless one was sent too recently. Returns False when rate limited.
def put_login_code(email, code_hash, ttl_seconds, resend_seconds):
    now = int(time.time())
    try:
        _auth.put_item(
            Item={
                'pk': f'code#{email}',
                'codeHash': code_hash,
                'attempts': 0,
                'resendAfter': now + resend_seconds,
                'expiresAt': now + ttl_seconds,
            },
            ConditionExpression='attribute_not_exists(pk) OR resendAfter <= :now',
            ExpressionAttributeValues={':now': now},
        )
        return True
    except ClientError as err:
        if not _condition_failed(err):
            raise
        return False


# Counts a guess against the code before it is checked. Returns None when there is no code.
def attempt_login_code(email):
    try:
        res = _auth.update_item(
            Key={'pk': f'code#{email}'},
            UpdateExpression='ADD attempts :one',
            ConditionExpression='attribute_exists(pk)',
            ExpressionAttributeValues={':one': 1},
            ReturnValues='ALL_NEW',
        )
        return res['Attributes']
    except ClientError as err:
        if not _condition_failed(err):
            raise
        return None


# Deletes the code. Returns False if it was already used by a concurrent request.
def consume_login_code(email):
    res = _auth.delete_item(Key={'pk': f'code#{email}'}, ReturnValues='ALL_OLD')
    return bool(res.get('Attributes'))


# ---- DJs -------------------------------------------------------------------


def get_dj(dj_id):
    return _djs.get_item(Key={'djId': dj_id}).get('Item')


def put_dj(dj):
    existing = get_dj(dj['djId'])
    now = now_iso()
    item = _clean({**dj, 'createdAt': (existing or {}).get('createdAt') or now, 'updatedAt': now})
    _djs.put_item(Item=item)
    return item


# What singers see. `name` is the DJ's real name, except for DJs from before nicknames,
# whose only name was the one they chose to show singers.
def dj_public_name(dj):
    return dj.get('nickname') or dj.get('name')


def list_djs():
    djs = []
    kwargs = {
        'ProjectionExpression': 'djId, #n, nickname, address, lat, lng',
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
                'ProjectionExpression': 'djId, #n, nickname',
                'ExpressionAttributeNames': {'#n': 'name'},
            }
        }
        # BatchGetItem may hand back part of the request as UnprocessedKeys.
        while request:
            res = _dynamodb.batch_get_item(RequestItems=request)
            for dj in res['Responses'].get(DJ_TABLE, []):
                names[dj['djId']] = dj_public_name(dj)
            request = res.get('UnprocessedKeys')
    return names


# DJs who signed up before email-code login are keyed by their old WorkOS user id.
def find_dj_by_email(email):
    return _scan_first(_djs, FilterExpression=Attr('email').eq(email), ProjectionExpression='djId')


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


def find_singer_by_email(email):
    return _scan_first(_singers, FilterExpression=Attr('email').eq(email), ProjectionExpression='singerId')


def set_singer_dj(singer_id, dj_id):
    _singers.update_item(
        Key={'singerId': singer_id},
        UpdateExpression='SET currentDjId = :djId, updatedAt = :now',
        ExpressionAttributeValues={':djId': dj_id, ':now': now_iso()},
    )


def mark_performances_synced(singer_id):
    _singers.update_item(
        Key={'singerId': singer_id},
        UpdateExpression='SET performancesSyncedAt = :now',
        ExpressionAttributeValues={':now': now_iso()},
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


# Every change to a night goes through here, so the singer's performance file in S3 is
# rewritten to match.
def _update_night(singer_id, date, **kwargs):
    item = _songs.update_item(Key={'singerId': singer_id, 'date': date}, ReturnValues='ALL_NEW', **kwargs)['Attributes']
    try:
        performances.save(item)
    except Exception:
        # The queue change itself went through; the file catches up on the night's next change.
        logger.exception('Could not save performance')
    return item


def append_song(singer_id, singer_name, date, dj_id, song):
    return _update_night(
        singer_id,
        date,
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
    )


# Moves tonight's still-queued songs to a new DJ (at the back of their line).
def move_night_to_dj(item, dj_id):
    now = now_ms()
    songs = [
        {**song, 'djId': dj_id, 'order': now + i} if song.get('status') == 'queued' else song
        for i, song in enumerate(item.get('songs') or [])
    ]
    _update_night(
        item['singerId'],
        item['date'],
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
    _update_night(
        singer_id,
        date,
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
    _update_night(
        singer_id,
        date,
        UpdateExpression=update,
        ConditionExpression=condition,
        ExpressionAttributeValues=_clean(values),
        **kwargs,
    )


# Every night a singer has requested songs on.
def list_nights(singer_id):
    items = []
    kwargs = {'KeyConditionExpression': 'singerId = :s', 'ExpressionAttributeValues': {':s': singer_id}}
    while True:
        page = _songs.query(**kwargs)
        items.extend(page['Items'])
        if 'LastEvaluatedKey' not in page:
            return items
        kwargs['ExclusiveStartKey'] = page['LastEvaluatedKey']
