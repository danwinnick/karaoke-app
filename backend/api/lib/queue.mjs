// Pure queue logic over the karaoke-requested-songs items for one DJ and one night.

export const TIP_POSITION = 10;

export function buildQueue(items, djId) {
  const queue = [];
  let nowPlaying = null;

  for (const item of items) {
    (item.songs ?? []).forEach((song, index) => {
      if (song.djId !== djId) return;
      const entry = {
        singerId: item.singerId,
        singerName: item.singerName,
        songId: song.songId,
        index,
        videoId: song.videoId,
        title: song.title,
        thumbnail: song.thumbnail,
        order: song.order,
        status: song.status,
        requestedAt: song.requestedAt,
        startedAt: song.startedAt,
        tipped: item.tip?.djId === djId,
      };
      if (song.status === 'queued') queue.push(entry);
      else if (song.status === 'playing' && (!nowPlaying || entry.startedAt > nowPlaying.startedAt)) {
        nowPlaying = entry;
      }
    });
  }

  queue.sort((a, b) => a.order - b.order);
  queue.forEach((entry, i) => {
    entry.position = i + 1;
  });
  return { nowPlaying, queue };
}

export function singerPosition(queue, singerId) {
  return queue.find((e) => e.singerId === singerId)?.position ?? null;
}

// Returns the new order value that places the singer's next song at #10, or null if
// they are already in the top 10. Throws if the singer has nothing queued.
export function boostOrder(queue, singerId) {
  const idx = queue.findIndex((e) => e.singerId === singerId);
  if (idx < 0) throw new Error('You need a song in the queue before you can tip');
  if (idx < TIP_POSITION) return null;
  const before = queue[TIP_POSITION - 2].order;
  const after = queue[TIP_POSITION - 1].order;
  return { entry: queue[idx], order: (before + after) / 2 };
}

export function tipsForDj(items, djId) {
  return items
    .filter((item) => item.tip?.djId === djId)
    .map((item) => ({ singerId: item.singerId, singerName: item.singerName, at: item.tip.at }))
    .sort((a, b) => a.at.localeCompare(b.at));
}
