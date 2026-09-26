import assert from 'node:assert/strict';
import { test } from 'node:test';
import { boostOrder, buildQueue, singerPosition, tipsForDj } from '../api/lib/queue.mjs';

const song = (songId, order, extra = {}) => ({ songId, order, djId: 'dj1', status: 'queued', videoId: 'x', title: songId, ...extra });

function itemsWithSingers(n) {
  return Array.from({ length: n }, (_, i) => ({
    singerId: `s${i + 1}`,
    singerName: `Singer ${i + 1}`,
    djId: 'dj1',
    songs: [song(`song${i + 1}`, (i + 1) * 100)],
  }));
}

test('orders queued songs and ignores other DJs and finished songs', () => {
  const items = [
    { singerId: 'a', singerName: 'A', songs: [song('a1', 300), song('a0', 50, { status: 'done' })] },
    { singerId: 'b', singerName: 'B', songs: [song('b1', 100), song('b2', 200, { djId: 'dj2' })] },
    { singerId: 'c', singerName: 'C', songs: [song('c1', 10, { status: 'playing', startedAt: '2026-01-01T00:00:00Z' })] },
  ];
  const { queue, nowPlaying } = buildQueue(items, 'dj1');
  assert.deepEqual(queue.map((e) => e.songId), ['b1', 'a1']);
  assert.deepEqual(queue.map((e) => e.position), [1, 2]);
  assert.equal(nowPlaying.songId, 'c1');
  assert.equal(queue[1].index, 0);
  assert.equal(singerPosition(queue, 'a'), 2);
  assert.equal(singerPosition(queue, 'zzz'), null);
});

test('boost moves a singer from #15 to exactly #10', () => {
  const items = itemsWithSingers(15);
  const { queue } = buildQueue(items, 'dj1');
  const boost = boostOrder(queue, 's15');
  items[14].songs[0].order = boost.order;
  const after = buildQueue(items, 'dj1').queue;
  assert.equal(singerPosition(after, 's15'), 10);
  assert.equal(singerPosition(after, 's10'), 11);
  assert.equal(singerPosition(after, 's9'), 9);
});

test('boost from #11 lands at #10', () => {
  const items = itemsWithSingers(11);
  const boost = boostOrder(buildQueue(items, 'dj1').queue, 's11');
  items[10].songs[0].order = boost.order;
  assert.equal(singerPosition(buildQueue(items, 'dj1').queue, 's11'), 10);
});

test('no boost when already in the top 10', () => {
  const { queue } = buildQueue(itemsWithSingers(12), 'dj1');
  assert.equal(boostOrder(queue, 's10'), null);
  assert.equal(boostOrder(queue, 's1'), null);
});

test('boost requires a queued song', () => {
  const { queue } = buildQueue(itemsWithSingers(3), 'dj1');
  assert.throws(() => boostOrder(queue, 'nobody'), /need a song/);
});

test('tips are listed for the DJ they were given to', () => {
  const items = [
    { singerId: 'a', singerName: 'A', tip: { djId: 'dj1', at: '2026-01-01T02:00:00Z' } },
    { singerId: 'b', singerName: 'B', tip: { djId: 'dj2', at: '2026-01-01T01:00:00Z' } },
    { singerId: 'c', singerName: 'C', tip: { djId: 'dj1', at: '2026-01-01T01:00:00Z' } },
  ];
  assert.deepEqual(tipsForDj(items, 'dj1').map((t) => t.singerId), ['c', 'a']);
});
