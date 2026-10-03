import logging
import os

from lib import catalog, songs
from lib import stingray as stingray_api
from lib.youtube import search_karaoke

# The steps of the karaoke-song-search state machine. Each one is its own Lambda
# (karaoke-search-karafun, -stingray, -youtube, -finish) built from this same zip, with one of
# the handlers at the bottom. The state machine passes {'searchId': ...} from step to step and
# stops at the first source that answers {'found': True}; if none does it runs `finish`.

logger = logging.getLogger()
logger.setLevel(logging.INFO)


# KaraFun has no search API open to this app, so it is searched through its published song list.
def search_karafun(query):
    return catalog.search('karafun', query)


# Stingray's Karaoke API when the deployment has credentials for it, otherwise a song list.
def search_stingray(query):
    client_id, client_secret = os.environ.get('STINGRAY_CLIENT_ID'), os.environ.get('STINGRAY_CLIENT_SECRET')
    if client_id and client_secret:
        return stingray_api.search(query, client_id, client_secret)
    return catalog.search('stingray', query)


def search_youtube(query):
    return search_karaoke(query, os.environ.get('YOUTUBE_API_KEY'))


SEARCHERS = {'karafun': search_karafun, 'stingray': search_stingray, 'youtube': search_youtube}


# Searches one source and writes the outcome to the search's file. A search that has already
# ended (the state machine retried a step that had finished) is left alone.
def search_source(source, event):
    search = songs.load(event['searchId'])
    if search['status'] == 'searching':
        songs.save(songs.begin(search, source))
        try:
            results = SEARCHERS[source](search['query'])
        except Exception:
            logger.exception('%s search failed', source)
            songs.record_failure(search, source)
        else:
            songs.record(search, source, results)
        songs.save(search)
    return {'searchId': search['searchId'], 'found': search['status'] == 'found'}


def karafun(event, context):
    return search_source('karafun', event)


def stingray(event, context):
    return search_source('stingray', event)


def youtube(event, context):
    return search_source('youtube', event)


def finish(event, context):
    search = songs.load(event['searchId'])
    songs.save(songs.conclude(search))
    return {'searchId': search['searchId'], 'status': search['status']}
