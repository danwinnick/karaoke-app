import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'api'))

from lib.queue import boost_order, build_queue, singer_position, tips_for_dj  # noqa: E402


def song(song_id, order, **extra):
    return {'songId': song_id, 'order': order, 'djId': 'dj1', 'status': 'queued', 'videoId': 'x', 'title': song_id, **extra}


def items_with_singers(n):
    return [
        {'singerId': f's{i + 1}', 'singerName': f'Singer {i + 1}', 'djId': 'dj1', 'songs': [song(f'song{i + 1}', (i + 1) * 100)]}
        for i in range(n)
    ]


class QueueTest(unittest.TestCase):
    def test_orders_queued_songs_and_ignores_other_djs_and_finished_songs(self):
        items = [
            {'singerId': 'a', 'singerName': 'A', 'songs': [song('a1', 300), song('a0', 50, status='done')]},
            {'singerId': 'b', 'singerName': 'B', 'songs': [song('b1', 100), song('b2', 200, djId='dj2')]},
            {'singerId': 'c', 'singerName': 'C', 'songs': [song('c1', 10, status='playing', startedAt='2026-01-01T00:00:00Z')]},
        ]
        now_playing, queue = build_queue(items, 'dj1')
        self.assertEqual([e['songId'] for e in queue], ['b1', 'a1'])
        self.assertEqual([e['position'] for e in queue], [1, 2])
        self.assertEqual(now_playing['songId'], 'c1')
        self.assertEqual(queue[1]['index'], 0)
        self.assertEqual(singer_position(queue, 'a'), 2)
        self.assertIsNone(singer_position(queue, 'zzz'))

    def test_boost_moves_a_singer_from_15_to_exactly_10(self):
        items = items_with_singers(15)
        _, queue = build_queue(items, 'dj1')
        boost = boost_order(queue, 's15')
        items[14]['songs'][0]['order'] = boost['order']
        _, after = build_queue(items, 'dj1')
        self.assertEqual(singer_position(after, 's15'), 10)
        self.assertEqual(singer_position(after, 's10'), 11)
        self.assertEqual(singer_position(after, 's9'), 9)

    def test_boost_from_11_lands_at_10(self):
        items = items_with_singers(11)
        boost = boost_order(build_queue(items, 'dj1')[1], 's11')
        items[10]['songs'][0]['order'] = boost['order']
        self.assertEqual(singer_position(build_queue(items, 'dj1')[1], 's11'), 10)

    def test_no_boost_when_already_in_the_top_10(self):
        _, queue = build_queue(items_with_singers(12), 'dj1')
        self.assertIsNone(boost_order(queue, 's10'))
        self.assertIsNone(boost_order(queue, 's1'))

    def test_boost_requires_a_queued_song(self):
        _, queue = build_queue(items_with_singers(3), 'dj1')
        with self.assertRaisesRegex(ValueError, 'need a song'):
            boost_order(queue, 'nobody')

    def test_tips_are_listed_for_the_dj_they_were_given_to(self):
        items = [
            {'singerId': 'a', 'singerName': 'A', 'tip': {'djId': 'dj1', 'at': '2026-01-01T02:00:00Z'}},
            {'singerId': 'b', 'singerName': 'B', 'tip': {'djId': 'dj2', 'at': '2026-01-01T01:00:00Z'}},
            {'singerId': 'c', 'singerName': 'C', 'tip': {'djId': 'dj1', 'at': '2026-01-01T01:00:00Z'}},
        ]
        self.assertEqual([t['singerId'] for t in tips_for_dj(items, 'dj1')], ['c', 'a'])


if __name__ == '__main__':
    unittest.main()
