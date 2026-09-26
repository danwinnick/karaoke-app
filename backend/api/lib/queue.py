# Pure queue logic over the karaoke-requested-songs items for one DJ and one night.

TIP_POSITION = 10


def build_queue(items, dj_id):
    queue = []
    now_playing = None

    for item in items:
        for index, song in enumerate(item.get('songs') or []):
            if song.get('djId') != dj_id:
                continue
            entry = {
                'singerId': item['singerId'],
                'singerName': item.get('singerName'),
                'songId': song.get('songId'),
                'index': index,
                'videoId': song.get('videoId'),
                'title': song.get('title'),
                'thumbnail': song.get('thumbnail'),
                'order': song.get('order'),
                'status': song.get('status'),
                'requestedAt': song.get('requestedAt'),
                'startedAt': song.get('startedAt'),
                'tipped': (item.get('tip') or {}).get('djId') == dj_id,
            }
            if song.get('status') == 'queued':
                queue.append(entry)
            elif song.get('status') == 'playing' and (
                not now_playing
                or (entry['startedAt'] and now_playing['startedAt'] and entry['startedAt'] > now_playing['startedAt'])
            ):
                now_playing = entry

    queue.sort(key=lambda e: e['order'])
    for i, entry in enumerate(queue):
        entry['position'] = i + 1
    return now_playing, queue


def singer_position(queue, singer_id):
    return next((e['position'] for e in queue if e['singerId'] == singer_id), None)


# Returns the new order value that places the singer's next song at #10, or None if
# they are already in the top 10. Raises ValueError if the singer has nothing queued.
def boost_order(queue, singer_id):
    idx = next((i for i, e in enumerate(queue) if e['singerId'] == singer_id), -1)
    if idx < 0:
        raise ValueError('You need a song in the queue before you can tip')
    if idx < TIP_POSITION:
        return None
    before = queue[TIP_POSITION - 2]['order']
    after = queue[TIP_POSITION - 1]['order']
    return {'entry': queue[idx], 'order': (before + after) / 2}


def tips_for_dj(items, dj_id):
    tips = [
        {'singerId': item['singerId'], 'singerName': item.get('singerName'), 'at': item['tip']['at']}
        for item in items
        if (item.get('tip') or {}).get('djId') == dj_id
    ]
    return sorted(tips, key=lambda t: t['at'])
