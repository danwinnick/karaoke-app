import json
import os
from datetime import datetime, timezone

# Song lookup across the karaoke sources. A search is a JSON file in the searches bucket, named
# searches/<searchId>.json. The API creates it and starts the karaoke-song-search state machine,
# which runs one Lambda per source in the order below; each one records in the file what it is
# doing and what it found, and the first source with any results ends the search. The singer's
# page re-reads the file (through CloudFront) until its status is no longer 'searching'.

BUCKET = os.environ.get('SEARCHES_BUCKET')
STATE_MACHINE_ARN = os.environ.get('SEARCH_STATE_MACHINE_ARN')
SOURCES = ('karafun', 'stingray', 'youtube')

_clients = {}


def _client(service):
    if service not in _clients:
        import boto3  # imported lazily so the helpers below can be unit tested without boto3

        _clients[service] = boto3.client(service)
    return _clients[service]


def search_key(search_id):
    return f'searches/{search_id}.json'


# A search's status is 'searching' until it ends as 'found', 'not_found', or 'failed'. Each
# source's step goes from 'pending' to 'searching' to one of those three; steps after the
# source that had the song stay 'pending' because they are never searched.
def new_search(search_id, query):
    return {
        'searchId': search_id,
        'query': query,
        'status': 'searching',
        'source': None,
        'results': [],
        'steps': [{'source': source, 'status': 'pending'} for source in SOURCES],
    }


def _step(search, source):
    return next(step for step in search['steps'] if step['source'] == source)


# The source is about to be searched.
def begin(search, source):
    _step(search, source)['status'] = 'searching'
    search['source'] = source
    return search


# What the source answered. Any results end the search; none leaves it open for the next source.
def record(search, source, results):
    _step(search, source)['status'] = 'found' if results else 'not_found'
    if results:
        search.update(
            status='found',
            source=source,
            results=[{**result, 'source': source} for result in results],
        )
    return search


# A source that fails counts as a miss: the search goes on to the next source.
def record_failure(search, source):
    _step(search, source)['status'] = 'failed'
    return search


# Every source has had its turn and none had the song. A step still marked 'searching' never
# reported back (its Lambda crashed or timed out), so it failed. The search itself only fails
# if a source did: otherwise the song really isn't anywhere.
def conclude(search):
    if search['status'] != 'searching':
        return search
    for step in search['steps']:
        if step['status'] == 'searching':
            step['status'] = 'failed'
    failed = any(step['status'] == 'failed' for step in search['steps'])
    search.update(status='failed' if failed else 'not_found', source=None)
    return search


def load(search_id):
    return json.loads(_client('s3').get_object(Bucket=BUCKET, Key=search_key(search_id))['Body'].read())


def save(search):
    search['updatedAt'] = datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
    _client('s3').put_object(
        Bucket=BUCKET,
        Key=search_key(search['searchId']),
        Body=json.dumps(search).encode(),
        ContentType='application/json',
        CacheControl='no-store',
    )


def start(search_id):
    _client('stepfunctions').start_execution(
        stateMachineArn=STATE_MACHINE_ARN,
        name=search_id,
        input=json.dumps({'searchId': search_id}),
    )
