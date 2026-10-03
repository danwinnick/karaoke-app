import json
import sys
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'api'))

from lib.performances import dates_from_keys, performance_key, to_performance  # noqa: E402


class PerformancesTest(unittest.TestCase):
    def test_file_is_named_for_the_date_under_the_singer(self):
        self.assertEqual(performance_key('user_1', '2026-06-05'), 'user_1/2026-06-05.json')

    def test_performance_holds_the_nights_songs_without_queue_internals(self):
        item = {
            'singerId': 'user_1',
            'date': '2026-06-05',
            'requestId': 'req_1',
            'djId': 'dj_1',
            'singerName': 'Sam',
            'tip': {'djId': 'dj_1', 'at': '2026-06-06T04:00:00.000Z'},
            'updatedAt': '2026-06-06T04:00:00.000Z',
            'songs': [
                {'songId': 's1', 'videoId': 'abcdefghijk', 'title': 'Jolene', 'status': 'done', 'djId': 'dj_1', 'order': Decimal('1717.5')},
            ],
        }
        performance = to_performance(item)
        self.assertEqual(performance['date'], '2026-06-05')
        self.assertEqual(performance['tip']['djId'], 'dj_1')
        self.assertEqual(performance['songs'][0]['title'], 'Jolene')
        self.assertEqual(performance['songs'][0]['status'], 'done')
        self.assertNotIn('order', performance['songs'][0])
        # DynamoDB numbers are Decimals, which must not leak into the JSON file.
        json.dumps(performance)

    def test_performance_without_songs_or_tip(self):
        performance = to_performance({'singerId': 'user_1', 'date': '2026-06-05'})
        self.assertEqual(performance['songs'], [])
        self.assertIsNone(performance['tip'])

    def test_dates_are_newest_first_and_only_the_singers_own(self):
        keys = [
            'user_1/2026-06-05.json',
            'user_1/2026-07-01.json',
            'user_1/2026-05-30.json',
            'user_1/notes.txt',
            'user_1/not-a-date.json',
            'user_10/2026-08-01.json',
        ]
        self.assertEqual(dates_from_keys('user_1', keys), ['2026-07-01', '2026-06-05', '2026-05-30'])

    def test_dates_before_a_cursor(self):
        keys = ['user_1/2026-06-05.json', 'user_1/2026-07-01.json', 'user_1/2026-05-30.json']
        self.assertEqual(dates_from_keys('user_1', keys, before='2026-07-01'), ['2026-06-05', '2026-05-30'])
        self.assertEqual(dates_from_keys('user_1', keys, before='2026-05-30'), [])


if __name__ == '__main__':
    unittest.main()
