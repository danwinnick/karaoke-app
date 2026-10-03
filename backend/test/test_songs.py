import copy
import logging
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'api'))

from lib.catalog import normalize, parse_catalog, search_songs  # noqa: E402
from lib import songs, stingray  # noqa: E402
from lib.youtube import error_reason  # noqa: E402
import search  # noqa: E402

KARAFUN_CSV = [
    'Id;Title;Artist;Year\n',
    '101;Someone Like You;Adele;2011\n',
    '102;Hello;Adele;2015\n',
    '103;Hello, Dolly!;Louis Armstrong;1964\n',
    "104;Don't Stop Believin';Journey;1981\n",
    '105;Halo;Beyoncé;2008\n',
]


# Stands in for the searches bucket: every save is kept, so tests can see what the singer's page
# would have read at each moment.
class FakeStore:
    def __init__(self, search):
        self.saves = [copy.deepcopy(search)]

    def load(self, search_id):
        return copy.deepcopy(self.saves[-1])

    def save(self, search):
        self.saves.append(copy.deepcopy(search))


def step_statuses(search):
    return {step['source']: step['status'] for step in search['steps']}


class SearchDocumentTest(unittest.TestCase):
    def setUp(self):
        self.search = songs.new_search('abc', 'hello')

    def test_a_new_search_is_searching_with_every_source_pending(self):
        self.assertEqual(self.search, {
            'searchId': 'abc',
            'query': 'hello',
            'status': 'searching',
            'source': None,
            'results': [],
            'steps': [
                {'source': 'karafun', 'status': 'pending'},
                {'source': 'stingray', 'status': 'pending'},
                {'source': 'youtube', 'status': 'pending'},
            ],
        })
        self.assertEqual(songs.search_key('abc'), 'searches/abc.json')

    def test_begin_shows_which_source_is_being_searched(self):
        songs.begin(self.search, 'karafun')
        self.assertEqual(self.search['status'], 'searching')
        self.assertEqual(self.search['source'], 'karafun')
        self.assertEqual(step_statuses(self.search)['karafun'], 'searching')

    def test_results_end_the_search_and_carry_their_source(self):
        songs.record(songs.begin(self.search, 'karafun'), 'karafun', [{'title': 'Adele - Hello'}])
        self.assertEqual(self.search['status'], 'found')
        self.assertEqual(self.search['source'], 'karafun')
        self.assertEqual(self.search['results'], [{'title': 'Adele - Hello', 'source': 'karafun'}])
        self.assertEqual(
            step_statuses(self.search), {'karafun': 'found', 'stingray': 'pending', 'youtube': 'pending'}
        )

    def test_no_results_leaves_the_search_open_for_the_next_source(self):
        songs.record(songs.begin(self.search, 'karafun'), 'karafun', [])
        self.assertEqual(self.search['status'], 'searching')
        self.assertEqual(step_statuses(self.search)['karafun'], 'not_found')

    def test_a_search_no_source_could_answer_is_not_found(self):
        for source in songs.SOURCES:
            songs.record(songs.begin(self.search, source), source, [])
        songs.conclude(self.search)
        self.assertEqual(self.search['status'], 'not_found')
        self.assertIsNone(self.search['source'])
        self.assertEqual(self.search['results'], [])

    def test_a_failed_source_fails_the_search_only_if_no_other_source_has_the_song(self):
        songs.record_failure(songs.begin(self.search, 'karafun'), 'karafun')
        found = copy.deepcopy(self.search)
        songs.record(songs.begin(found, 'stingray'), 'stingray', [{'title': 'Hello'}])
        self.assertEqual(songs.conclude(found)['status'], 'found')

        for source in ('stingray', 'youtube'):
            songs.record(songs.begin(self.search, source), source, [])
        self.assertEqual(songs.conclude(self.search)['status'], 'failed')

    def test_a_source_that_never_reported_back_failed(self):
        songs.record(songs.begin(self.search, 'karafun'), 'karafun', [])
        songs.begin(self.search, 'stingray')
        songs.conclude(self.search)
        self.assertEqual(
            step_statuses(self.search), {'karafun': 'not_found', 'stingray': 'failed', 'youtube': 'pending'}
        )
        self.assertEqual(self.search['status'], 'failed')

    def test_concluding_a_found_search_changes_nothing(self):
        songs.record(songs.begin(self.search, 'youtube'), 'youtube', [{'title': 'Hello'}])
        before = copy.deepcopy(self.search)
        self.assertEqual(songs.conclude(self.search), before)


# The state machine runs these handlers in order, stopping at the first that answers found.
class SearchStepsTest(unittest.TestCase):
    def setUp(self):
        logging.disable(logging.ERROR)
        self.addCleanup(logging.disable, logging.NOTSET)
        self.store = FakeStore(songs.new_search('abc', 'hello'))
        for name in ('load', 'save'):
            patcher = mock.patch.object(songs, name, getattr(self.store, name))
            patcher.start()
            self.addCleanup(patcher.stop)

    def searchers(self, **answers):
        self.calls = []

        def searcher(source, answer):
            def run(query):
                self.calls.append((source, query))
                if isinstance(answer, Exception):
                    raise answer
                return answer

            return run

        patcher = mock.patch.dict(search.SEARCHERS, {s: searcher(s, a) for s, a in answers.items()})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_step_saves_that_it_is_searching_and_then_what_it_found(self):
        self.searchers(karafun=[{'title': 'Adele - Hello'}])
        self.assertEqual(search.karafun({'searchId': 'abc'}, None), {'searchId': 'abc', 'found': True})
        self.assertEqual(self.calls, [('karafun', 'hello')])
        _, searching, found = self.store.saves
        self.assertEqual((searching['status'], searching['source']), ('searching', 'karafun'))
        self.assertEqual(found['status'], 'found')
        self.assertEqual(found['results'], [{'title': 'Adele - Hello', 'source': 'karafun'}])

    def test_a_step_with_no_results_answers_not_found(self):
        self.searchers(stingray=[])
        self.assertEqual(search.stingray({'searchId': 'abc'}, None), {'searchId': 'abc', 'found': False})
        self.assertEqual(self.store.saves[-1]['status'], 'searching')

    def test_a_failing_source_is_recorded_and_the_step_still_answers(self):
        self.searchers(youtube=RuntimeError('quotaExceeded'))
        self.assertEqual(search.youtube({'searchId': 'abc'}, None), {'searchId': 'abc', 'found': False})
        self.assertEqual(step_statuses(self.store.saves[-1])['youtube'], 'failed')

    def test_a_retried_step_leaves_a_finished_search_alone(self):
        self.searchers(karafun=[{'title': 'Adele - Hello'}], stingray=[{'title': 'Hello'}])
        search.karafun({'searchId': 'abc'}, None)
        saves = len(self.store.saves)
        self.assertEqual(search.stingray({'searchId': 'abc'}, None), {'searchId': 'abc', 'found': True})
        self.assertEqual(self.calls, [('karafun', 'hello')])
        self.assertEqual(len(self.store.saves), saves)

    def test_youtube_is_the_last_resort_and_finish_settles_a_search_nobody_answered(self):
        self.searchers(karafun=[], stingray=RuntimeError('down'), youtube=[])
        for step in (search.karafun, search.stingray, search.youtube):
            self.assertFalse(step({'searchId': 'abc'}, None)['found'])
        self.assertEqual(search.finish({'searchId': 'abc'}, None), {'searchId': 'abc', 'status': 'failed'})
        self.assertEqual(
            step_statuses(self.store.saves[-1]), {'karafun': 'not_found', 'stingray': 'failed', 'youtube': 'not_found'}
        )

    def test_stingray_uses_its_api_only_when_the_deployment_has_credentials(self):
        with mock.patch.object(search.catalog, 'search', return_value=['from the list']) as from_list, \
                mock.patch.object(search.stingray_api, 'search', return_value=['from the api']) as from_api:
            with mock.patch.dict(os.environ, clear=True):
                self.assertEqual(search.search_stingray('hello'), ['from the list'])
            with mock.patch.dict(os.environ, {'STINGRAY_CLIENT_ID': 'id', 'STINGRAY_CLIENT_SECRET': 'secret'}):
                self.assertEqual(search.search_stingray('hello'), ['from the api'])
        from_list.assert_called_once_with('stingray', 'hello')
        from_api.assert_called_once_with('hello', 'id', 'secret')


class StingrayTest(unittest.TestCase):
    def test_search_text_fits_the_apis_3_to_40_characters(self):
        self.assertEqual(stingray.search_text('  someone   like you '), 'someone like you')
        self.assertIsNone(stingray.search_text('ab'))
        self.assertEqual(stingray.search_text('la ' * 30), 'la ' * 13 + 'l')

    def test_songs_become_results_with_no_video(self):
        data = {
            'artists': [{'id': 'a1', 'name': 'Adele'}],
            'songs': [
                {'id': 's1', 'title': 'Hello', 'artist_display_name': 'Adele'},
                {'id': 's2', 'title': 'Hello'},
                {'id': 's3', 'title': '', 'is_blank': True},
            ],
        }
        self.assertEqual(stingray.to_results(data), [
            {'sourceId': 's1', 'title': 'Adele - Hello', 'channel': 'Stingray Karaoke', 'videoId': None, 'thumbnail': None},
            {'sourceId': 's2', 'title': 'Hello', 'channel': 'Stingray Karaoke', 'videoId': None, 'thumbnail': None},
        ])
        self.assertEqual(stingray.to_results({}), [])

    def test_limits_results(self):
        data = {'songs': [{'id': str(i), 'title': f'Song {i}'} for i in range(20)]}
        self.assertEqual(len(stingray.to_results(data)), 8)


class YouTubeErrorTest(unittest.TestCase):
    def test_reads_the_reason_from_youtubes_error_body(self):
        body = b'{"error": {"code": 403, "message": "Quota exceeded.", "errors": [{"reason": "quotaExceeded"}]}}'
        self.assertEqual(error_reason(body), 'quotaExceeded')
        self.assertEqual(error_reason(b'{"error": {"code": 400, "message": "API key not valid."}}'), 'API key not valid.')
        self.assertEqual(error_reason(b'<html>Bad gateway</html>'), 'unknown')


class CatalogTest(unittest.TestCase):
    def setUp(self):
        self.songs = parse_catalog(KARAFUN_CSV)

    def titles(self, query):
        return [r['title'] for r in search_songs(self.songs, query, 'KaraFun')]

    def test_normalize_ignores_case_accents_and_punctuation(self):
        self.assertEqual(normalize("Beyoncé - Don't Stop!"), 'beyonce dont stop')

    def test_parses_karafuns_semicolon_export(self):
        self.assertEqual(len(self.songs), 5)
        self.assertEqual(self.songs[0]['id'], '101')
        self.assertEqual(self.songs[0]['title'], 'Adele - Someone Like You')

    def test_parses_comma_separated_lists_without_ids(self):
        songs = parse_catalog(['Artist,Title\n', 'Dolly Parton,"Jolene"\n', 'Nobody,\n'])
        self.assertEqual([(s['id'], s['title']) for s in songs], [(None, 'Dolly Parton - Jolene')])

    def test_a_list_without_title_and_artist_columns_has_no_songs(self):
        self.assertEqual(parse_catalog(['Song,Singer\n', 'Jolene,Dolly Parton\n']), [])
        self.assertEqual(parse_catalog([]), [])

    def test_matches_artist_and_title_words_in_any_order(self):
        self.assertEqual(self.titles('someone like you adele'), ['Adele - Someone Like You'])
        self.assertEqual(self.titles('beyonce halo'), ['Beyoncé - Halo'])
        self.assertEqual(self.titles('dont stop'), ["Journey - Don't Stop Believin'"])

    def test_matches_a_word_the_singer_is_still_typing(self):
        self.assertEqual(self.titles('someone li'), ['Adele - Someone Like You'])
        # ...but only from the start of a word
        self.assertEqual(self.titles('omeone'), [])

    def test_exact_title_matches_come_first(self):
        self.songs.reverse()
        self.assertEqual(self.titles('hello'), ['Adele - Hello', 'Louis Armstrong - Hello, Dolly!'])

    def test_results_carry_the_catalog_id_and_no_video(self):
        self.assertEqual(search_songs(self.songs, 'halo', 'KaraFun'), [
            {'sourceId': '105', 'title': 'Beyoncé - Halo', 'channel': 'KaraFun', 'videoId': None, 'thumbnail': None},
        ])

    def test_limits_results_and_ignores_empty_queries(self):
        songs = parse_catalog(['Title,Artist\n'] + [f'Song {i},Band\n' for i in range(20)])
        self.assertEqual(len(search_songs(songs, 'band', 'KaraFun')), 8)
        self.assertEqual(search_songs(songs, ' - ', 'KaraFun'), [])


if __name__ == '__main__':
    unittest.main()
